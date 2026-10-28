"""Bubbles from pixels: the filled or bordered rectangles a chat thread draws its messages,
pictures and cards in, found without any text. OCR lines cannot see them: a paragraph two
pitches below the one above in the same bubble reads like the next bubble, and a picture
holds no lines at all.

A rectangle's left side is a column where the background gives way to something else
(a fill, a border) on every row from its top to its bottom, its right side a column where
something gives way to the background along the same rows. Glyphs make such columns too,
but only as tall as a letter: a side must run at least a line. A hole of background inside
a rectangle bounded the other way round (something, then background, then something) is a
rectangle too: a card's white body inside a tinted bubble."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from jharokha.core.lazy import LazyNumpy
from jharokha.core.models import Box, Frame

if TYPE_CHECKING:
    import numpy as np
else:
    np = LazyNumpy(globals())

# Levels any channel must differ from the background by. Teams paints others' bubbles
# 10 below a white pane, a picture's border 8 to 15, and leaves a flat white 1 to 3 off.
_MARGIN = 6
_SIDE = 2  # px a side may wander from row to row as the codec smears it
_SPAN = 3  # px two sides' tops (or bottoms) may differ and still bound one rectangle
_WIDE = 1.5  # line heights: the narrowest rectangle, a few characters
_SAME = 4  # px: a rectangle this close inside another on every side is the same one
# px inside a rectangle's sides where its fill is looked at: past a border, inside the
# padding a bubble keeps around its text.
_RING = (3, 7)
_AVATAR = 2  # line heights an avatar beside a bubble's first rows may hide of its left side


class Bubble(NamedTuple):
    box: Box
    filled: bool  # painted in a tint, as a message is; a picture or a card is only bordered


def bubbles(frame: Frame, region: Box, line: int = 18) -> list[Bubble]:
    """boxes, each with whether it is filled: most of a ring just inside its sides differs
    from the background (the padding of a bubble's tint around its text), rather than
    being the background inside a border (a card) or whatever a picture shows at its edge."""
    found, other, (rx, ry) = _scan(frame, region, line)
    out = []
    a, b = _RING
    for x, y, w, h in found:
        m = other[y - ry : y - ry + h, x - rx : x - rx + w]
        ring = [m[a:b, a:-a], m[-b:-a, a:-a], m[b:-b, a:b], m[b:-b, -b:-a]]
        n = sum(r.size for r in ring)
        out.append(Bubble((x, y, w, h), n > 0 and 2 * sum(int(r.sum()) for r in ring) > n))
    return out


def boxes(frame: Frame, region: Box, line: int = 18) -> list[Box]:
    """The rectangles inside region that stand out from its background, at least line tall
    and a few characters wide, as (x, y, w, h) in client pixels, top to bottom."""
    return _scan(frame, region, line)[0]


def _scan(frame: Frame, region: Box, line: int) -> tuple[list[Box], np.ndarray, tuple[int, int]]:
    """boxes, the mask of what differs from the background, and the mask's origin."""
    rx, ry, rw, rh = _clip(frame, region)
    a = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    px = a[ry : ry + rh, rx : rx + rw, :3].astype(np.int16)
    if rw < 3 or rh < line:
        return [], np.zeros((rh, rw), np.int8), (rx, ry)
    bg = _background(px)
    other = (np.abs(px - np.array(bg, np.int16)).max(axis=2) >= _MARGIN).astype(np.int8)
    # Beyond the region is background: a rectangle the region's side cuts still has sides.
    pad = np.zeros((rh, rw + 2), np.int8)
    pad[:, 1:-1] = other
    step = np.diff(pad, axis=1)  # +1: background then something at x; -1: something then bg
    rises = _sides(step[:, :-1] == 1, line)  # at x: x is something, x - 1 background
    falls = _sides(step[:, 1:] == -1, line)  # at x: x is something, x + 1 background
    wide = int(_WIDE * line)
    outer = _pairs(rises, falls, wide, line)
    # A hole runs from the column after a fall to the one before a rise.
    hollow = _pairs(
        [(x + 1, t, e) for x, t, e in falls], [(x - 1, t, e) for x, t, e in rises], wide, line
    )
    holes = [b for b in hollow if any(_inside(b, o) and b != o for o in outer)]
    out = _distinct(outer + holes)
    found = sorted(((x + rx, y + ry, w, h) for x, y, w, h in out), key=lambda b: (b[1], b[0]))
    return found, other, (rx, ry)


def _clip(frame: Frame, region: Box) -> Box:
    x, y, w, h = region
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.width, x + w), min(frame.height, y + h)
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def _background(px: np.ndarray) -> tuple[int, int, int]:
    """The region's most common colour on a sparse grid: the pane its bubbles sit on."""
    s = px[::4, ::4].reshape(-1, 3).astype(np.int32)
    # The codec moves a flat colour by a level or two; count colours in steps of 4.
    packed = ((s[:, 0] >> 2) << 12) | ((s[:, 1] >> 2) << 6) | (s[:, 2] >> 2)
    values, counts = np.unique(packed, return_counts=True)
    top = packed == values[int(counts.argmax())]
    r, g, b = (int(np.median(s[top, i])) for i in range(3))
    return r, g, b


def _sides(hit: np.ndarray, line: int) -> list[tuple[int, int, int]]:
    """(x, top, bottom) of each run down a column of hit at least line tall, a hit in the
    column beside counting too so a side the codec moved by a pixel stays one run."""
    near = hit.copy()
    for d in range(1, _SIDE):
        near[:, d:] |= hit[:, :-d]
        near[:, :-d] |= hit[:, d:]
    col = np.zeros((near.shape[0] + 2, near.shape[1]), np.int8)
    col[1:-1] = near
    d = np.diff(col, axis=0)
    starts = np.argwhere(d.T == 1)  # (x, y), by x then y
    ends = np.argwhere(d.T == -1)
    long = (ends[:, 1] - starts[:, 1]) >= line
    runs = [
        (int(x), int(t), int(e)) for (x, t), (_, e) in zip(starts[long], ends[long], strict=True)
    ]
    # Neighbouring columns repeat a side the codec smeared: keep the middle one.
    groups: list[list[tuple[int, int, int]]] = []
    for x, t, e in sorted(runs, key=lambda r: (r[1], r[2], r[0])):
        g = groups[-1] if groups else None
        if g and g[-1][1] == t and g[-1][2] == e and x - g[-1][0] <= _SIDE:
            g.append((x, t, e))
        else:
            groups.append([(x, t, e)])
    return [g[len(g) // 2] for g in groups]


def _pairs(
    lefts: list[tuple[int, int, int]],
    rights: list[tuple[int, int, int]],
    wide: int,
    line: int,
) -> list[Box]:
    """Each left side with the farthest right side along the same rows: a border's inner
    edge, or a line drawn inside a picture, is a nearer one. A left side that starts up to
    an avatar's height below its right side and ends with it bounds the right side's rows:
    the avatar beside a bubble's first rows hides its left side there."""
    out = []
    for xl, t, e in lefts:
        whole, part = [], []
        for xr, t2, e2 in rights:
            if xr - xl + 1 < wide:
                continue
            top, bottom = abs(t2 - t) <= _SPAN, abs(e2 - e) <= _SPAN
            if top and bottom:
                whole.append((xr, t, e))
            elif bottom and t2 < t <= t2 + _AVATAR * line:
                part.append((xr, t2, e))
        if whole or part:
            xr, t0, e0 = max(whole or part)
            out.append((xl, t0, xr - xl + 1, e0 - t0))
    return out


def _inside(a: Box, b: Box) -> bool:
    return (
        b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]
    )


def _distinct(found: list[Box]) -> list[Box]:
    """found without repeats: a rectangle within a few pixels of a larger one on every side
    (a bordered picture's white inside, a side found twice) is that one."""
    out: list[Box] = []
    for b in sorted(set(found), key=lambda b: -b[2] * b[3]):
        if any(_near(b, o) for o in out):
            continue
        out.append(b)
    return out


def _near(a: Box, b: Box) -> bool:
    return (
        abs(a[0] - b[0]) <= _SAME
        and abs(a[1] - b[1]) <= _SAME
        and abs(a[0] + a[2] - b[0] - b[2]) <= _SAME
        and abs(a[1] + a[3] - b[1] - b[3]) <= _SAME
    )
