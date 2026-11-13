"""Text-less UI elements from pixels: the icons along a taskbar, left to right; and every
icon, input field and picture on a screen, so a click on one needs no screenshot.

A taskbar's icons keep their order but not their places: a taskbar holding two more
running apps can draw the same pinned icons 40 px apart instead of 55, so a spot measured
as a fraction clicks an icon two places off. A spot given as a slot (the nth icon) is
found again on every frame (core.session).

Clicks at [x, y] read off a screenshot often land on no text: icons, fields, pictures.
`detect` finds them from the pixels in 100-200 ms a frame: edges less the text OCR read
and the long rules, in 4 px cells, joined into components and sorted by size; fields from
two horizontal edges of one span a line's height apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jharokha.core.models import Box, Frame, Line

CELL = 4  # px: edges are joined in cells this size
EDGE, FAINT = 28, 10  # grey steps that make an edge; a field's faint border
RULE = 56  # px: an edge run this long is a border or separator, not an icon
ICON = (16, 56)  # px: an icon's smaller side at least the first, both at most the second
FIELD_H = (18, 56)  # px between a field's top and bottom edges
NEAR = 12  # px: text this close on both sides makes a blob a glyph OCR skipped
HINT = 160  # px: the farthest text named in a hint on the same row


@dataclass(frozen=True)
class Element:
    kind: str  # icon, field or image
    box: Box
    hint: str  # untrusted: the text it is named by, e.g. 'right of "Refresh"'
    row: str = ""  # 'toolbar icon 3 of 7' when it sits in a row of three or more icons

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.box
        return x + w // 2, y + h // 2


def _edges(g: np.ndarray, thr: int) -> np.ndarray:
    gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
    gy = np.abs(np.diff(g, axis=0, prepend=g[:1]))
    return np.maximum(gx, gy) > thr


def _runs(m: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Horizontal runs of at least n set pixels: rows, starts, ends."""
    pad = np.zeros((m.shape[0], 1), bool)
    d = np.diff(np.hstack([pad, m, pad]).astype(np.int8), axis=1)
    sr, sc = np.nonzero(d == 1)
    _, ec = np.nonzero(d == -1)
    keep = (ec - sc) >= n
    return sr[keep], sc[keep], ec[keep]


def _no_rules(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    for t in (out, out.T):
        for r, s, e in zip(*_runs(t, RULE), strict=True):
            t[r, s:e] = False
    return out


def _components(m: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """8-connected components of a small grid: (x0, y0, x1, y1, cells)."""
    h, w = m.shape
    seen = np.zeros((h, w), bool)
    out = []
    for sy, sx in zip(*(a.tolist() for a in np.nonzero(m)), strict=True):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        stack = [(sy, sx)]
        x0 = x1 = sx
        y0 = y1 = sy
        n = 0
        while stack:
            y, x = stack.pop()
            n += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for yy in (y - 1, y, y + 1):
                for xx in (x - 1, x, x + 1):
                    if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        stack.append((yy, xx))
        out.append((x0, y0, x1, y1, n))
    return out


def _fields(g: np.ndarray) -> list[Box]:
    """Input fields and outlined buttons: two horizontal edges of about the same span, a
    line's height apart; the outermost of nested ones."""
    by_span: dict[tuple[int, int], list[int]] = {}
    for r, s, e in zip(*(a.tolist() for a in _runs(_edges(g, FAINT), RULE)), strict=True):
        by_span.setdefault((s // 4, e // 4), []).append(r)
    found: dict[tuple[int, int, int], Box] = {}
    for (s4, e4), ys in by_span.items():
        for ds in (0, -1, 1):
            for de in (0, -1, 1):
                for y1 in set(ys):
                    for y2 in by_span.get((s4 + ds, e4 + de), []):
                        if FIELD_H[0] <= y2 - y1 <= FIELD_H[1]:
                            found.setdefault(
                                (s4, e4, y1 // 6), (s4 * 4, y1, (e4 - s4) * 4, y2 - y1)
                            )
    kept: list[Box] = []
    for b in sorted(found.values(), key=lambda b: -b[2] * b[3]):
        if not any(_within(b, k, 6) for k in kept):
            kept.append(b)
    return kept


def _within(b: Box, k: Box, tol: int) -> bool:
    return (
        k[0] - tol <= b[0]
        and b[0] + b[2] <= k[0] + k[2] + tol
        and k[1] - tol <= b[1]
        and b[1] + b[3] <= k[1] + k[3] + tol
    )


def _same_row(a: Box, b: Box) -> bool:
    return abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) < max(a[3], b[3]) * 0.6


def detect(frame: Frame, lines: list[Line]) -> list[Element]:
    """Icons, fields and pictures on the frame, in reading order, each named by the text
    beside it (or above it) and its place in a row of icons."""
    a = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    # Pillow's luma (per mille: R 299, G 587, B 114): the thresholds were tuned on it
    c3 = a[:, :, :3].astype(np.int32)
    g = ((c3[:, :, 2] * 299 + c3[:, :, 1] * 587 + c3[:, :, 0] * 114) // 1000).astype(np.int16)
    e = _no_rules(_edges(g, EDGE))
    for ln in lines:  # text is known: words are not icons, and icons beside them stay apart
        x, y, w, h = ln.box
        e[max(0, y - 3) : y + h + 3, max(0, x - 3) : x + w + 3] = False
    hc, wc = frame.height // CELL, frame.width // CELL
    c = np.asarray(e[: hc * CELL, : wc * CELL].reshape(hc, CELL, wc, CELL).any(axis=(1, 3)))
    raw: list[tuple[str, Box]] = []
    for x0, y0, x1, y1, n in _components(c):
        bw, bh = (x1 - x0 + 1) * CELL, (y1 - y0 + 1) * CELL
        box = (x0 * CELL, y0 * CELL, bw, bh)
        if 8 <= bw <= ICON[1] and 8 <= bh <= ICON[1] and 1 / 3 <= bw / bh <= 3:
            raw.append(("icon", box))
        elif ICON[1] < bw <= 760 and 40 < bh <= 640 and n / ((x1 - x0 + 1) * (y1 - y0 + 1)) > 0.2:
            raw.append(("image", box))
    raw += [("field", b) for b in _fields(g)]
    images = [b for k, b in raw if k == "image"]
    kept: list[tuple[str, Box]] = []
    for kind, b in raw:
        if kind == "icon":
            if b[2] < ICON[0] and b[3] < ICON[0]:
                continue  # a speck
            if any(_within(b, i, 0) for i in images):
                continue  # part of a picture
            row = [ln.box for ln in lines if _same_row(b, ln.box)]
            left = any(0 <= b[0] - (r[0] + r[2]) <= NEAR for r in row)
            right = any(0 <= r[0] - (b[0] + b[2]) <= NEAR for r in row)
            if left and right:
                continue  # a glyph between words that OCR skipped
        kept.append((kind, b))
    kept.sort(key=lambda kb: (kb[1][1] // 8, kb[1][0]))
    return _named(kept, lines)


def _named(kept: list[tuple[str, Box]], lines: list[Line]) -> list[Element]:
    hints = []
    for _, b in kept:
        best: tuple[float, str] | None = None
        for ln in lines:
            r = ln.box
            if not _same_row(b, r):
                continue
            if r[0] + r[2] <= b[0]:
                d, side = b[0] - (r[0] + r[2]), "right of"
            elif r[0] >= b[0] + b[2]:
                d, side = r[0] - (b[0] + b[2]), "left of"
            else:
                continue
            if d < HINT and (best is None or d < best[0]):
                best = (d, f'{side} "{ln.text[:24]}"')
        if best is None:
            for ln in lines:
                r = ln.box
                d = b[1] - (r[1] + r[3])
                if (
                    0 <= d < 60
                    and r[0] - 40 <= b[0] <= r[0] + r[2]
                    and (best is None or d < best[0])
                ):
                    best = (d, f'below "{ln.text[:24]}"')
        hints.append(best[1] if best else "")
    rows: dict[int, str] = {}
    icons = [i for i, (k, _) in enumerate(kept) if k == "icon"]
    groups: list[list[int]] = []
    for i in sorted(icons, key=lambda i: (kept[i][1][1], kept[i][1][0])):
        b = kept[i][1]
        if (
            groups
            and abs(kept[groups[-1][0]][1][1] + kept[groups[-1][0]][1][3] / 2 - (b[1] + b[3] / 2))
            < 8
        ):
            groups[-1].append(i)
        else:
            groups.append([i])
    for grp in groups:
        if len(grp) >= 3:
            grp.sort(key=lambda i: kept[i][1][0])
            for k, i in enumerate(grp, 1):
                rows[i] = f"toolbar icon {k} of {len(grp)}"
    return [Element(k, b, hints[i], rows.get(i, "")) for i, (k, b) in enumerate(kept)]


INK = 60  # summed channel difference from the bar's colour that is an icon's pixel
GAP = 4  # blank columns that part two icons: an icon's own gaps are narrower
ICON_MIN, ICON_MAX = 0.2, 0.8  # an icon's width as a share of the bar's height
MARK = 0.22  # of the bar's height, top and bottom: the running-app marks under the icons


def taskbar_icons(frame: Frame, bar: Box) -> list[Box]:
    """The icons on the bar, left to right: column runs of pixels off the bar's colour,
    parted by a few blank columns, as wide as an icon. The bar's middle band only: the
    marks under running apps would join neighbours."""
    x, y, w, h = bar
    x, y = max(0, x), max(0, y)
    w, h = min(w, frame.width - x), min(h, frame.height - y)
    if w <= 0 or h <= 4:
        return []
    top, bottom = y + int(h * MARK), y + h - int(h * MARK)
    px = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    band = px[top:bottom, x : x + w, :3].astype(np.int32)
    bg = np.median(band.reshape(-1, 3), axis=0)
    ink = (np.abs(band - bg).sum(axis=2) > INK).sum(axis=0)
    cols = np.flatnonzero(ink > 1)
    if not len(cols):
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(cols[0])
    for c in cols[1:]:
        if c - prev > GAP:
            runs.append((start, prev))
            start = int(c)
        prev = int(c)
    runs.append((start, prev))
    out = []
    for a, b in runs:
        if ICON_MIN * h <= b - a + 1 <= ICON_MAX * h:
            out.append((x + a, top, b - a + 1, bottom - top))
    return out
