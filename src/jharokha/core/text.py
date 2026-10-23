"""Turn OCR lines into something an agent can read, search and trust only as data."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass

from jharokha.core.models import Box, Line, Word, union

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮⁠-⁤﻿]")
_LOOKALIKE = re.compile(r"[A-Za-z]\d|\d[A-Za-z]")
_SUSPECT = re.compile(r"\d|[A-Z]{3}")
# OCR confuses these; fold both sides of a search to the same letter.
_FOLD = str.maketrans("0|1!5$8", "oll1ssb")
_DASHES = str.maketrans("—–‐‑−", "-----")
# OCR puts spaces around path and URL punctuation: `links .txt`, `https : //host . com`.
_PUNCT_GAP = re.compile(r"\s+(?=[.:/\\])|(?<=[./\\])\s+|(?<=:)\s+(?=/)")
# A terminal's caret after its prompt reads as a letter: `PS C:\repo>I`.
_CARET = re.compile(r"(?<=[>$#])\s*[I|_l]$")
_LOCK_HINTS = (
    "press ctrl+alt+del",
    "ctrl + alt + delete",
    "ctrl+alt+delete to unlock",
    "pc is locked",
    "sign-in options",
    "i forgot my password",
    "i forgot my pin",
)
_CLOCK = re.compile(r"^\d{1,2}:\d{2}$")
_LONG_DATE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday), \w+ \d{1,2}$", re.IGNORECASE
)


def clean(s: str) -> str:
    return _CONTROL.sub("", s)


def reading_order(lines: list[Line], panes: list[Box] | None = None) -> list[Line]:
    """Cell by cell (see cells), rows top to bottom and left to right inside each. That
    keeps a chat list separate from the thread beside it."""
    return [ln for _, p in cells(lines, None, panes) for ln in _rows(p)]


def cells(
    lines: list[Line], bounds: Box | None = None, panes: list[Box] | None = None
) -> list[tuple[Box, list[Line]]]:
    """The parts of the screen that hold text, in reading order, each with its lines. With
    pixel panes (core.panes.reading_panes), the panes: a line belongs to the one its centre
    is in, and lines in none come after, in text-gap panels. Without, as on a screen with
    no dividers or colours to go by, the text-gap panels.

    Text gaps are not tried inside a pane: on the eval screens they only ever cut a table
    into columns (an Edge page: CER .08 by panes alone, .50 with gaps inside), and the
    lists and threads they used to separate have their own panes."""
    if not panes:
        return panels(lines, bounds)
    inside: list[list[Line]] = [[] for _ in panes]
    rest = []
    for ln in lines:
        cx, cy = ln.box[0] + ln.box[2] / 2, ln.box[1] + ln.box[3] / 2
        at = (i for i, (x, y, w, h) in enumerate(panes) if x <= cx < x + w and y <= cy < y + h)
        i = next(at, None)
        (rest if i is None else inside[i]).append(ln)
    out = [(pane, ls) for pane, ls in zip(panes, inside, strict=True) if ls]
    return out + (panels(rest) if rest else [])


def panels(lines: list[Line], bounds: Box | None = None) -> list[tuple[Box, list[Line]]]:
    """Side-by-side columns and the bands above or below them, in reading order, each with
    its cell: bounds split at the middle of the blank bands, so text that appears later
    still falls inside. A blank band running the full height of a region (three line
    heights or wider) splits it into columns; one running the full width (1.5 line heights)
    into stacked blocks; stacked blocks of the same width merge back into one panel."""
    if not lines:
        return []
    cell = bounds or union(ln.box for ln in lines)
    if len(lines) < 2:
        return [(cell, list(lines))]
    heights = sorted(ln.box[3] for ln in lines)
    h = heights[len(heights) // 2]
    extent = union(ln.box for ln in lines)
    # A toolbar or a label row has wide gaps too; only something taller is columns.
    cols = _cuts(lines, 0, 3 * h) if extent[3] >= 3 * h else []
    if cut := next((c for c in cols if _side_by_side(c[0], c[1])), None):
        a, b = _split(cell, 0, cut[2])
        return panels(cut[0], a) + panels(cut[1], b)
    if cut := _cut(lines, 1, 1.5 * h):
        a, b = _split(cell, 1, cut[2])
        out = panels(cut[0], a)
        for box, ls in panels(cut[1], b):
            prev, pls = out[-1]
            if (prev[0], prev[2]) == (box[0], box[2]) and prev[1] + prev[3] == box[1]:
                out[-1] = ((prev[0], prev[1], prev[2], prev[3] + box[3]), pls + ls)
            else:
                out.append((box, ls))
        return out
    return [(cell, list(lines))]


def _side_by_side(a: list[Line], b: list[Line]) -> bool:
    """Columns share rows: a third of the smaller side's lines have a line beside them on the
    other side. A chat thread's own messages sit right-aligned but never beside the others,
    so they stay one column."""
    small, big = sorted((a, b), key=len)
    beside = sum(
        any(ln.box[1] < m.box[1] + m.box[3] and m.box[1] < ln.box[1] + ln.box[3] for m in big)
        for ln in small
    )
    return beside * 3 >= len(small)


def _split(cell: Box, axis: int, at: int) -> tuple[Box, Box]:
    x, y, w, h = cell
    if axis == 0:
        return (x, y, at - x, h), (at, y, x + w - at, h)
    return (x, y, w, at - y), (x, at, w, y + h - at)


Cut = tuple[list[Line], list[Line], int]


def _cuts(lines: list[Line], axis: int, min_gap: float) -> list[Cut]:
    """Every split at an empty band along axis (0 = x, 1 = y) at least min_gap wide, widest
    first, each with the middle of its band."""
    spans = sorted((ln.box[axis], ln.box[axis] + ln.box[axis + 2]) for ln in lines)
    end, gaps = spans[0][1], []
    for start, stop in spans[1:]:
        if start - end >= min_gap:
            gaps.append((start - end, end))
        end = max(end, stop)
    out = []
    for width, at in sorted(gaps, reverse=True):
        before = [ln for ln in lines if ln.box[axis] + ln.box[axis + 2] <= at]
        after = [ln for ln in lines if ln.box[axis] + ln.box[axis + 2] > at]
        out.append((before, after, int(at + width / 2)))
    return out


def _cut(lines: list[Line], axis: int, min_gap: float) -> Cut | None:
    return next(iter(_cuts(lines, axis, min_gap)), None)


def _rows(lines: list[Line]) -> list[Line]:
    """Two lines share a row when their vertical centres are closer than half a line height."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: ln.box[1]):
        cy = ln.box[1] + ln.box[3] / 2
        for row in rows:
            r = row[-1]
            if abs(cy - (r.box[1] + r.box[3] / 2)) < max(r.box[3], ln.box[3]) / 2:
                row.append(ln)
                break
        else:
            rows.append([ln])
    return [ln for row in rows for ln in _join(sorted(row, key=lambda ln: ln.box[0]))]


def _join(row: list[Line]) -> list[Line]:
    """Pieces of one line that OCR split apart (it does on monospaced terminal text) join
    when the gap is under 1.5 line heights; wider gaps are separate cells of a table row."""
    out = [row[0]]
    for ln in row[1:]:
        prev = out[-1]
        gap = ln.box[0] - (prev.box[0] + prev.box[2])
        if gap <= 1.5 * max(prev.box[3], ln.box[3]):
            words = prev.words + ln.words
            out[-1] = Line(f"{prev.text} {ln.text}", union((prev.box, ln.box)), words)
        else:
            out.append(ln)
    return out


def _within(lines: list[Line], box: Box) -> list[Line]:
    x0, y0, bw, bh = box
    return [
        ln
        for ln in lines
        if x0 <= ln.box[0] + ln.box[2] / 2 < x0 + bw and y0 <= ln.box[1] + ln.box[3] / 2 < y0 + bh
    ]


def _metrics(words: list[Word]) -> tuple[float, float, float, float]:
    """Row pitch, character width, left edge and first row centre, from the words."""
    h = sorted(w.box[3] for w in words)[len(words) // 2]
    pitch = _pitch([w.box[1] + w.box[3] / 2 for w in words], h)
    widths = sorted(w.box[2] / len(w.text) for w in words if len(w.text) >= 5)
    widths = widths or sorted(w.box[2] / max(1, len(w.text)) for w in words)
    cw = widths[len(widths) // 2]
    left = min(w.box[0] for w in words)
    top = min(w.box[1] + w.box[3] / 2 for w in words)
    return pitch, cw, left, top


def grid_metrics(lines: list[Line], box: Box) -> tuple[float, float, tuple[int, int]] | None:
    """What `jharokha measure` writes for a grid region: row pitch and character width in
    pixels, and the first cell's top-left relative to the region. None without text."""
    words = [w for ln in _within(lines, box) for w in (ln.words or (Word(ln.text, ln.box),))]
    if len(words) < 2:
        return None
    pitch, cw, left, top = _metrics(words)
    return pitch, cw, (round(left - box[0]), round(top - pitch / 2 - box[1]))


def grid(
    lines: list[Line],
    box: Box,
    pitch: float | None = None,
    char_width: float | None = None,
    origin: tuple[int, int] | None = None,
) -> list[Line]:
    """Lines inside box re-read as a terminal's character grid; the others as they were.
    pitch, char_width and origin (the first cell's top-left in box) come from calibration
    when it has them, otherwise from the words on screen.

    OCR splits a terminal row into pieces that do not line up and drops the spaces that
    align columns. A grid has a fixed row pitch and character width, so words snap to rows
    by pitch, and the gap between two words becomes that many spaces: `dist/index . html`
    is `dist/index.html` again and a table keeps its columns. A row that runs to the right
    edge continues on the next one and is joined to it. Zeros read as e, @ or o inside a
    number (slashed or dotted zeros) become 0."""
    x0, y0, bw, bh = box
    inside = _within(lines, box)
    words = [w for ln in inside for w in (ln.words or (Word(ln.text, ln.box),))]
    if len(words) < 2:
        return lines
    seen = _metrics(words)
    # Calibration (apps.toml) fixes what one screen can get wrong: a pitch from two rows,
    # a left edge from an indented first word.
    pitch = pitch or seen[0]
    cw = char_width or seen[1]
    left = x0 + origin[0] if origin else seen[2]
    top = y0 + origin[1] + pitch / 2 if origin else seen[3]
    rows: dict[int, list[Word]] = {}
    for w in words:
        rows.setdefault(round((w.box[1] + w.box[3] / 2 - top) / pitch), []).append(w)
    out: list[Line] = []
    prev_row, wrapped = None, False
    for r in sorted(rows):
        ws = sorted(rows[r], key=lambda w: w.box[0])
        text = " " * round((ws[0].box[0] - left) / cw)
        for a, b in zip([None, *ws], ws, strict=False):
            if a is not None:
                # A word's box is its ink, a little narrower than its cells.
                text += " " * max(0, round((b.box[0] - a.box[0] - a.box[2]) / cw - 0.3))
            text += clean(b.text)
        text = _ZERO_IN_NUMBER.sub(lambda m: _ZEROS.sub("0", m.group()), text)
        line = Line(text, union(w.box for w in ws), tuple(ws))
        if wrapped and prev_row == r - 1:
            last = out[-1]
            line = Line(
                last.text + text.lstrip(), union((last.box, line.box)), last.words + line.words
            )
            out[-1] = line
        else:
            out.append(line)
        prev_row = r
        wrapped = ws[-1].box[0] + ws[-1].box[2] >= x0 + bw - 2.5 * cw
    keep = set(map(id, inside))
    first = next(i for i, ln in enumerate(lines) if id(ln) in keep)
    rest = [ln for ln in lines if id(ln) not in keep]
    return rest[:first] + out + rest[first:]


# A number with a decimal point or separator; e, @, o and O in it are zeros misread.
_ZERO_IN_NUMBER = re.compile(r"(?<![\w.])(?=[\w.,]*\d)(?=[\w.,]*[.,])[\d.,e@oO]+(?![\w.])")
_ZEROS = re.compile(r"[e@oO]")


def _pitch(centres: list[float], h: int) -> float:
    """The row pitch: the typical distance between neighbouring rows, from word centres
    clustered into rows."""
    rows: list[float] = []
    for c in sorted(centres):
        if not rows or c - rows[-1] > h / 2:
            rows.append(c)
    gaps = [b - a for a, b in zip(rows, rows[1:], strict=False)]
    if not gaps:
        return float(h)
    small = [g for g in gaps if g <= 1.5 * min(gaps)]
    return sorted(small)[len(small) // 2]


def command_output(lines: list[Line], prompt: str, command: str) -> tuple[list[Line], bool] | None:
    """What a command typed at a shell prompt printed, once the shell is back at its prompt:
    the lines between the command's own line (the prompt, then the command) and the first
    idle prompt below it. None while it runs. prompt is a regex for the prompt at the start
    of a line. The bool is whether the command's line was found; when it scrolled off the
    top, the output is everything above the prompt and its start is missing."""
    pat = re.compile(prompt)
    first = next((c for c in command.splitlines() if c.strip()), "")
    want = fold(first)[:30]
    rows = sorted(lines, key=lambda ln: ln.box[1])
    echoes, idle = [], []
    for i, ln in enumerate(rows):
        t = normalize(ln.text)
        if not (m := pat.match(t)):
            continue
        rest = t[m.end() :].strip()
        if len(rest) <= 1:  # nothing typed, or the caret read as a character
            idle.append(i)
        elif want and difflib.SequenceMatcher(None, fold(rest)[: len(want)], want).ratio() >= 0.75:
            echoes.append(i)
    if echoes:
        after = [i for i in idle if i > echoes[-1]]
        return (rows[echoes[-1] + 1 : after[0]], True) if after else None
    return (rows[: idle[-1]], False) if idle else None


def lookalike(word: str) -> bool:
    return bool(_LOOKALIKE.search(word))


def suspect(line: Line) -> bool:
    """Lines where a lookalike misread would hide: any digit, or a run of capitals (an ID
    misread as `INCOOI 2345` has no letter-digit mix left to flag)."""
    return bool(_SUSPECT.search(line.text))


def agree(a: str, b: str) -> bool:
    """Same reading apart from spacing, which the two engines split differently."""
    return "".join(a.split()) == "".join(b.split())


def apply_verified(lines: list[Line], alt: dict[int, str]) -> list[Line]:
    """Lines with their second reading where it differs. Word boxes survive when the word
    count matches; otherwise the line is one word."""
    out = []
    for i, ln in enumerate(lines):
        t = alt.get(i)
        if t is None or agree(t, ln.text):
            out.append(ln)
            continue
        parts = t.split()
        if len(parts) == len(ln.words):
            words = tuple(Word(p, w.box) for p, w in zip(parts, ln.words, strict=True))
        else:
            words = (Word(t, ln.box),)
        out.append(Line(t, ln.box, words))
    return out


def wrap_untrusted(text: str) -> str:
    return f'<screen_text source="ocr" untrusted="true">\n{text}\n</screen_text>'


def _page(lines: list[Line], offset: int, limit: int) -> tuple[list[Line], str]:
    page = lines[offset : offset + limit]
    end = offset + len(page)
    note = ""
    if end < len(lines):
        note = (
            f"\n[truncated: lines {offset}-{end - 1} of {len(lines)}; "
            f"pass offset={end}, a region, or use jharokha_find_text]"
        )
    return page, note


def format_concise(
    lines: list[Line], offset: int = 0, limit: int = 200, alt: dict[int, str] | None = None
) -> str:
    """alt holds second readings by line index; one that disagrees is shown after a `|`."""
    alt = alt or {}
    page, note = _page(lines, offset, limit)
    out = []
    for n, ln in enumerate(page, offset):
        text = clean(ln.text)
        row = f"L{n} [{ln.box[0]},{ln.box[1]}] {text}"
        if n in alt and not agree(alt[n], text):
            row += f" | verify: {alt[n]}"
        out.append(row)
    return wrap_untrusted("\n".join(out) or "(no text)") + note


def format_detailed(
    lines: list[Line], offset: int = 0, limit: int = 200, alt: dict[int, str] | None = None
) -> str:
    alt = alt or {}
    page, note = _page(lines, offset, limit)
    out = [
        {
            "ref": f"L{n}",
            "text": clean(ln.text),
            "box": ln.box,
            "words": [
                {
                    "text": clean(w.text),
                    "box": w.box,
                    **({"lookalike": True} if lookalike(w.text) else {}),
                }
                for w in ln.words
            ],
            **({"verify": alt[n], "agree": agree(alt[n], ln.text)} if n in alt else {}),
        }
        for n, ln in enumerate(page, offset)
    ]
    return wrap_untrusted(json.dumps(out, separators=(",", ":"))) + note


@dataclass(frozen=True)
class Match:
    ref: str
    text: str
    box: Box
    center: tuple[int, int]


def normalize(s: str) -> str:
    """Undo what OCR does to punctuation: dashes of any kind (and runs of them) as `-`/`--`,
    spaces it puts around `.`, `/`, `\\` and `:`, and a caret read as a letter after a
    prompt."""
    s = re.sub(r"-{2,}", "--", clean(s).translate(_DASHES))
    return _CARET.sub("", _PUNCT_GAP.sub("", s))


def fold(s: str) -> str:
    """s as find compares it: normalized, lower case, lookalikes folded."""
    return normalize(s).lower().translate(_FOLD)


def _match(ref: str, words: tuple[Word, ...]) -> Match:
    box = union(w.box for w in words)
    return Match(
        ref,
        clean(" ".join(w.text for w in words)),
        box,
        (box[0] + box[2] // 2, box[1] + box[3] // 2),
    )


def find(
    lines: list[Line], query: str, regex: bool = False, all: bool = False, fuzzy: bool = True
) -> list[Match]:
    """Word spans matching the query. Plain queries fold case, OCR lookalikes and punctuation
    (see normalize), ignore spaces, and fall back to fuzzy (ratio >= 0.8) only when nothing
    matches exactly and fuzzy is on. Regex runs on the raw line, then on the normalized
    line, where the match is the whole line."""
    exact: list[Match] = []
    near: list[Match] = []
    n = max(1, len(query.split()))
    pat = re.compile(query, re.IGNORECASE) if regex else None
    q = fold(query)
    bare = q.replace(" ", "")
    for i, ln in enumerate(lines):
        ref = f"L{i}"
        if pat:
            joined = clean(" ".join(w.text for w in ln.words))
            if m := pat.search(joined):
                # Words whose character span overlaps the match.
                pos, hit = 0, []
                for w in ln.words:
                    end = pos + len(clean(w.text))
                    if pos < m.end() and end > m.start():
                        hit.append(w)
                    pos = end + 1
                exact.append(_match(ref, tuple(hit) or ln.words))
            elif pat.search(normalize(joined)):
                exact.append(_match(ref, ln.words))
            continue
        spans = [
            (size, ln.words[s : s + size])
            for size in (n, n + 1)
            for s in range(len(ln.words) - size + 1)
        ]
        for size, span in spans:
            folded = fold(" ".join(w.text for w in span))
            if q in folded or bare in folded.replace(" ", ""):
                exact.append(_match(ref, span))
                break
            close = fuzzy and size == n
            if close and difflib.SequenceMatcher(None, q, folded).ratio() >= 0.8:
                near.append(_match(ref, span))
                break
        else:
            # OCR split one word into more pieces than the spans cover (a URL, a path).
            if bare in fold(ln.text).replace(" ", ""):
                exact.append(_match(ref, ln.words))
    found = exact or near
    return found if all else found[:1]


def looks_locked(lines: list[Line]) -> bool:
    """A lock or sign-in screen: one of its phrases, or the little such a screen shows (a
    password or PIN box, or only a clock and the date) and nothing else."""
    text = " ".join(ln.text for ln in lines).lower()
    if any(h in text for h in _LOCK_HINTS):
        return True
    if len(lines) > 6:
        return False
    texts = [clean(ln.text).strip() for ln in lines]
    if any(t.lower() in ("password", "pin") for t in texts):
        return True
    return any(_CLOCK.match(t) for t in texts) and any(_LONG_DATE.match(t) for t in texts)
