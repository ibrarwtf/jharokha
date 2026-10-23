"""Learning an app from what is on screen, so a new app (or a new view of a known one) needs
no code: scan the screen, propose a definition in the `format = 1` schema of core.apps with
its calibration, let a person or an agent name things, verify on more frames, then save.

    s = scan(frame, lines)                       # panes, roles, readers, anchors
    p = propose(s, "tickets", "queue")           # an App, its calibration, TOML text
    v = verify(p.app, "queue", [scan(f, ls) for f, ls in more])
    p = refine(p, v)                             # drop anchors that moved, set ready
    text = save(old_apps_toml, p)                # what apps.toml should hold now

Anchors are the text an app draws itself: short labels in its bars, rails and headers.
Content (sentences, names in a list, times, dates, numbers, IDs, URLs) changes from one
screen to the next and would make an app unrecognisable tomorrow, so a label must look like
one, sit in a bar, a narrow pane or alone at a pane's edge, and not repeat text from a list
or a page. One screen can still be fooled by a name in a header; a second screen (another
chat, a scroll) settles it: propose keeps only anchors every scan shows in the same place.

Everything here is pure: no capture, no OCR, no file is written."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from jharokha.core import apps
from jharokha.core.apps import RGB, App, Calibration, Cue, Grid, Region, Variant, View
from jharokha.core.elements import elements as find_elements
from jharokha.core.identify import Identity, identify
from jharokha.core.layout import Fraction, to_fraction
from jharokha.core.models import Box, Frame, Line, iou, union
from jharokha.core.panes import background as screen_background
from jharokha.core.panes import panes as find_panes
from jharokha.core.text import fold, grid_metrics, wrap_untrusted

ROLES = ("toolbar", "nav", "header", "footer", "sidebar", "list", "table", "content")
SHORT = 0.15  # of the height: a pane this short is a bar (title, ribbon, tabs, toolbar)
# A browser's tab strip, toolbar and favourites run together into one pane across the top.
TOP = 0.2
NARROW = 0.2  # of the width: a pane this narrow is a rail or a sidebar
MAX_ANCHORS = 10
MAX_LABEL = 32  # characters
MAX_WORDS = 4
ISOLATED = 2.5  # line heights of blank space that set a line apart at a pane's edge
STABLE = 0.05  # fraction of the client area an anchor may move between scans
MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Pane:
    """One part of the screen as the scan sees it: a pane from the pixels, or a table's
    cells merged into one."""

    index: int
    box: Box
    role: str
    reader: str
    lines: tuple[Line, ...]
    elements: int  # text-less things (icons, buttons) inside
    background: RGB
    members: tuple[Box, ...] = ()  # the panes merged into it, top-left first


@dataclass(frozen=True)
class Anchor:
    text: str
    box: Box
    pane: int
    score: float


@dataclass(frozen=True)
class Scan:
    width: int
    height: int
    panes: tuple[Pane, ...]  # what regions can be made of, in reading order
    raw: tuple[Box, ...]  # every pane core.panes found, for placing regions as apps does
    lines: tuple[Line, ...]  # everything OCR read, the session's taskbar too
    elements: tuple[Box, ...]
    background: RGB
    anchors: tuple[Anchor, ...]  # best first
    focus: int | None  # the pane to read after input: the largest content pane
    ready: str | None  # an anchor alone at the focus pane's foot (a compose box)
    prompt: str | None  # a shell prompt seen at the start of a grid's rows, as a regex
    session: tuple[Box, ...] = ()  # the session's own panes (taskbar), left out


# Scanning


def scan(frame: Frame, lines: Sequence[Line], session: Iterable[Region] | None = None) -> Scan:
    """What the screen is made of. session: the session's own regions to leave out
    (default the built-in taskbar)."""
    w, h = frame.width, frame.height
    lines = list(lines)
    raw = find_panes(frame)
    if session is None:
        session = [r for d in apps.builtin() for r in d.session.values()]
    sess: list[Box] = []
    for reg in session:
        placed = apps.place(reg, raw, w, h)
        if placed:
            sess.append(placed[0])
    kept = [p for p in raw if not any(iou(p, s) >= apps.PANE_FIT for s in sess)]
    bottom = min((s[1] for s in sess if s[1] > h / 2), default=h)
    pixels = np.frombuffer(frame.bgra, np.uint8).reshape(h, w, 4)
    found = find_elements(frame, lines, raw)

    units = _merge_tables(kept)
    infos: list[Pane] = []
    for members in units:
        box = union(members)
        inside = tuple(_inside(lines, box))
        n_el = sum(1 for e in found if _centre_in(e, box))
        if not inside and not n_el:
            continue
        bg, share = _pane_background(pixels, box)
        if len(members) > 1:
            reader = "table"
        else:
            reader = _reader(list(inside), share)
        infos.append(Pane(len(infos), box, "", reader, inside, n_el, bg, tuple(members)))

    focus = _focus(infos, w, h)
    infos = _roles(infos, focus, w, h, bottom)
    anchors = _anchors(infos, focus, w, h)
    ready = None
    if focus is not None:
        foot = _isolated(list(infos[focus].lines))[1]
        ready = next((a.text for a in anchors if foot is not None and a.box == foot.box), None)
    prompt = next((p for i in infos if i.reader == "grid" and (p := _prompt(list(i.lines)))), None)
    return Scan(
        w,
        h,
        tuple(infos),
        tuple(raw),
        tuple(lines),
        tuple(found),
        screen_background(frame),
        tuple(anchors),
        focus,
        ready,
        prompt,
        tuple(sess),
    )


def describe(s: Scan) -> str:
    """The scan as an agent reads it before naming things: each pane with its index, role,
    reader, box and a sample of its text, then the anchors. Screen text is untrusted and is
    wrapped as such."""
    out = [f"{s.width}x{s.height}, background {list(s.background)}"]
    for p in s.panes:
        mark = " focus" if p.index == s.focus else ""
        sample = "; ".join(ln.text for ln in p.lines[:3])
        out.append(
            f"P{p.index} {p.role} reader={p.reader}{mark} box={list(p.box)} "
            f"lines={len(p.lines)} icons={p.elements}" + (f" | {sample}" if sample else "")
        )
    out.append("anchors: " + " | ".join(f"{a.text} (P{a.pane})" for a in s.anchors))
    if s.ready:
        out.append(f"ready: {s.ready}")
    return wrap_untrusted("\n".join(out))


def _centre_in(b: Box, box: Box) -> bool:
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    return box[0] <= cx < box[0] + box[2] and box[1] <= cy < box[1] + box[3]


def _inside(lines: Iterable[Line], box: Box) -> list[Line]:
    return sorted(
        (ln for ln in lines if _centre_in(ln.box, box)), key=lambda ln: (ln.box[1], ln.box[0])
    )


def _pane_background(pixels: np.ndarray, box: Box) -> tuple[RGB, float]:
    """The pane's commonest colour and the share of the pane painted with it."""
    x, y, w, h = box
    s = pixels[y : y + h : 3, x : x + w : 3, :3].reshape(-1, 3).astype(np.int32)
    if not len(s):
        return (0, 0, 0), 0.0
    packed = ((s[:, 2] >> 3) << 10) | ((s[:, 1] >> 3) << 5) | (s[:, 0] >> 3)
    values, counts = np.unique(packed, return_counts=True)
    top = int(counts.argmax())
    sel = packed == values[top]
    r, g, b = (int(np.median(s[sel, i])) for i in (2, 1, 0))
    return (r, g, b), float(counts[top]) / len(packed)


def _merge_tables(boxes: list[Box]) -> list[list[Box]]:
    """Panes, with a table's cells (a calendar's days and hours: many panes of one size in
    columns) merged into one unit with the strips between them and the row header beside."""
    sizes: dict[tuple[int, int], list[Box]] = {}
    for b in boxes:
        key = (round(b[2] / 6), round(b[3] / 6))
        sizes.setdefault(key, []).append(b)
    cells = max(sizes.values(), key=len, default=[])
    if len(cells) < 6 or len({c[0] for c in cells}) < 2:
        return [[b] for b in boxes]
    block = union(cells)
    cols = {(c[0], c[2]) for c in cells}
    # Everything in the cells' columns inside their span, then a pane that runs the block's
    # full height (hours) or width (days) right beside it.
    group = [
        b
        for b in boxes
        if any(abs(b[0] - x) <= 3 and abs(b[2] - cw) <= 3 for x, cw in cols)
        and block[1] - 50 <= b[1]
        and b[1] + b[3] <= block[1] + block[3] + 50
    ]
    block = union(group)
    for b in boxes:
        if b in group:
            continue
        beside = abs(b[0] + b[2] - block[0]) <= 3 or abs(block[0] + block[2] - b[0]) <= 3
        same_h = abs(b[1] - block[1]) <= 4 and abs(b[3] - block[3]) <= 8
        if beside and same_h:
            group.append(b)
    group.sort(key=lambda b: (b[1], b[0]))
    rest = [[b] for b in boxes if b not in group]
    out = rest + [sorted(group, key=lambda b: (b[0] + b[1] * 4, b[0]))]
    return sorted(out, key=lambda m: (union(m)[1], union(m)[0]))


# Readers


def _reader(lines: list[Line], uniform: float) -> str:
    if _is_grid(lines, uniform):
        return "grid"
    if _columns(lines) >= 3:
        return "table"
    if _list_items(lines):
        return "list"
    return "text"


def _words(lines: list[Line]) -> list[tuple[str, Box]]:
    return [(w.text, w.box) for ln in lines for w in (ln.words or ())] or [
        (ln.text, ln.box) for ln in lines
    ]


# Rough advance widths of a proportional sans-serif, in ems: what a word's width follows
# when the font is not monospace.
_NARROW_CH = set("ijlI!.,:;'|()[]1")
_SEMI_CH = set('frtJ-"')
_WIDE_CH = set("mwMW@")


def _advance(c: str) -> float:
    if c in _NARROW_CH:
        return 0.28
    if c in _SEMI_CH:
        return 0.4
    if c in _WIDE_CH:
        return 0.9
    if c.isupper():
        return 0.68
    return 0.55


def _cv(a: np.ndarray) -> float:
    return float(a.std() / a.mean()) if len(a) and a.mean() else 1.0


def _monospace(lines: list[Line]) -> bool:
    """Whether word widths follow their character counts (monospace) better than the
    letters in them (a proportional font: "Inbox" is narrower than "Mmmmm")."""
    words = [(t, b[2]) for t, b in _words(lines) if len(t) >= 3]
    # A handful of labels (a rail's, folders) can fit either model by chance.
    if len(words) < 12:
        return False
    per_char = np.array([w / len(t) for t, w in words])
    per_em = np.array([w / sum(_advance(c) for c in t) for t, w in words])
    return _cv(per_char) <= 0.15 and _cv(per_char) < _cv(per_em)


def _is_grid(lines: list[Line], uniform: float) -> bool:
    """Monospace rows on one flat background at a constant pitch: a terminal."""
    if len(lines) < 4 or uniform < 0.8 or not _monospace(lines):
        return False
    tops = sorted({ln.box[1] + ln.box[3] / 2 for ln in lines})
    gaps = [b - a for a, b in zip(tops, tops[1:], strict=False) if b - a > 4]
    if len(gaps) < 3:
        return False
    pitch = min(gaps)
    on = sum(abs(g / pitch - round(g / pitch)) <= 0.2 for g in gaps)
    return on >= 0.8 * len(gaps)


def _rows(lines: list[Line]) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: ln.box[1] + ln.box[3] / 2):
        c = ln.box[1] + ln.box[3] / 2
        if rows:
            last = rows[-1][0]
            if abs(last.box[1] + last.box[3] / 2 - c) <= max(last.box[3], ln.box[3]) / 2:
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return rows


def _columns(lines: list[Line]) -> int:
    """How many columns line up across at least four rows that each have three or more."""
    rows = [r for r in _rows(lines) if len(r) >= 3]
    if len(rows) < 4:
        return 0
    starts: list[list[int]] = []
    for r in rows:
        for ln in r:
            for col in starts:
                if abs(col[0] - ln.box[0]) <= 4:
                    col.append(ln.box[0])
                    break
            else:
                starts.append([ln.box[0]])
    return sum(len(c) >= 4 for c in starts)


def _list_items(lines: list[Line]) -> tuple[float, int, int] | None:
    """Items of two or more lines repeating at one pitch from one left edge (a chat list, a
    message list): (pitch, lines an item, items). One-line items (a rail, folders) are not a
    list: they are labels."""
    if len(lines) < 6:
        return None
    lefts: dict[int, list[Line]] = {}
    for ln in lines:
        key = next((k for k in lefts if abs(k - ln.box[0]) <= 4), ln.box[0])
        lefts.setdefault(key, []).append(ln)
    col = max(lefts.values(), key=len)
    tops = sorted(ln.box[1] for ln in col)
    h = sorted(ln.box[3] for ln in col)[len(col) // 2]
    best: tuple[int, float] | None = None
    for i, a in enumerate(tops):
        for b in tops[i + 1 :]:
            d = b - a
            if d < 2.5 * h:
                continue
            n = sum(any(abs(t2 - t1 - d) <= 4 for t2 in tops) for t1 in tops)
            if best is None or n > best[0] or (n == best[0] and d < best[1]):
                best = (n, d)
    if best is None or best[0] < 3:
        return None
    n, pitch = best
    per = sum(1 for t in tops if tops[0] <= t < tops[0] + pitch - 2)
    if per < 2:
        return None
    return pitch, per, n + 1


def _prompt(lines: list[Line]) -> str | None:
    """A shell prompt: the first word most rows of a grid start with, when it is not text."""
    firsts: dict[str, int] = {}
    for ln in lines:
        word = ln.text.split()[0] if ln.text.split() else ""
        if re.fullmatch(r"[A-Za-z]{1,4}", word):
            firsts[word] = firsts.get(word, 0) + 1
    if not firsts:
        return None
    word, n = max(firsts.items(), key=lambda kv: kv[1])
    return f"^{re.escape(word)} " if n >= 2 else None


# Roles


def _focus(infos: list[Pane], w: int, h: int) -> int | None:
    """The largest pane that is neither a bar nor a rail, as the built-in apps choose the
    thread, the reading pane, the page or the terminal's body."""
    body = [p for p in infos if p.box[3] > SHORT * h and p.box[2] > NARROW * w]
    pool = body or infos
    if not pool:
        return None
    best = max(pool, key=lambda p: (p.box[2] * p.box[3], -p.box[0]))
    return best.index


def _roles(infos: list[Pane], focus: int | None, w: int, h: int, bottom: int) -> list[Pane]:
    out = []
    lefts = [p.box[0] for p in infos if p.box[3] > SHORT * h]
    leftmost = min(lefts) if lefts else 0
    for p in infos:
        x, y, pw, ph = p.box
        reader = p.reader
        if p.index == focus:
            # The focus is read in order: a list there is a thread of messages, and columns
            # of text are a table on a page of prose. Cells drawn as panes stay a table.
            keep = reader == "grid" or len(p.members) > 1
            role, reader = "content", reader if keep else "text"
        elif ph <= SHORT * h or (y <= 0.02 * h and pw >= 0.9 * w and ph <= TOP * h):
            below = [
                q
                for q in infos
                if q is not p and abs(q.box[1] - (y + ph)) <= 4 and _span(p.box, q.box) >= 0.8
            ]
            if y <= 0.02 * h:
                role = "toolbar"
            elif below:
                role = "header"
            elif y + ph >= bottom - 4 and y > h / 2:
                role = "footer"
            else:
                role = "toolbar"
        elif pw <= NARROW * w:
            role = "nav" if x == leftmost and ph >= 0.5 * (bottom - y) else "sidebar"
        elif reader in ("list", "table"):
            role = reader
        else:
            role = "content"
        out.append(replace(p, role=role, reader=reader))
    return out


def _span(a: Box, b: Box) -> float:
    """How much of each other's width two boxes share, the lesser of the two."""
    lo, hi = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
    shared = max(0, hi - lo)
    return min(shared / max(1, a[2]), shared / max(1, b[2]))


# Anchors


_NOT_LABEL = re.compile(r"[\d/\\@=_|.,:;!?\"'<>{}\[\]#$%&*~^`]")


def is_label(text: str) -> bool:
    """Whether text looks like UI an app draws itself rather than content: short, no
    digits (times, dates, counts, IDs), no URL or path punctuation, not a sentence, not a
    window title ("Queue - Project")."""
    t = text.strip()
    words = t.split()
    if not (2 <= len(t) <= MAX_LABEL) or not words or len(words) > MAX_WORDS:
        return False
    if sum(c.isalpha() for c in t) < 3 or _NOT_LABEL.search(t.replace("(Ctrl+", "(")):
        return False
    if " - " in t or t.endswith("-") or t.startswith("-"):
        return False
    # "Type a message" is a label; "the run on staging" is a sentence cut short.
    lower = sum(1 for wd in words if wd[:1].islower())
    return not (len(words) >= 4 and lower >= 2)


def _phrases(lines: list[Line]) -> list[Line]:
    """Lines on one row that OCR split at a small gap, joined: a tab's title and the project
    after it are one piece of text."""
    out: list[Line] = []
    for row in _rows(lines):
        row = sorted(row, key=lambda ln: ln.box[0])
        cur = row[0]
        for ln in row[1:]:
            gap = ln.box[0] - (cur.box[0] + cur.box[2])
            if gap <= 1.0 * min(cur.box[3], ln.box[3]):
                cur = Line(f"{cur.text} {ln.text}", union((cur.box, ln.box)), cur.words + ln.words)
            else:
                out.append(cur)
                cur = ln
        out.append(cur)
    return out


def _isolated(lines: list[Line]) -> tuple[Line | None, Line | None]:
    """The first and last lines of a pane when blank space sets them apart from the rest."""
    if len(lines) < 2:
        return None, None
    ls = sorted(lines, key=lambda ln: ln.box[1])
    h = sorted(ln.box[3] for ln in ls)[len(ls) // 2]
    first = ls[0] if ls[1].box[1] - (ls[0].box[1] + ls[0].box[3]) >= ISOLATED * h else None
    last = ls[-1] if ls[-1].box[1] - (ls[-2].box[1] + ls[-2].box[3]) >= ISOLATED * h else None
    return first, last


def _anchors(infos: list[Pane], focus: int | None, w: int, h: int) -> list[Anchor]:
    content = {
        fold(ln.text).replace(" ", "")
        for p in infos
        if p.role in ("list", "table", "content")
        for ln in p.lines
    }
    found: list[Anchor] = []
    for p in infos:
        if p.reader in ("grid", "table") or p.role in ("list", "table"):
            continue
        if p.role == "content":
            first, last = _isolated(list(p.lines))
            candidates = [ln for ln in (first, last) if ln is not None]
            base = 1.5
        else:
            candidates = _phrases(list(p.lines))
            top = p.role == "toolbar" and p.box[1] <= 0.02 * h
            base = 3.0 if top or p.role == "nav" else 2.0
        for ln in candidates:
            if not is_label(ln.text):
                continue
            key = fold(ln.text).replace(" ", "")
            # A label inside content repeats a list item or a page (the selected chat's name
            # in the thread's header).
            if p.role != "content" and key in content:
                continue
            found.append(Anchor(ln.text.strip(), ln.box, p.index, base))
    # A label inside another one ("Day" in "Today", "Week" in "Work week") is found wherever
    # the other is, so it tells less.
    every = [fold(ln.text).replace(" ", "") for p in infos for ln in p.lines]
    found = [
        replace(a, score=a.score - 0.5)
        if sum(fold(a.text).replace(" ", "") in t for t in every) > 1
        else a
        for a in found
    ]
    seen: set[str] = set()
    out = []
    for a in sorted(found, key=lambda a: (-a.score, a.box[1], a.box[0])):
        key = fold(a.text).replace(" ", "")
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


# Proposing


@dataclass(frozen=True)
class Proposal:
    app: App  # the whole app: a known app's views with the new one added
    view: str
    calibration: Calibration | None
    regions: dict[str, int] = field(default_factory=dict)  # region -> scan pane index
    anchors: tuple[str, ...] = ()

    @property
    def definition(self) -> str:
        return definition_toml(self.app)

    @property
    def calibration_text(self) -> str:
        if self.calibration is None:
            return ""
        return apps.calibration_toml(self.app.id, self.view, self.calibration, "learned")

    @property
    def text(self) -> str:
        """A whole apps.toml for this app alone: what `parse` reads back."""
        parts = [f"format = {apps.FORMAT}", self.definition]
        if self.calibration_text:
            parts.append(self.calibration_text)
        return "\n\n".join(p.rstrip() for p in parts) + "\n"


def stable_anchors(scans: Sequence[Scan]) -> list[Anchor]:
    """The first scan's anchors that every other scan shows within STABLE of the same place."""
    first, rest = scans[0], scans[1:]
    out = []
    for a in first.anchors:
        key = fold(a.text).replace(" ", "")
        ax, ay = a.box[0] / first.width, a.box[1] / first.height
        ok = True
        for s in rest:
            near = [
                ln
                for ln in s.lines
                if key in fold(ln.text).replace(" ", "")
                and abs(ln.box[0] / s.width - ax) <= STABLE
                and abs(ln.box[1] / s.height - ay) <= STABLE
            ]
            ok = ok and bool(near)
        if ok:
            out.append(a)
    return out


def propose(
    scans: Scan | Sequence[Scan],
    app_id: str,
    view_id: str,
    names: Mapping[int, str] | None = None,
    known: App | None = None,
    name: str | None = None,
) -> Proposal:
    """A definition for what the scans show, as app_id's view view_id. More than one scan
    (the same view after a scroll or another item opened) keeps only the anchors all show
    in one place. names maps scan pane indexes to region names; the rest are named by role
    (toolbar, nav, header, footer, sidebar, list, table, content). known: the app when this
    is a new view of it; its app-wide cues the new view does not show move to its views."""
    ss = [scans] if isinstance(scans, Scan) else list(scans)
    s = ss[0]
    for k, v in (("app", app_id), ("view", view_id)):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", v) or v == apps.SESSION:
            raise apps.AppError(f"{k} '{v}' must be letters, digits, - or _")
    names = dict(names or {})
    for i in names:
        if not 0 <= i < len(s.panes):
            raise apps.AppError(f"names: no pane P{i} (P0 to P{len(s.panes) - 1})")
    w, h = s.width, s.height

    regions: dict[str, Region] = {}
    index: dict[str, int] = {}
    used: dict[str, int] = {}
    for p in s.panes:
        base = names.get(p.index) or p.role
        used[base] = used.get(base, 0) + 1
        key = base if used[base] == 1 and base not in regions else f"{base}{used[base]}"
        regions[key] = _region(key, p, p.index == s.focus, w, h)
        index[key] = p.index

    chosen = (stable_anchors(ss) if len(ss) > 1 else list(s.anchors))[:MAX_ANCHORS]
    strong = {a.text for a in chosen[:2]}

    def cue(a: Anchor) -> Cue:
        at = (round(a.box[0] / w, 3), round(a.box[1] / h, 3))
        return Cue(text=(a.text,), at=(at,), weight=2.0 if a.text in strong else 1.0)

    wide = {p.index for p in s.panes if p.role == "nav" or (p.role == "toolbar" and p.box[1] <= 2)}
    app_cues = [cue(a) for a in chosen if a.pane in wide]
    view_cues = [cue(a) for a in chosen if a.pane not in wide]
    if luma(s.background) < 100:
        app_cues.append(Cue(background=s.background, weight=2.0))
    for p in s.panes:
        if p.index in wide and len(app_cues) < MAX_ANCHORS + 3:
            app_cues.append(Cue(pane=_frac(p.box, w, h), weight=0.5))
    ready = s.ready if s.ready in {a.text for a in chosen} else None
    prompt = s.prompt
    if prompt:
        view_cues.append(Cue(regex=prompt, weight=2.0))
    view = View(view_id, tuple(view_cues), ready, prompt, regions)

    if known is None:
        app = App(app_id, name or app_id, tuple(app_cues), {view_id: view}, source="learned")
    else:
        app = _add_view(known, view, app_cues, s)

    cal = None
    grids = {}
    for key, reg in regions.items():
        if reg.reader == "grid":
            box = s.panes[index[key]].box
            m = grid_metrics(list(s.lines), box)
            if m:
                grids[key] = Grid(round(m[0], 1), round(m[1], 2), m[2])
    if grids:
        cal = Calibration((Variant("", grids=grids),))
    return Proposal(app, view_id, cal, index, tuple(a.text for a in chosen))


def _add_view(known: App, view: View, app_cues: list[Cue], s: Scan) -> App:
    """known with view added: the new view's cues lose those the app already has, and app
    cues this screen does not show move to the views that had them."""
    have = {fold(t).replace(" ", "") for c in known.cues for t in c.text}
    shown = {fold(ln.text).replace(" ", "") for ln in s.lines}
    keep, move = [], []
    for c in known.cues:
        texts = [fold(t).replace(" ", "") for t in c.text]
        if c.text and not any(any(t in ln for ln in shown) for t in texts):
            move.append(c)
        else:
            keep.append(c)
    own = [
        c
        for c in (*app_cues, *view.cues)
        if not c.text or fold(c.text[0]).replace(" ", "") not in have
    ]
    own = [c for c in own if c.pane is None and c.background is None]
    views = {k: replace(v, cues=v.cues + tuple(move)) for k, v in known.views.items()}
    views[view.id] = replace(view, cues=tuple(own))
    return replace(known, cues=tuple(keep), views=views)


def _frac(box: Box, w: int, h: int) -> Fraction:
    f = to_fraction(box, w, h)
    x, y = round(f[0], 3), round(f[1], 3)
    return (x, y, round(min(f[2], 1 - x), 3), round(min(f[3], 1 - y), 3))


def _region(key: str, p: Pane, focus: bool, w: int, h: int) -> Region:
    def centre(b: Box) -> tuple[float, float]:
        return (round((b[0] + b[2] / 2) / w, 3), round((b[1] + b[3] / 2) / h, 3))

    to = None
    if len(p.members) > 1:
        first = min(p.members, key=lambda b: (b[1], b[0]))
        last = max(p.members, key=lambda b: (b[1] + b[3], b[0] + b[2]))
        at, to = centre(first), centre(last)
    else:
        at = centre(p.box)
    return Region(key, p.reader, at, to, _frac(p.box, w, h), focus)


def luma(c: RGB) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


# Writing definitions


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list | tuple):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(str(k))} = {_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} as TOML")


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def _regex(r: str) -> str:
    # A literal string keeps backslashes as written, as the built-in files do.
    return f"'{r}'" if "'" not in r and "\n" not in r else json.dumps(r)


def _cue_lines(c: Cue) -> list[str]:
    out = []
    if c.text:
        out.append(f"text = {_value(c.text[0] if len(c.text) == 1 else list(c.text))}")
    if c.regex is not None:
        out.append(f"regex = {_regex(c.regex)}")
    if c.background is not None:
        out.append(f"background = {_value(list(c.background))}")
    if c.pane is not None:
        out.append(f"pane = {_value(list(c.pane))}")
    if c.at:
        at = [list(p) for p in c.at]
        out.append(f"at = {_value(at[0] if len(at) == 1 else at)}")
    if c.near != 0.1:
        out.append(f"near = {_value(c.near)}")
    if c.weight != 1.0:
        out.append(f"weight = {_value(c.weight)}")
    if c.report:
        out.append(f"report = {_value(c.report)}")
    return out


def definition_toml(app: App) -> str:
    """The [app.<id>] tables for app, in the format apps.parse reads (without the
    `format = 1` line, so it can be appended to a file that has one)."""
    a = f"app.{_key(app.id)}"
    out = [f"[{a}]", f"name = {_value(app.name)}"]
    for c in app.cues:
        out += ["", f"[[{a}.cue]]", *_cue_lines(c)]
    for k in ("policy", "action"):
        table = getattr(app, k)
        if table:
            out += ["", f"[{a}.{k}]", *(f"{_key(x)} = {_value(v)}" for x, v in table.items())]
    for v in app.views.values():
        vk = f"{a}.view.{_key(v.id)}"
        out += ["", f"[{vk}]"]
        if v.ready:
            out.append(f"ready = {_value(v.ready)}")
        if v.prompt:
            out.append(f"prompt = {_regex(v.prompt)}")
        for c in v.cues:
            out += ["", f"[[{vk}.cue]]", *_cue_lines(c)]
        for r in v.regions.values():
            out += ["", f"[{vk}.region.{_key(r.name)}]"]
            if r.reader != "text":
                out.append(f"reader = {_value(r.reader)}")
            if r.kind:
                out.append(f"kind = {_value(r.kind)}")
            if r.at:
                out.append(f"at = {_value(list(r.at))}")
            if r.to:
                out.append(f"to = {_value(list(r.to))}")
            if r.box:
                out.append(f"box = {_value(list(r.box))}")
            if r.focus:
                out.append("focus = true")
    return "\n".join(out) + "\n"


# Verifying


@dataclass(frozen=True)
class Check:
    """One frame checked: who it was identified as, where each region went (and how:
    pane or box), and where each anchor was found against where it belongs."""

    identity: Identity
    regions: dict[str, tuple[Box, str] | None]
    anchors: dict[str, float | None]  # cue text -> distance from `at`; None: not on screen


@dataclass(frozen=True)
class Verification:
    checks: tuple[Check, ...]
    problems: tuple[str, ...]
    unstable: tuple[str, ...]  # anchors missing or moved on some frame
    ready: str | None  # the anchor that appeared last, when the frames came in that order

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(
    app: App | str,
    view: str,
    scans: Sequence[Scan],
    others: Iterable[App] | None = None,
    min_confidence: float = MIN_CONFIDENCE,
    loading: bool = False,
) -> Verification:
    """Whether app's view holds on more frames of it (after a scroll, another item opened):
    identification picks it with confidence among the other apps (default every built-in
    app but one of the same id), every region lands on a pane, and every anchor is where it
    belongs. app may be TOML text (format 1) defining it.

    loading: the frames were captured in order while the view loaded. Only the last must
    be the whole view; an anchor missing only from the first frames arrived late, and the
    last to arrive is the ready cue."""
    if isinstance(app, str):
        doc = apps.parse(app, "learned", "learned")
        if len(doc.apps) != 1:
            raise apps.AppError("verify: the definition must hold exactly one app")
        app = next(iter(doc.apps.values()))
    if view not in app.views:
        raise apps.AppError(f"verify: {app.id} has no view '{view}'")
    pool = [a for a in (others if others is not None else _builtin_apps()) if a.id != app.id]
    pool.append(app)
    v = app.views[view]
    cues = [c for c in app.cues + v.cues if c.text and c.at]
    checks, problems = [], []
    for n, s in enumerate(scans):
        whole = not loading or n == len(scans) - 1
        who = identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)
        if whole and (who.app, who.view) != (app.id, view):
            problems.append(f"frame {n}: identified as {who.app}.{who.view}, not {app.id}.{view}")
        elif whole and who.confidence < min_confidence:
            problems.append(
                f"frame {n}: {app.id}.{view} at confidence {who.confidence}"
                + (f", runner-up {who.runner_up[0]}.{who.runner_up[1]}" if who.runner_up else "")
            )
        placed: dict[str, tuple[Box, str] | None] = {}
        for r in v.regions.values():
            got = apps.place(r, list(s.raw), s.width, s.height)
            placed[r.name] = got
            if not whole:
                continue
            if got is None:
                problems.append(f"frame {n}: region {r.name} cannot be placed")
            elif r.at and got[1] != "pane":
                problems.append(f"frame {n}: region {r.name} found no pane, used its box")
        where = {c.text[0]: _distance(c, s) for c in cues}
        checks.append(Check(who, placed, where))
    unstable = []
    for c in cues:
        t = c.text[0]
        here = [(d := ch.anchors[t]) is not None and d <= c.near for ch in checks]
        if loading and True in here:
            # Arriving late is loading; leaving again is not.
            here = here[here.index(True) :]
        if not all(here):
            unstable.append(t)
            problems.append(
                f"anchor '{t}' missing or moved on {here.count(False)} of {len(here)} frames"
            )
    ready = _last_to_appear(cues, checks) if loading else None
    return Verification(tuple(checks), tuple(problems), tuple(dict.fromkeys(unstable)), ready)


def _distance(c: Cue, s: Scan) -> float | None:
    keys = [fold(t).replace(" ", "") for t in c.text]
    best = None
    for ln in s.lines:
        f = fold(ln.text).replace(" ", "")
        if any(k and k in f for k in keys):
            x, y = ln.box[0] / s.width, ln.box[1] / s.height
            d = min(((x - ax) ** 2 + (y - ay) ** 2) ** 0.5 for ax, ay in c.at)
            best = d if best is None else min(best, d)
    return best


def _last_to_appear(cues: list[Cue], checks: list[Check]) -> str | None:
    """With frames in the order they came while the view loaded, the anchor that showed up
    last: what says the view is ready. None when every anchor was there from the start."""
    if len(checks) < 2:
        return None
    first: dict[str, int] = {}
    for c in cues:
        t = c.text[0]
        here = [(d := ch.anchors.get(t)) is not None and d <= c.near for ch in checks]
        if here[-1]:
            first[t] = here.index(True)
    if not first or max(first.values()) == 0:
        return None
    return max(first.items(), key=lambda kv: kv[1])[0]


def refine(p: Proposal, v: Verification) -> Proposal:
    """p without the anchors verify found unstable, and with the ready cue it found."""
    drop = set(v.unstable)

    def keep(cues: tuple[Cue, ...]) -> tuple[Cue, ...]:
        return tuple(c for c in cues if not (c.text and c.text[0] in drop))

    view = p.app.views[p.view]
    ready = v.ready or (view.ready if view.ready not in drop else None)
    views = {**p.app.views, p.view: replace(view, cues=keep(view.cues), ready=ready)}
    app = replace(p.app, cues=keep(p.app.cues), views=views)
    return replace(p, app=app, anchors=tuple(a for a in p.anchors if a not in drop))


def _builtin_apps() -> list[App]:
    return [a for d in apps.builtin() for a in d.apps.values()]


# Variants


def variants(
    app: App, view: str, scans: Sequence[Scan], names: Sequence[str] | None = None
) -> Calibration:
    """Calibration with one variant per scan (zoom levels, window sizes), told apart by
    where the anchor sits, as `jharokha measure --zoom` writes: each with its regions as
    placed on that scan and its grids measured there. The anchor is the strongest text cue
    every scan shows."""
    v = app.views[view]
    cues = sorted(
        (c for c in app.cues + v.cues if c.text), key=lambda c: -c.weight
    )  # stable: definition order among equals
    anchor = None
    spots: list[tuple[float, float]] = []
    for c in cues:
        found = [_find(c.text, s) for s in scans]
        if all(found):
            anchor = c.text[0]
            spots = [f for f in found if f]
            break
    if anchor is None:
        raise apps.AppError("variants: no anchor is on every screen")
    out = []
    for i, s in enumerate(scans):
        regions: dict[str, Fraction] = {}
        grids: dict[str, Grid] = {}
        for r in v.regions.values():
            got = apps.place(r, list(s.raw), s.width, s.height)
            if got is None:
                continue
            regions[r.name] = _frac(got[0], s.width, s.height)
            if r.reader == "grid" and (m := grid_metrics(list(s.lines), got[0])):
                grids[r.name] = Grid(round(m[0], 1), round(m[1], 2), m[2])
        name = names[i] if names else str(i)
        at = (round(spots[i][0], 4), round(spots[i][1], 4))
        out.append(Variant(name, anchor, at, regions, grids))
    return Calibration(tuple(out))


def _find(texts: tuple[str, ...], s: Scan) -> tuple[float, float] | None:
    keys = [fold(t).replace(" ", "") for t in texts]
    for ln in s.lines:
        if any(k and k in fold(ln.text).replace(" ", "") for k in keys):
            return ln.box[0] / s.width, ln.box[1] / s.height
    return None


# Saving


def replace_app(text: str, app_id: str, tables: str) -> str:
    """text (an apps.toml) with every [app.<app_id>...] table dropped and tables appended;
    other apps, calibration and comments outside the dropped tables stay. A missing file
    starts with `format = 1`."""
    keep, drop = [], False
    for line in text.splitlines():
        if m := apps._HEADER.match(line.strip()):
            drop = apps._path(m.group(1))[:2] == ["app", app_id]
        if not drop:
            keep.append(line)
    body = "\n".join(keep).rstrip() or f"format = {apps.FORMAT}"
    return body + "\n\n" + tables.rstrip() + "\n"


def save(text: str, p: Proposal) -> str:
    """What apps.toml should hold with p in it: p's app replacing any of the same id, its
    view's calibration replacing any before. The caller writes it; it is checked to parse
    and load first."""
    out = replace_app(text, p.app.id, p.definition)
    if p.calibration_text:
        out = apps.replace_calibration(out, p.app.id, p.view, p.calibration_text)
    apps.catalog(apps.parse(out, "apps.toml"))
    return out
