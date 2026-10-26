"""Where on the current screen an action's step points: a locator resolved against the
screen's lines, the view's named regions, its panes and the session's spots, to a box and
its centre, or a clear miss with the closest texts on screen.

    { text = "Inbox" }                        fuzzy find, anywhere
    { text = "Inbox", region = "folders" }    only inside a named region
    { right_of = "To" }                       the nearest text right of it on its row, or the
                                              empty field there when the row has none
    { below = "Inbox" }                       the nearest text under it (left_of, above too)
    { region = "compose" }                    a region's centre
    { spot = "taskbar.teams" }                a session spot (a taskbar icon)
    { spot = "tickets.reply" }                an app's spot: its icon, while one is there
    { region = "list", nth = 2 }              the second item of a region (-1: the last), as
                                              its reader makes items (core.readers)
    { text = "Open", nth = 2 }                the second match

Pure: no input, no reading; the caller passes what was read."""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jharokha.core import readers
from jharokha.core.models import Box, Line, Point, Word, inside, rows
from jharokha.core.text import clean, find, fold

KEYS = ("text", "regex", "region", "right_of", "left_of", "below", "above", "spot", "nth")
_SIDES = ("right_of", "left_of", "below", "above")
_SPOT = 16  # px; the box a spot stands for, about an icon's middle
Spot = tuple[int, ...]  # a spot in pixels: x, y (and a size, if apps.toml gives one)


@dataclass(frozen=True)
class Locator:
    text: str | None = None
    regex: bool = False
    region: str | None = None
    right_of: str | None = None
    left_of: str | None = None
    below: str | None = None
    above: str | None = None
    spot: str | None = None
    nth: int | None = None  # 1-based; negative counts from the end

    def describe(self) -> str:
        parts = []
        for k in KEYS:
            v = getattr(self, k)
            if v not in (None, False):
                parts.append(f"{k}={v!r}")
        return "{" + ", ".join(parts) + "}"


@dataclass(frozen=True)
class Hit:
    box: Box
    center: Point
    how: str
    text: str = ""  # untrusted screen text of what was found


@dataclass(frozen=True)
class Miss:
    reason: str
    candidates: tuple[str, ...] = ()  # untrusted: the closest texts on screen


@dataclass(frozen=True)
class RegionInfo:
    """How a named region reads as items: its reader and list kind (core.apps.Region),
    and the boxes of the regions nested in it (a compose box), whose lines are not its."""

    reader: str = "text"
    kind: str | None = None
    inner: tuple[Box, ...] = ()


def locator(t: Mapping[str, object]) -> Locator:
    """A Locator from a TOML inline table; ValueError on a key or value it does not take."""
    extra = sorted(set(t) - set(KEYS))
    if extra:
        raise ValueError(f"unknown locator key {', '.join(extra)}; use {', '.join(KEYS)}")
    kw: dict[str, object] = {}
    for k in ("text", "region", "spot", *_SIDES):
        if k in t:
            if not isinstance(t[k], str) or not str(t[k]).strip():
                raise ValueError(f"locator {k} must be text")
            kw[k] = t[k]
    if "regex" in t:
        if not isinstance(t["regex"], str):
            raise ValueError("locator regex must be a regex")
        kw["text"], kw["regex"] = t["regex"], True
        if "text" in t:
            raise ValueError("locator takes text or regex, not both")
    if "nth" in t:
        n = t["nth"]
        if not isinstance(n, int) or isinstance(n, bool) or n == 0:
            raise ValueError("locator nth must be a whole number other than 0")
        kw["nth"] = n
    loc = Locator(**kw)  # type: ignore[arg-type]
    if sum(getattr(loc, k) is not None for k in _SIDES) > 1:
        raise ValueError("locator takes one of right_of, left_of, below, above")
    if loc.spot and (loc.text or loc.region or any(getattr(loc, k) for k in _SIDES)):
        raise ValueError("locator spot stands alone")
    if not (loc.text or loc.region or loc.spot or any(getattr(loc, k) for k in _SIDES)):
        raise ValueError("locator needs text, regex, region, spot or a side (right_of, ...)")
    return loc


def spot_px(spot: Sequence[float], width: int, height: int) -> Spot:
    """A spot in fractions of the client area (x, y and maybe width, height) in pixels."""
    return tuple(round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(spot))


def row_texts(lines: list[Line]) -> list[str]:
    """One text per screen row, the row's lines joined by two spaces."""
    return ["  ".join(clean(ln.text) for ln in r) for r in rows(lines)]


def unwrapped(lines: list[Line], right: int) -> list[str]:
    """row_texts of a terminal, with a row the terminal wrapped joined to the next: one that
    ends within a few characters of right (the pane's edge) and as far right as any row,
    since a row that merely ends near the edge is not cut."""
    rs = rows(lines)
    edge = max((r[-1].box[0] + r[-1].box[2] for r in rs), default=0)
    out: list[str] = []
    carry = False
    for r in rs:
        text = "  ".join(clean(ln.text) for ln in r)
        if carry:
            out[-1] += text
        else:
            out.append(text)
        end, h = r[-1].box[0] + r[-1].box[2], r[-1].box[3]
        carry = end >= right - 3 * h and end >= edge - h / 2
    return out


def _center(b: Box) -> Point:
    return (b[0] + b[2] // 2, b[1] + b[3] // 2)


def _candidates(lines: list[Line], query: str, n: int = 3) -> tuple[str, ...]:
    q = fold(query)
    scored = sorted(
        ((difflib.SequenceMatcher(None, q, fold(ln.text)).ratio(), clean(ln.text)) for ln in lines),
        key=lambda s: -s[0],
    )
    return tuple(t for _, t in scored[:n] if t)


def _rank(line: str, query: str) -> int:
    """How closely a line that matched query matches it, best first: the whole line, its
    start, whole words inside it, part of a word. Two matches at the best rank are
    ambiguous, and a click on either is a guess."""
    f, q = fold(line).strip(), fold(query).strip()
    if f.replace(" ", "") == q.replace(" ", ""):
        return 0
    word = rf"(?<![^\W_]){re.escape(q)}(?![^\W_])"
    if re.match(word, f):
        return 1
    return 2 if re.search(word, f) else 3


def _pick(found: list[Hit], nth: int | None) -> Hit | None:
    if not found:
        return None
    i = (nth or 1) - 1 if (nth or 1) > 0 else len(found) + (nth or 0)
    return found[i] if 0 <= i < len(found) else None


def locate(
    loc: Locator,
    lines: list[Line],
    regions: Mapping[str, Box],
    spots: Mapping[str, Spot],
    screen: Box,
    info: Mapping[str, RegionInfo] | None = None,
) -> Hit | Miss:
    """loc on this screen. lines are the whole screen's; regions are the placed named
    regions of the view on screen and the session (full names, `teams.list`, or the short
    ones the caller resolved), info how each reads as items (text blocks when not given);
    spots the session's spots and the front app's, in pixels."""
    if loc.spot:
        p = spots.get(loc.spot) or spots.get(f"session.{loc.spot}")
        if p is None:
            known = ", ".join(spots) or "none"
            return Miss(
                f"no spot '{loc.spot}' here (spots: {known}); an app's spots count only while "
                "it is in front"
            )
        at = (p[0], p[1])
        b = (at[0] - _SPOT // 2, at[1] - _SPOT // 2, _SPOT, _SPOT)
        return Hit(b, at, f"spot {loc.spot}")
    bounds = screen
    if loc.region:
        if loc.region not in regions:
            known = ", ".join(regions) or "none"
            return Miss(f"region '{loc.region}' is not on this screen (regions now: {known})")
        bounds = regions[loc.region]
    pool = inside(lines, bounds) if loc.region else lines
    side = next((k for k in _SIDES if getattr(loc, k)), None)
    if side:
        anchor_q = getattr(loc, side)
        anchors = find(pool, anchor_q, all=True)
        if not anchors:
            return Miss(f"'{anchor_q}' is not on screen", _candidates(pool, anchor_q))
        a = anchors[0].box
        hits = _beside(pool, a, side, loc.text, loc.regex)
        if hits:
            hit = _pick(hits, loc.nth)
            if hit is None:
                return Miss(f"only {len(hits)} texts {side.replace('_', ' ')} '{anchor_q}'")
            return hit
        if loc.text:
            return Miss(
                f"'{loc.text}' is not {side.replace('_', ' ')} '{anchor_q}'",
                _candidates(pool, loc.text),
            )
        return Hit(*_field(a, side, bounds), f"empty field {side.replace('_', ' ')} '{anchor_q}'")
    if loc.text:
        where = f" in {loc.region}" if loc.region else ""
        matches = find(pool, loc.text, regex=loc.regex, all=True)
        found = [
            Hit(m.box, m.center, f"text in {loc.region}" if loc.region else "text", m.text)
            for m in matches
        ]
        if loc.nth is None and len(found) > 1:
            ranks = [
                0 if loc.regex else _rank(pool[int(m.ref[1:])].text, loc.text) for m in matches
            ]
            best = [h for h, r in zip(found, ranks, strict=True) if r == min(ranks)]
            if len(best) > 1:
                shown = tuple(clean(pool[int(m.ref[1:])].text) for m in matches)
                return Miss(
                    f"'{loc.text}'{where} is ambiguous: {len(best)} matches alike; nothing "
                    "was picked. Give nth (1 is the first in reading order, -1 the last) or "
                    "a region, or more of the text",
                    tuple(dict.fromkeys(shown))[:5],
                )
            found = best
        hit = _pick(found, loc.nth)
        if hit is None:
            if found:
                return Miss(f"'{loc.text}'{where} is on screen {len(found)} times, not {loc.nth}")
            return Miss(f"'{loc.text}' is not on screen{where}", _candidates(pool, loc.text))
        return hit
    assert loc.region
    if loc.nth is not None:
        reg = (info or {}).get(loc.region, RegionInfo())
        its = readers.items(reg.reader, pool, bounds, reg.kind, reg.inner)
        boxes = [tuple(i.box if hasattr(i, "box") else i["box"]) for i in its]
        found = [
            Hit(b, _center(b), "", readers.item_text(i))  # type: ignore[arg-type]
            for b, i in zip(boxes, its, strict=True)
        ]
        it = _pick(found, loc.nth)
        if it is None:
            return Miss(f"region '{loc.region}' shows {len(its)} items, not item {loc.nth}")
        return Hit(it.box, it.center, f"item {loc.nth} of {loc.region}", it.text)
    return Hit(bounds, _center(bounds), f"centre of {loc.region}")


def _side(b: Box, a: Box, side: str) -> float | None:
    """How far b is from anchor box a on that side, or None when it is not there: on a's row
    for left and right, in its column for above and below."""
    x, y, w, h = b
    ax, ay, aw, ah = a
    same_row = y < ay + ah and y + h > ay
    same_col = x < ax + aw and x + w > ax
    if side == "right_of" and same_row and x >= ax + aw - 2:
        return x - (ax + aw)
    if side == "left_of" and same_row and x + w <= ax + 2:
        return ax - (x + w)
    if side == "below" and same_col and y >= ay + ah - 2:
        return y - (ay + ah)
    if side == "above" and same_col and y + h <= ay + 2:
        return ay - (y + h)
    return None


def _beside(lines: list[Line], a: Box, side: str, text: str | None, regex: bool) -> list[Hit]:
    """Words beside anchor box a, nearest first; only those matching text when given."""
    ax, ay, aw, ah = a
    out: list[tuple[float, Hit]] = []
    for ln in lines:
        for w in ln.words or (Word(ln.text, ln.box),):
            x, y, ww, wh = w.box
            if x >= ax and x + ww <= ax + aw and y >= ay and y + wh <= ay + ah:
                continue  # the anchor itself
            d = _side(w.box, a, side)
            if d is not None:
                out.append((d, Hit(w.box, _center(w.box), side.replace("_", " "), clean(w.text))))
    out.sort(key=lambda o: o[0])
    hits = [h for _, h in out]
    if text:
        hits = [
            h
            for h in hits
            if find([Line(h.text, h.box, (Word(h.text, h.box),))], text, regex=regex)
        ]
    return hits


def _field(a: Box, side: str, bounds: Box) -> tuple[Box, Point]:
    """The blank space beside an anchor with nothing next to it: a label's empty field."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = bounds
    if side == "right_of":
        b = (ax + aw + 4, ay, max(8, min(300, bx + bw - ax - aw - 4)), ah)
    elif side == "left_of":
        w = max(8, min(300, ax - bx - 4))
        b = (ax - 4 - w, ay, w, ah)
    elif side == "below":
        b = (ax, ay + ah + 4, aw, ah)
    else:
        b = (ax, ay - 4 - ah, aw, ah)
    return b, _center(b)
