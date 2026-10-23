"""Panes from pixels: the boxes an app draws its parts in, found from the 1 to 2 px divider
lines between them and the edges of their background colours, without any text. Text gaps
cannot see these: a Teams chat list and its thread are separated only by background colour
(245 against 255), and a list that runs up to its divider leaves no gap at all."""

from __future__ import annotations

import numpy as np

from jharokha.core.models import Box, Frame

# Green between pixels two apart; two apart so a boundary the codec smeared over two pixels
# still counts. Green alone carries most of the brightness and costs a third of mixing all
# three channels. Light themes put panes 3 apart (a taskbar at 238 under a rail at 235);
# the codec leaves a flat background flat, and a divider needs 90% of a run anyway.
_STEP = 3
# A divider runs along every pixel, so every 4th row (for one running down) or column (for
# one running across) shows it as well as all of them, at a quarter of the work.
_SAMPLE = 4
_SPAN = 0.9  # of the pane's height (or width) a divider must run, so text never makes one
_TIE = 0.02  # coverage the codec can take off a divider; closer than this is a tie
_MIN_RUN = 40  # px; shorter regions are not split, as glyph strokes could fill them
_MIN = 8  # px; a cut leaves both sides at least this wide
_CLUSTER = 3  # px; hits this close are one divider (both edges of a line, a smeared edge)


def panes(frame: Frame, bounds: Box | None = None) -> list[Box]:
    """Panes inside bounds (default the frame) in reading order, each split again inside:
    bars across the top and bottom, side-by-side columns, the header above a list."""
    bx, by, bw, bh = bounds or (0, 0, frame.width, frame.height)
    a = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    g = a[by : by + bh, bx : bx + bw, 1]
    # down[i, x]: a change across column x on sampled row i; across[y, j]: a change down
    # through row y on sampled column j.
    rows = g[::_SAMPLE].astype(np.int16)
    down = np.zeros(rows.shape, np.uint8)
    down[:, 1:-1] = np.abs(rows[:, 2:] - rows[:, :-2]) >= _STEP
    cols = g[:, ::_SAMPLE].astype(np.int16)
    across = np.zeros(cols.shape, np.uint8)
    across[1:-1] = np.abs(cols[2:] - cols[:-2]) >= _STEP
    found = _split((0, 0, g.shape[1], g.shape[0]), down, across)
    return [(x + bx, y + by, w, h) for x, y, w, h in found]


def background(frame: Frame) -> tuple[int, int, int]:
    """The most common colour (RGB) on a sparse grid of pixels: what most of the screen is
    painted with, such as a terminal's (12, 12, 12)."""
    a = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    s = a[::8, ::8, :3].reshape(-1, 3).astype(np.int32)
    # The codec moves a flat colour by a level or two; count colours in steps of 4.
    packed = ((s[:, 2] >> 2) << 12) | ((s[:, 1] >> 2) << 6) | (s[:, 0] >> 2)
    values, counts = np.unique(packed, return_counts=True)
    top = packed == values[int(counts.argmax())]
    r, g, b = (int(np.median(s[top, i])) for i in (2, 1, 0))
    return r, g, b


def reading_panes(frame: Frame, region: Box | None = None) -> list[Box] | None:
    """The panes to read the frame (or a region of it) in: the whole frame's panes, cut to
    the region so a region reads as it does on the full screen. None when the frame shows
    no panes, for reading by text gaps."""
    found = panes(frame)
    if len(found) < 2:
        return None
    return within(found, region) if region else found


def within(panes: list[Box], region: Box) -> list[Box]:
    """The parts of panes inside region."""
    rx, ry, rw, rh = region
    out = []
    for x, y, w, h in panes:
        x0, y0 = max(x, rx), max(y, ry)
        x1, y1 = min(x + w, rx + rw), min(y + h, ry + rh)
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def _split(box: Box, down: np.ndarray, across: np.ndarray) -> list[Box]:
    """Cut at the dividers that run the full height or width of box, then split each piece
    again. Columns go first when a full-height divider runs at least as completely as the
    best full-width one: a sidebar's divider beats a header line that stops at it, and a
    chat list stays together with its own header rather than the thread's."""
    x, y, w, h = box
    s0, s1 = -(-y // _SAMPLE), -(-(y + h) // _SAMPLE)  # sampled rows inside box
    c0, c1 = -(-x // _SAMPLE), -(-(x + w) // _SAMPLE)
    by_col = by_row = None
    if h >= _MIN_RUN and s1 > s0:
        by_col = down[s0:s1, x : x + w].sum(axis=0, dtype=np.uint16) / (s1 - s0)
    if w >= _MIN_RUN and c1 > c0:
        by_row = across[y : y + h, c0:c1].sum(axis=1, dtype=np.uint16) / (c1 - c0)
    for cover in (by_col, by_row):
        # A piece starts and ends on the divider it was cut at; its edges are not new ones.
        if cover is not None:
            cover[:_MIN] = 0
            cover[-_MIN:] = 0
    col_best = float(by_col.max()) if by_col is not None and by_col.size else 0.0
    row_best = float(by_row.max()) if by_row is not None and by_row.size else 0.0
    if col_best >= _SPAN and col_best >= row_best - _TIE:
        assert by_col is not None
        edges = [x, *_cuts(by_col >= col_best - _TIE, x, w), x + w]
        pieces = [(a, y, b - a, h) for a, b in zip(edges, edges[1:], strict=False)]
    elif row_best >= _SPAN:
        assert by_row is not None
        edges = [y, *_cuts(by_row >= row_best - _TIE, y, h), y + h]
        pieces = [(x, a, w, b - a) for a, b in zip(edges, edges[1:], strict=False)]
    else:
        return [box]
    if len(pieces) == 1:
        return pieces
    return [p for piece in pieces for p in _split(piece, down, across)]


def _cuts(hit: np.ndarray, start: int, length: int) -> list[int]:
    """Where to cut: the middle of each cluster of hits, at least _MIN from the ends and
    from the cut before."""
    out: list[int] = []
    idx = np.flatnonzero(hit)
    if not len(idx):
        return out
    breaks = np.flatnonzero(np.diff(idx) > _CLUSTER)
    for first, last in zip(
        np.r_[idx[0], idx[breaks + 1]], np.r_[idx[breaks], idx[-1]], strict=True
    ):
        at = int(first + last + 1) // 2
        if _MIN <= at <= length - _MIN and (not out or start + at - out[-1] >= _MIN):
            out.append(start + at)
    return out
