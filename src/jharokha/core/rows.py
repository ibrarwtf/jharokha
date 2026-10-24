"""A terminal read row by row: its character grid measured from the pixels once, then each
row read as a strip aligned to that grid, with the spaces between columns put back from
where the ink is.

Windows OCR drops whole rows and most of a dark terminal's prompt, and text.grid can only
guess the row pitch from the rows it found. The second engine reads a full-width strip
exactly when the strip holds one row and no part of its neighbours, but drops the spaces
between monospaced columns; the grid gives both the strips and the columns.

The second engine costs 50 to 150 ms a row, so a screen of 46 rows took seconds. A row
whose Windows OCR words put exactly one character on each inked cell of it, and cover them
all, was read well and keeps those words; only the others, and rows a caller needs exact
(`exact`: a command's output, a find's target), go to the second engine, and what it read
is kept per row while the row's pixels stay the same.

A scrollbar, a border or a mark down the right of the body is not text: ink in a vertical
run longer than one and a half rows is left out of every cell, and the grid ends at the
first such run right of all the text, so its columns never reach it and a row is joined to
the next only when its text runs to that edge."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from jharokha.core.lazy import LazyNumpy
from jharokha.core.models import Box, Frame, Line, Word
from jharokha.core.text import _ZERO_IN_NUMBER, _ZEROS, clean

if TYPE_CHECKING:
    import numpy as np
else:
    np = LazyNumpy(globals())

Reader = Callable[[list[Box]], list[str]]

_CARET_AREA = 100  # px^2: a text caret, not a character (as core.watch)
_DIFF = 8  # per channel; less is codec noise
_INK_CELL = 3  # ink pixels that make a cell more than a stray pixel of its neighbours
_FIT = 0.1  # how much worse than the best fit a period may be and still be the period
_RUN = 1.5  # pitches: a vertical run of ink this long is a scrollbar or a border, no glyph
_SCROLL_GAP = 3  # blank cells before a thin last cell that make it a scrollbar's thumb
# Cells the second engine reads in one read, about 1.5 ms each on a 4-core laptop: a full
# terminal of 46 rows is about 2,000, three and a half seconds; this keeps a first read of
# a new screen near a second, and each read after it takes the next rows.
BUDGET = 500
_DASHES = str.maketrans("—–‐‑−一", "------")
# A word of capitals with zeros and no other digit: the zeros are the letter O.
_O_IN_CAPS = re.compile(r"\b(?=[A-Z0]*[A-Z]{2})(?=[A-Z0]*0)[A-Z0]{3,}\b")


@dataclass(frozen=True)
class Grid:
    """A terminal's character grid in client pixels: row r spans origin_y + r * pitch to
    origin_y + (r + 1) * pitch, column c likewise across from origin_x by char_w."""

    origin_y: float
    pitch: float
    origin_x: float
    char_w: float
    rows: int
    cols: int

    def row_box(self, r: int, cols: int | None = None) -> Box:
        """Row r's strip, cols wide from the first column (default all of them)."""
        y0 = round(self.origin_y + r * self.pitch)
        y1 = round(self.origin_y + (r + 1) * self.pitch)
        x0 = round(self.origin_x)
        x1 = round(self.origin_x + (self.cols if cols is None else cols) * self.char_w)
        return (x0, y0, x1 - x0, y1 - y0)

    def col_x(self, c: float) -> int:
        return round(self.origin_x + c * self.char_w)


def measured(
    box: Box, pitch: float | None, char_width: float | None, origin: tuple[int, int] | None
) -> Grid | None:
    """The grid a calibration (apps.toml: pitch, character width, the first cell's top-left
    relative to the region) gives inside box; None unless it gives all three."""
    if not (pitch and char_width and origin):
        return None
    oy, ox = box[1] + origin[1], box[0] + origin[0]
    rows = int((box[1] + box[3] - oy) / pitch + 0.25)
    cols = int((box[0] + box[2] - ox) / char_width + 0.25)
    return Grid(float(oy), float(pitch), float(ox), float(char_width), rows, cols)


def spanning(grid: Grid, box: Box) -> Grid:
    """grid with columns across all of box, for bounded() to end at each frame's edge."""
    return replace(grid, cols=int((box[0] + box[2] - grid.origin_x) / grid.char_w + 0.25))


def row_boxes(grid: Grid, which: Sequence[int] | None = None) -> list[Box]:
    """Full-width strips aligned to the grid, for the rows in which (default all)."""
    return [grid.row_box(r) for r in (range(grid.rows) if which is None else which)]


# ---------------------------------------------------------------------------- calibration


def calibrate(frame: Frame, lines: Sequence[Line], box: Box) -> Grid | None:
    """The grid of the terminal inside box, measured from the frame; None when the box
    holds no text to measure.

    The pitch is the spacing at which every boundary between rows falls where there is
    (almost) no ink. A period half the pitch puts every other boundary through the middle
    of a row, so the smallest period that fits is the pitch, and a blank line between rows
    only leaves a boundary with nothing on either side. Rows OCR missed count as much as
    rows it found: only the pixels are used, and OCR lines only for the scale to search at.
    The character width is found the same way across, origin_y at the row boundary above
    the first text row (moved up a row at a time while it stays in the box), origin_x at
    the column boundary left of the leftmost ink."""
    bx, by, bw, bh = _clip(box, frame)
    if bw < 4 or bh < 4:
        return None
    ink = _box_ink(frame, (bx, by, bw, bh))
    hs = sorted(ln.box[3] for ln in lines if _inside(ln.box, (bx, by, bw, bh)))
    words = [
        w
        for ln in lines
        if _inside(ln.box, (bx, by, bw, bh))
        for w in (ln.words or (Word(ln.text, ln.box),))
    ]
    text_h = hs[len(hs) // 2] if hs else None
    # A scrollbar or a border runs down through every row; no glyph is taller than a few
    # rows.
    across = _drop_runs(ink, axis=0, longest=3 * (text_h or 24)).sum(axis=1)
    if not across.any():
        return None
    if text_h is None:
        text_h = _median_band(across)
    found = _period(across.astype(np.float64), 0.8 * text_h, 2.5 * text_h, gain=10)
    if found is None:
        return None
    pitch, phase_y = found
    edges = _long_runs(ink, _RUN * pitch)
    text = ink & ~edges

    cws = sorted(w.box[2] / len(w.text) for w in words if len(w.text) >= 5)
    if cws:
        cw0 = cws[len(cws) // 2]
        lo, hi = 0.8 * cw0, 1.3 * cw0
    else:
        lo, hi = 0.3 * pitch, 0.8 * pitch
    # A rule of dashes or box-drawing characters fills every column alike.
    down = _drop_runs(text, axis=1, longest=int(4 * hi) + 1).sum(axis=0)
    if not down.any():
        return None
    found = _period(down.astype(np.float64), lo, hi)
    if found is None:
        return None
    char_w, phase_x = found

    top = int(np.flatnonzero(across)[0])
    left = int(np.flatnonzero(down)[0])
    # A glyph may reach a pixel past its cell's edge, so the first cell is the one the
    # first ink is nearest the start of.
    oy = phase_y + np.floor((top - phase_y) / pitch + 0.5) * pitch
    while oy - pitch >= -1:
        oy -= pitch
    ox = phase_x + np.floor((left - phase_x) / char_w + 0.5) * char_w
    rows = int((bh - oy) / pitch + 0.25)
    cols = int((_edge(text, edges, bw) - ox) / char_w + 0.25)
    return Grid(by + float(oy), float(pitch), bx + float(ox), float(char_w), rows, cols)


def _edge(text: np.ndarray, edges: np.ndarray, width: int) -> int:
    """Where the text area ends, in pixels from the box's left: at the first edge (a
    scrollbar, a border, a mark) right of all the text, else at the box's own edge."""
    inked = np.flatnonzero(text.any(axis=0))
    if not len(inked):
        return width
    right = np.flatnonzero(edges.any(axis=0))
    right = right[right > inked[-1]]
    return int(right[0]) if len(right) else width


def bounded(grid: Grid, frame: Frame, box: Box) -> Grid:
    """grid with its columns ending where the text area ends in this frame (_edge), never
    further than they went: a grid from calibration in apps.toml spans its whole region,
    scrollbar included."""
    bx, by, bw, bh = _clip(box, frame)
    if bw < 4 or bh < 4:
        return grid
    ink = _box_ink(frame, (bx, by, bw, bh))
    edges = _long_runs(ink, _RUN * grid.pitch)
    edge = bx + _edge(ink & ~edges, edges, bw)
    cols = int((edge - grid.origin_x) / grid.char_w + 0.25)
    return replace(grid, cols=cols) if 0 < cols < grid.cols else grid


def _period(
    profile: np.ndarray, lo: float, hi: float, gain: float = 1.0
) -> tuple[float, float] | None:
    """The smallest period in lo..hi whose boundaries fall in the profile's gaps, and the
    offset of a boundary (the middle of the gap), sub-pixel.

    Each boundary scores its ink against the most ink near it, times gain, up to 1. Rows
    have true gaps between them, so down the screen a tenth already counts fully (gain
    10): a period that misses the gaps still has to cut every row somewhere, and may find
    only the thin tops of its capitals to cut, while the right one cuts only where a
    descender touches the row below. Across, summed over every row, columns have no true
    gaps, only less ink between the glyphs than through them (gain 1)."""
    n = len(profile)
    lo = max(lo, 3.0)
    hi = min(hi, n / 1.5)
    if hi <= lo:
        return None
    nz = np.flatnonzero(profile)
    first, last = int(nz[0]), int(nz[-1])
    half = max(1, int(lo / 2))
    padded = np.pad(profile, half)
    local = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1).max(axis=1)
    rel = np.where(local > 0, np.minimum(1.0, gain * profile / np.maximum(local, 1e-9)), np.nan)

    def score(p: float, phases: np.ndarray) -> np.ndarray:
        k = np.arange(int((first - p) // p), int((last + p) // p) + 2)
        at = phases[:, None] + k[None, :] * p
        ok = (at >= first - 1) & (at <= last + 1)
        idx = np.clip(np.rint(at).astype(np.intp), 0, n - 1)
        v = np.where(ok, rel[idx], np.nan)
        counted = (~np.isnan(v)).sum(axis=1)
        s = np.nansum(v, axis=1) / np.maximum(counted, 1)
        return np.where(counted >= 2, s, np.inf)

    # Steps fine enough that the lattice is still within an eighth of a period of every gap
    # at the far end, for _refine to find them.
    step = min(0.1, max(0.005, lo * lo / (4 * max(1, last - first))))
    periods = np.arange(lo, hi, step)
    best = np.array([score(p, np.arange(0, p, 0.5)).min() for p in periods])
    if not np.isfinite(best).any():
        return None
    floor_ = best.min()
    fits = np.flatnonzero(best <= floor_ + _FIT)
    # The smallest period that fits, at its best within a few percent: a period a little
    # off still fits over a few rows, and the drift over many tells them apart.
    p0 = periods[fits[0]]
    near = (periods >= p0) & (periods <= p0 * 1.04)
    cand = periods[near][np.argmin(best[near])]
    phases = np.arange(0, cand, 0.25)
    phase = float(phases[int(np.argmin(score(cand, phases)))])
    return _refine(profile, float(cand), phase, first, last)


def _refine(
    profile: np.ndarray, p: float, phase: float, first: int, last: int
) -> tuple[float, float]:
    """The period and offset fitted by least squares to the middle of each gap between
    rows: the least-inked run of pixels near each boundary. Gaps wider than half a period
    are blank lines, whose middle says nothing about where the boundary is."""
    ks, mids = [], []
    k0, k1 = int(np.floor((first - phase) / p)), int(np.ceil((last - phase) / p))
    for k in range(k0, k1 + 1):
        b = phase + k * p
        lo, hi = max(0, int(b - p / 4)), min(len(profile), int(b + p / 4) + 1)
        if hi - lo < 2:
            continue
        seg = profile[lo:hi]
        low = np.flatnonzero(seg == seg.min())
        # The run of lowest pixels that holds the first of them.
        run = low[: 1 + int(np.argmax(np.diff(np.append(low, low[-1] + 2)) > 1))]
        zeros = profile[max(0, lo - int(p)) : hi + int(p)] == 0
        if seg.min() == 0 and zeros.sum() > p:
            continue
        if len(run) > p / 2:
            continue
        ks.append(k)
        mids.append(lo + (run[0] + run[-1]) / 2 + 0.5)
    if len(ks) < 3:
        return p, phase % p
    k, mid = np.array(ks, float), np.array(mids)
    slope, icept = np.polyfit(k, mid, 1)
    # A terminal draws its cells a whole number of pixels apart. Gap middles move a pixel
    # with what is printed above and below them, so a few rows can fit a slope a tenth of
    # a pixel off; the whole number is taken when it fits the gaps about as well.
    whole = round(float(slope))
    w_icept = float(np.mean(mid - k * whole))
    free = np.sqrt(np.mean((mid - (icept + k * slope)) ** 2))
    snapped = np.sqrt(np.mean((mid - (w_icept + k * whole)) ** 2))
    if whole and abs(slope - whole) <= 0.2 and snapped <= free + 0.35:
        return float(whole), w_icept % whole
    return float(slope), float(icept % slope)


def _median_band(profile: np.ndarray) -> float:
    """The typical height of a run of inked rows."""
    on = np.concatenate(([0], (profile > 0).astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(on))
    heights = sorted(edges[1::2] - edges[::2])
    return float(heights[len(heights) // 2]) if heights else 16.0


def _drop_runs(ink: np.ndarray, axis: int, longest: float) -> np.ndarray:
    """ink without its runs along axis longer than longest pixels."""
    a = ink if axis == 0 else ink.T
    out = a & ~_long_runs(a, longest)
    return out if axis == 0 else out.T


def _long_runs(ink: np.ndarray, longest: float) -> np.ndarray:
    """The pixels of ink in runs down a column longer than longest. A window sum finds the
    few columns that hold one; only those are measured run by run: each pixel's run reaches
    from the last blank above it to the first blank below."""
    n = ink.shape[0]
    out = np.zeros_like(ink)
    k = int(longest) + 1
    if n < k:
        return out
    cs = np.zeros((n + 1, ink.shape[1]), np.int32)
    np.cumsum(ink, axis=0, out=cs[1:])
    cols = np.flatnonzero(((cs[k:] - cs[:-k]) == k).any(axis=0))
    if not len(cols):
        return out
    sub = ink[:, cols]
    at = np.arange(1, n + 1, dtype=np.int32)[:, None]
    above = np.maximum.accumulate(np.where(sub, 0, at), axis=0)
    below = np.minimum.accumulate(np.where(sub, n + 1, at)[::-1], axis=0)[::-1]
    out[:, cols] = sub & (below - above - 1 > longest)
    return out


# ------------------------------------------------------------------------------- pixels


_LAST_INK: list[tuple[bytes, Box, np.ndarray]] = []


def _box_ink(frame: Frame, box: Box) -> np.ndarray:
    """_ink of a clipped box, kept for the last frame and box: one read calibrates, bounds
    and measures cells on the same pixels, and each pass over them costs tens of ms."""
    for bgra, b, ink in _LAST_INK:
        if bgra is frame.bgra and b == box:
            return ink
    bx, by, bw, bh = box
    ink = _ink(_pixels(frame)[by : by + bh, bx : bx + bw])
    _LAST_INK[:] = [(frame.bgra, box, ink)]
    return ink


def _pixels(frame: Frame) -> np.ndarray:
    return np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)[:, :, :3]


def _ink(region: np.ndarray) -> np.ndarray:
    """Pixels that differ from the region's background (its commonest colour) by more than
    a third of the strongest contrast in it."""
    return _inked(region, *_ink_params(region))


def _ink_params(region: np.ndarray) -> tuple[np.ndarray, float]:
    """The background colour and the ink threshold, from every third pixel each way:
    enough for a colour that fills most of the region, at a ninth of the work."""
    flat = region[::3, ::3].reshape(-1, 3).astype(np.int32)
    if not len(flat):
        return np.zeros(3, np.int16), 255.0
    packed = flat[:, 0] | (flat[:, 1] << 8) | (flat[:, 2] << 16)
    values, counts = np.unique(packed, return_counts=True)
    bg = int(values[np.argmax(counts)])
    bgc = np.array([bg & 255, (bg >> 8) & 255, bg >> 16], np.int16)
    strong = float(np.percentile(np.abs(flat - bgc).max(axis=1), 99.9))
    return bgc, max(24.0, strong / 3)


def _inked(region: np.ndarray, bgc: np.ndarray, threshold: float) -> np.ndarray:
    return np.abs(region.astype(np.int16) - bgc).max(axis=2) > threshold


def _clip(box: Box, frame: Frame) -> Box:
    x0, y0 = max(0, box[0]), max(0, box[1])
    x1, y1 = min(frame.width, box[0] + box[2]), min(frame.height, box[1] + box[3])
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _inside(b: Box, box: Box) -> bool:
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    return box[0] <= cx < box[0] + box[2] and box[1] <= cy < box[1] + box[3]


@dataclass(frozen=True)
class Cell:
    """The ink in one cell: its top and bottom row, in pixels from the top of the row's
    strip, its width in pixels, and whether the middle of it is inked (a slashed zero is,
    the letter O is not)."""

    top: int
    bottom: int
    width: int
    middle: bool


Ink = Sequence[Sequence[Cell | None]]


def ink_cells(
    frame: Frame, grid: Grid, which: Sequence[int] | None = None, box: Box | None = None
) -> list[list[Cell | None]]:
    """For each row in which (default all), the ink in each of its cells, None where it is
    blank. A cell's sides and the strip's edge pixels are left out: ClearType and the codec
    smear a glyph a pixel or two into the cells beside it, and a descender into the row
    below."""
    which = range(grid.rows) if which is None else which
    px = _pixels(frame)
    x0, x1 = round(grid.origin_x), min(frame.width, round(grid.origin_x + grid.cols * grid.char_w))
    y0 = max(0, round(grid.origin_y))
    y1 = min(frame.height, round(grid.origin_y + grid.rows * grid.pitch))
    if x1 <= x0 or y1 <= y0 or not len(which):
        return [[] for _ in which]
    b = _clip(box, frame) if box is not None else None
    if b is not None and b[0] <= x0 and b[1] <= y0 and x1 <= b[0] + b[2] and y1 <= b[1] + b[3]:
        area = _box_ink(frame, b)[y0 - b[1] : y1 - b[1], x0 - b[0] : x1 - b[0]]
    else:
        area = _ink(px[y0:y1, x0:x1])
    # A border or a mark down the body inside the grid is no character of any row.
    area = area & ~_long_runs(area, _RUN * grid.pitch)
    edges = grid.origin_x + np.arange(grid.cols) * grid.char_w
    inset = 0.2 * grid.char_w
    lefts = np.clip(np.rint(edges + inset).astype(np.intp), 0, frame.width)
    rights = np.clip(np.rint(edges + grid.char_w - inset).astype(np.intp), 0, frame.width)
    rights = np.maximum(rights, lefts + 1)
    wide_l = np.clip(np.rint(edges + 1).astype(np.intp), 0, frame.width)
    wide_r = np.clip(np.rint(edges + grid.char_w - 1).astype(np.intp), 0, frame.width)
    wide_r = np.maximum(wide_r, wide_l + 1)
    out = []
    for r in which:
        _, ry, _, rh = grid.row_box(r)
        strip = np.zeros((max(0, rh - 2), frame.width), bool)
        s0, s1 = max(y0, ry + 1), min(y1, ry + rh - 1)
        if s1 > s0:
            strip[s0 - ry - 1 : s1 - ry - 1, x0:x1] = area[s0 - y0 : s1 - y0]
        cum = np.concatenate(([0], strip.sum(axis=0).cumsum()))
        counts = cum[np.minimum(rights, len(cum) - 1)] - cum[np.minimum(lefts, len(cum) - 1)]
        row: list[Cell | None] = []
        for c, n in enumerate(counts):
            if n < _INK_CELL:
                row.append(None)
                continue
            # The shape over nearly the whole cell: a W's outer strokes reach the top, its
            # middle does not.
            sub = strip[:, wide_l[c] : wide_r[c]]
            ys = np.flatnonzero(sub.any(axis=1))
            xs = np.flatnonzero(sub.any(axis=0))
            top, bottom = int(ys[0]), int(ys[-1])
            span = bottom - top
            mid = sub[
                top + int(0.35 * span) : bottom - int(0.35 * span) + 1,
                max(0, sub.shape[1] // 2 - 1) : sub.shape[1] // 2 + 1,
            ]
            row.append(Cell(top + 1, bottom + 1, int(xs[-1] - xs[0] + 1), bool(mid.any())))
        out.append(_no_thumb(row, grid))
    return out


def _no_thumb(row: list[Cell | None], grid: Grid) -> list[Cell | None]:
    """row without a scrollbar's thumb too short to be a long run: one thin cell in the
    last two columns, after a gap of blank cells."""
    on = [c for c, cell in enumerate(row) if cell]
    if not on or on[-1] < grid.cols - 2:
        return row
    last = on[-1]
    cell = row[last]
    assert cell is not None
    before = on[-2] if len(on) > 1 else -_SCROLL_GAP - 1
    if last - before > _SCROLL_GAP and cell.width <= 0.6 * grid.char_w:
        row = list(row)
        row[last] = None
    return row


def changed_rows(old: Frame | None, new: Frame, grid: Grid) -> list[int]:
    """Rows whose pixels differ between the frames, all of them when there is no old frame
    or its size differs. A change smaller than a caret inside one cell is not a change."""
    if old is None or (old.width, old.height) != (new.width, new.height):
        return list(range(grid.rows))
    if old.bgra == new.bgra:
        return []
    a, b = _pixels(old), _pixels(new)
    out = []
    for r in range(grid.rows):
        x, y, w, h = _clip(grid.row_box(r), new)
        if not w or not h:
            continue
        d = np.abs(a[y : y + h, x : x + w].astype(np.int16) - b[y : y + h, x : x + w]).max(axis=2)
        ys, xs = np.nonzero(d > _DIFF)
        if not len(xs):
            continue
        cx0 = int((xs.min() + x - grid.origin_x) // grid.char_w)
        cx1 = int((xs.max() + x - grid.origin_x) // grid.char_w)
        area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
        if cx0 == cx1 and area <= _CARET_AREA:
            continue
        out.append(r)
    return out


# ----------------------------------------------------------------------------- assembly


def assemble(
    grid: Grid,
    row_texts: Sequence[str | None],
    ocr_lines: Sequence[Line] = (),
    ink: Ink | None = None,
) -> list[Line]:
    """One line per row: the second engine's text (row_texts[r], None where it was not
    read), laid on the row's ink cells so each character lands in a column and the blank
    cells between them are spaces again. Windows OCR words in the row anchor characters to
    columns where they agree, and stand in for a row the engine did not read. Then the
    shape of each cell's ink settles what a strip of one row cannot show the engine (see
    _by_shape). Without ink, the engine's text is taken as it is. Blank rows stay, as
    empty lines."""
    by_row = _windows_chars(grid, ocr_lines, ink)
    laid: list[list[str]] = []
    for r in range(grid.rows):
        cells = list(ink[r]) if ink is not None and r < len(ink) else None
        text = row_texts[r] if r < len(row_texts) else None
        win = by_row.get(r, {})
        if cells is not None and not any(cells):
            chars = []
        elif text is None:
            chars = _from_windows(win)
        elif cells is None:
            chars = list(_engine_text(text))
        else:
            chars = _lay(_engine_text(text), cells, win, grid.char_w)
        laid.append(chars)
    if ink is not None:
        _by_shape(laid, ink, grid)
    out = []
    for r, chars in enumerate(laid):
        line = "".join(chars).rstrip()
        line = _ZERO_IN_NUMBER.sub(lambda m: _ZEROS.sub("0", m.group()), line)
        out.append(_line(grid, r, line))
    return out


_CAPS = set("ABDEFGHIJKLMNQRTY123456789")  # no small letter of the same shape
_SMALL = set("aemnr")  # x-height, no ascender or descender
# Small with no ascender, so their case shows in how high the ink starts. Both engines flip
# them in file names (`Readme`, `appsettings.json`), not only those of one shape in both
# cases (c, o, s, ...).
_CASED = set("acegmnopqrsuvwxyzACEGMNOPQRSUVWXYZ")


def _by_shape(laid: list[list[str]], ink: Ink, grid: Grid) -> None:
    """Characters the engine cannot tell apart in a strip of one row, settled from where
    the ink sits in the cell, measured against the screen's own capitals and small
    letters: a letter whose small form has no ascender small or capital by the height of
    its top;
    `-` or `_` by the height of a thin bar, also in a cell the engine read nothing for,
    where a tall narrow stroke is `|`; O or 0 by whether the middle is inked."""
    caps, small, base = [], [], []
    for chars, cells in zip(laid, ink, strict=False):
        for ch, cell in zip(chars, cells, strict=False):
            if cell is None:
                continue
            if ch in _CAPS:
                caps.append(cell.top)
                base.append(cell.bottom)
            elif ch in _SMALL:
                small.append(cell.top)
                base.append(cell.bottom)
    cap_top = float(np.median(caps)) if caps else None
    x_top = float(np.median(small)) if small else None
    baseline = float(np.median(base)) if base else 0.78 * grid.pitch
    bar = max(2.0, 0.12 * grid.pitch)
    for chars, cells in zip(laid, ink, strict=False):
        for c, cell in enumerate(cells[: len(chars)]):
            ch = chars[c]
            if cell is None or len(ch) != 1:
                continue
            thin = cell.bottom - cell.top + 1 <= bar and cell.width >= 0.3 * grid.char_w
            # I and l have serifs or a tail; only | is a bare stroke.
            if (
                ch in " Il"
                and cell.width <= 0.3 * grid.char_w
                and cell.bottom - cell.top >= 0.6 * (baseline - (cap_top or 0))
            ):
                chars[c] = "|"
            elif thin and ch in "-_ ":
                low = (cell.top + cell.bottom) / 2 > baseline - 0.1 * grid.pitch
                chars[c] = "_" if low else "-"
            elif cap_top is not None and x_top is not None and x_top - cap_top >= 1.5:
                tall = cell.top - cap_top < (x_top - cap_top) / 2
                if ch in _CASED:
                    chars[c] = ch.upper() if tall else ch.lower()
                if chars[c] in "0Oo" and tall:
                    chars[c] = "0" if cell.middle else "O"


def _engine_text(text: str) -> str:
    """The engine's text in the characters a terminal shows: it was trained on Chinese as
    well, and reads a dash or a colon as their full-width forms, and the letter O between
    capitals as a zero (`INF0`, `ERR0R`; Cascadia's zero is slashed)."""
    s = unicodedata.normalize("NFKC", clean(text)).translate(_DASHES)
    return _O_IN_CAPS.sub(lambda m: m.group().replace("0", "O"), s)


def _line(grid: Grid, r: int, text: str) -> Line:
    """A row's line, its words boxed by the cells they sit in."""
    _, y, _, h = grid.row_box(r)
    words = []
    c = 0
    for part in text.split(" "):
        if part:
            x0, x1 = grid.col_x(c), grid.col_x(c + len(part))
            words.append(Word(part, (x0, y, x1 - x0, h)))
        c += len(part) + 1
    if not words:
        x, y, w, h = grid.row_box(r)
        return Line("", (x, y, w, h), ())
    x0 = words[0].box[0]
    x1 = words[-1].box[0] + words[-1].box[2]
    return Line(text, (x0, y, x1 - x0, h), tuple(words))


def _windows_chars(grid: Grid, lines: Sequence[Line], ink: Ink | None) -> dict[int, dict[int, str]]:
    """Windows OCR's characters by row and column. A word's characters go on the inked
    cells under it when there are as many; otherwise only its first character is placed,
    on the first cell under it."""
    out: dict[int, dict[int, str]] = {}
    for ln in lines:
        for w in ln.words or (Word(ln.text, ln.box),):
            r = int((w.box[1] + w.box[3] / 2 - grid.origin_y) // grid.pitch)
            if not 0 <= r < grid.rows:
                continue
            chars = [ch for ch in clean(w.text) if not ch.isspace()]
            if not chars:
                continue
            c0 = max(0, round((w.box[0] - grid.origin_x) / grid.char_w))
            c1 = min(grid.cols, round((w.box[0] + w.box[2] - grid.origin_x) / grid.char_w))
            row = out.setdefault(r, {})
            cells = ink[r] if ink is not None and r < len(ink) else None
            under = [c for c in range(c0, c1) if cells is None or (c < len(cells) and cells[c])]
            if len(under) == len(chars):
                row.update(zip(under, chars, strict=True))
            elif c0 < grid.cols:
                row.setdefault(under[0] if under else c0, chars[0])
                if cells is None:
                    row.update((c0 + i, ch) for i, ch in enumerate(chars) if c0 + i < grid.cols)
    return out


def _from_windows(win: dict[int, str]) -> list[str]:
    """A row from Windows OCR alone: its characters in their columns."""
    if not win:
        return []
    out = [" "] * (max(win) + 1)
    for c, ch in win.items():
        out[c] = ch
    return out


def _lay(text: str, cells: list[Cell | None], win: dict[int, str], char_w: float) -> list[str]:
    """The text's characters on the row's cells. When the engine read one character per
    inked cell (the usual case), they fill the inked cells in order. Otherwise an alignment
    decides which cells a missing character leaves empty, or where an extra one goes: the
    engine's own spaces prefer blank cells and Windows OCR's characters vote for theirs."""
    last = max(i for i, on in enumerate(cells) if on)
    cells = cells[: last + 1]
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) == sum(c is not None for c in cells):
        it = iter(chars)
        out = [next(it) if on else " " for on in cells]
    else:
        out = _align(text, cells, win, char_w)
    # The engine reads a capital P before digits as small (`p07`), which Windows OCR gets
    # right; the other way round Windows OCR is the one that is wrong (`PS c:\`).
    for c, w in win.items():
        if c < len(out) and w.isupper() and out[c] == w.lower():
            out[c] = w
    return out


_NARROW = set("|!il.,:;'`I1j")


def _align(text: str, cells: list[Cell | None], win: dict[int, str], char_w: float) -> list[str]:
    tokens: list[str] = []
    for ch in text.strip():
        if ch.isspace():
            if tokens and tokens[-1] != " ":
                tokens.append(" ")
        else:
            tokens.append(ch)
    n, m = len(tokens), len(cells)
    inf = float("inf")
    # cost[i][j]: the first i tokens laid on the first j cells; back[i][j] how.
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    # An inked cell left empty: one at the edge of a word or alone (punctuation the engine
    # skipped) is likelier than one inside a word.
    skip_cell = [
        0.0
        if not on
        else 1.0
        if (c == 0 or not cells[c - 1]) or (c == m - 1 or not cells[c + 1])
        else 1.2
        for c, on in enumerate(cells)
    ]
    for i in range(n + 1):
        t = tokens[i - 1] if i else ""
        # A token on no cell; the same character as the one before is CTC reading one glyph
        # twice.
        unplaced = 0.1 if t == " " else 0.5 if i >= 2 and tokens[i - 2] == t else 1.0
        row, up = cost[i], cost[i - 1] if i else None
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, how = inf, 0
            if j and row[j - 1] + skip_cell[j - 1] < best:  # cell left empty
                best, how = row[j - 1] + skip_cell[j - 1], 1
            if up is not None:
                if up[j] + unplaced < best:
                    best, how = up[j] + unplaced, 2
                if j:
                    on = cells[j - 1]
                    if t == " ":
                        c = 0.0 if not on else 1.5
                    elif on is None:
                        c = 2.0
                    else:
                        c = 0.0 if win.get(j - 1) == t else 0.3
                        if on.width <= 0.3 * char_w and t not in _NARROW:
                            c += 0.5  # a letter on a cell with one thin stroke in it
                    if up[j - 1] + c < best:
                        best, how = up[j - 1] + c, 3
            row[j], back[i][j] = best, how
    placed: dict[int, str] = {}
    extra: dict[int, str] = {}  # characters with no cell, after the cell before them
    i, j = n, m
    while i or j:
        how = back[i][j]
        if how == 1:
            j -= 1
        elif how == 2:
            t = tokens[i - 1]
            # The same character again is CTC reading one glyph twice (`-----` for `----`).
            twice = (i >= 2 and tokens[i - 2] == t) or (i < n and tokens[i] == t)
            if t != " " and not twice:
                extra[j - 1] = t + extra.get(j - 1, "")
            i -= 1
        else:
            if tokens[i - 1] != " ":
                placed[j - 1] = tokens[i - 1]
            i, j = i - 1, j - 1
    out = []
    for c in range(m):
        # An inked cell the engine read nothing for: Windows OCR's character, if any. The
        # engine skips a lone `|`, which Windows OCR reads as a letter.
        empty = win.get(c, " ") if cells[c] else " "
        alone = (c == 0 or not cells[c - 1]) and (c == m - 1 or not cells[c + 1])
        if alone and empty in "Il1":
            empty = "|"
        out.append(placed.get(c, empty) + extra.get(c, ""))
    if -1 in extra and out:
        out[0] = extra[-1] + out[0]
    return out


def unwrap(lines: Sequence[Line], grid: Grid) -> list[Line]:
    """Rows joined where a line ran on past the right edge: a row inked up to its last
    column continues on the next one. The last column, as the grid counts it, may be the
    cell of padding after the terminal's own last one."""
    out: list[Line] = []
    wrapped = False
    for ln in lines:
        if wrapped and out and ln.text:
            last = out[-1]
            words = last.words + ln.words
            x0 = min(w.box[0] for w in words)
            y0 = min(w.box[1] for w in words)
            x1 = max(w.box[0] + w.box[2] for w in words)
            y1 = max(w.box[1] + w.box[3] for w in words)
            out[-1] = Line(last.text + ln.text, (x0, y0, x1 - x0, y1 - y0), words)
        else:
            out.append(ln)
        wrapped = len(ln.text) >= grid.cols - 1
    return out


# --------------------------------------------------------------------------------- read


@dataclass(frozen=True)
class RowRead:
    """A read of a grid: what the engine read on each row (None for a blank row or one
    Windows OCR read well), the lines, and the frame, per-row pixel digests and ink, for
    reading only what changes next time. base is the grid asked for, grid the one read:
    base with its columns ending at this frame's text edge (bounded)."""

    frame: Frame
    grid: Grid
    texts: tuple[str | None, ...]
    lines: tuple[Line, ...]
    digests: tuple[bytes, ...]
    ink: tuple[tuple[Cell | None, ...], ...]
    base: Grid | None = None
    pending: int = 0  # inked rows left to Windows OCR by the budget: the next read takes them


# TODO(live 2026-10-24): the first read of a new terminal frame re-reads every inked row with
# the second engine (~7 s, 16 s CPU on a live Windows Terminal; status and terminal.run felt it).
# Read only the rows Windows OCR got wrong or the caller needs (a run's new rows), and verify
# the fix on the live session before trusting the generated screens.
def read_rows(
    frame: Frame,
    box: Box,
    reader: Reader,
    grid: Grid | None = None,
    ocr_lines: Sequence[Line] = (),
    prev: RowRead | None = None,
    exact: Box | None = None,
    budget: int = BUDGET,
) -> RowRead | None:
    """The grid inside box read row by row: one line per row, blank rows included, so line
    numbers are the terminal's rows. reader reads strips (boxes in client pixels) and
    returns their text. Without a grid it is calibrated from this frame (None when the box
    holds no text). Every row inside exact goes to the reader; then, bottom up, rows
    Windows OCR (ocr_lines) read poorly, then the others, while their cells fit in budget;
    a row left over shows Windows OCR's words and counts as pending. With the previous
    read, only rows whose pixels changed or are pending are read, and a row that only
    moved (the terminal scrolled) keeps the text read where it was."""
    base = grid or calibrate(frame, ocr_lines, box)
    if base is None:
        return None
    if prev is not None and prev.base == base and prev.frame.bgra == frame.bgra:
        wanted = _rows_in(prev.grid, exact)
        done = all(prev.texts[r] is not None or not any(prev.ink[r]) for r in wanted)
        if done and not prev.pending:
            return prev
    grid = bounded(base, frame, box)
    digests = _digests(frame, grid)
    texts: list[str | None] = [None] * grid.rows
    known: dict[bytes, str | None] = {}
    seen: dict[bytes, tuple[Cell | None, ...]] = {}
    if prev is not None and prev.grid == grid:
        known = dict(zip(prev.digests, prev.texts, strict=True))
        seen = dict(zip(prev.digests, prev.ink, strict=True))
        same = set(range(grid.rows)) - set(changed_rows(prev.frame, frame, grid))
        for r in same:
            if r < len(prev.texts):
                known.setdefault(digests[r], prev.texts[r])
    fresh = [r for r in range(grid.rows) if digests[r] not in seen]
    measured = dict(zip(fresh, ink_cells(frame, grid, fresh, box), strict=True))
    ink = [tuple(measured[r]) if r in measured else seen[digests[r]] for r in range(grid.rows)]
    words = _row_words(grid, ocr_lines)
    wanted = _rows_in(grid, exact)
    must, poor, rest = [], [], []
    for r in reversed(range(grid.rows)):  # the newest output, at the bottom, first
        if not any(ink[r]):
            continue
        if digests[r] in known and known[digests[r]] is not None:
            texts[r] = known[digests[r]]
        elif r in wanted:
            must.append(r)
        elif not _read_well(ink[r], words.get(r, [])) or any(
            _doubtful(chars) for _, _, chars in words.get(r, [])
        ):
            poor.append(r)
        else:
            rest.append(r)
    todo, cost = list(must), sum(_cells(ink[r]) for r in must)
    for r in poor + rest:
        if cost + _cells(ink[r]) > budget and (todo or cost):
            break
        todo.append(r)
        cost += _cells(ink[r])
    if todo:
        boxes = [_strip(grid, frame, r, ink[r]) for r in todo]
        for r, t in zip(todo, reader(boxes), strict=True):
            texts[r] = t
    pending = sum(1 for r in poor + rest if texts[r] is None)
    lines = assemble(grid, texts, ocr_lines, ink)
    return RowRead(
        frame, grid, tuple(texts), tuple(lines), tuple(digests), tuple(ink), base, pending
    )


def _cells(cells: Sequence[Cell | None]) -> int:
    """How many cells the engine reads for a row: up to its last ink, about what it costs."""
    return 2 + max((i for i, on in enumerate(cells) if on), default=0)


def _strip(grid: Grid, frame: Frame, r: int, cells: Sequence[Cell | None]) -> Box:
    """Row r's strip for the engine, to a cell past its last ink: a cell of margin each
    side, since the engine reads the first and last glyph worse when they touch the edge."""
    last = max(i for i, on in enumerate(cells) if on)
    _, y, _, h = grid.row_box(r)
    x0 = max(0, round(grid.origin_x - grid.char_w))
    x1 = min(frame.width, grid.col_x(min(grid.cols, last + 2)))
    return (x0, y, x1 - x0, h)


_LOOKS_DIGIT = set("OoIlSBZGUDQq|")


def _doubtful(chars: str) -> bool:
    """A word Windows OCR may have read with a lookalike in it at the right length: letters
    and digits mixed (a hash, an ID), a character no terminal output has, or one letter that
    looks like a digit alone (`-U`)."""
    alnum = [c for c in chars if c.isalnum()]
    if any(not c.isascii() for c in chars) or "@" in chars:
        return True
    if any(c.isdigit() for c in alnum) and any(c.isalpha() for c in alnum):
        return True
    return len(alnum) == 1 and alnum[0] in _LOOKS_DIGIT


def _rows_in(grid: Grid, box: Box | None) -> set[int]:
    """The rows whose middle lies inside box."""
    if box is None:
        return set()
    out = set()
    for r in range(grid.rows):
        x, y, w, h = grid.row_box(r)
        mid = y + h / 2
        if box[1] <= mid < box[1] + box[3] and x < box[0] + box[2] and box[0] < x + w:
            out.add(r)
    return out


Placed = tuple[int, int, str]  # a word's first column, the column past its end, its characters


def _row_words(grid: Grid, lines: Sequence[Line]) -> dict[int, list[Placed]]:
    """Windows OCR's words by the row their middle is on, with the columns they span; words
    outside the grid's columns (a scrollbar read as `|`) are left out."""
    out: dict[int, list[Placed]] = {}
    for ln in lines:
        for w in ln.words or (Word(ln.text, ln.box),):
            if at := _place(grid, w):
                r, c0, c1, chars = at
                out.setdefault(r, []).append((c0, c1, chars))
    return out


def _place(grid: Grid, w: Word) -> tuple[int, int, int, str] | None:
    """A word's row, first column, the column past its end and its characters; None
    outside the grid."""
    right = grid.origin_x + grid.cols * grid.char_w
    if not grid.origin_x - grid.char_w / 2 <= w.box[0] + w.box[2] / 2 < right:
        return None
    r = int((w.box[1] + w.box[3] / 2 - grid.origin_y) // grid.pitch)
    chars = "".join(ch for ch in clean(w.text) if not ch.isspace())
    if not 0 <= r < grid.rows or not chars:
        return None
    c0 = max(0, round((w.box[0] - grid.origin_x) / grid.char_w))
    c1 = min(grid.cols, round((w.box[0] + w.box[2] - grid.origin_x) / grid.char_w))
    return r, c0, c1, chars


def _read_well(cells: Sequence[Cell | None], words: Collection[Placed]) -> bool:
    """Windows OCR's words put one character on each inked cell under them, and leave no
    inked cell of the row without one: nothing dropped, split or run together. What is
    left to doubt (case, 0 and O) the cells' shapes settle as for the engine's reads."""
    inked = {c for c, cell in enumerate(cells) if cell}
    covered: set[int] = set()
    for c0, c1, chars in words:
        under = [c for c in range(c0, c1) if c in inked]
        if len(under) != len(chars):
            return False
        covered.update(under)
    return bool(inked) and covered == inked


def _digests(frame: Frame, grid: Grid) -> list[bytes]:
    px = _pixels(frame)
    out = []
    for r in range(grid.rows):
        x, y, w, h = _clip(grid.row_box(r), frame)
        out.append(hashlib.blake2b(px[y : y + h, x : x + w].tobytes(), digest_size=16).digest())
    return out
