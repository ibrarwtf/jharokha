"""A terminal's character grid, measured from the pixels: the row pitch, the character
width and where the first cell sits, so core.glyphs can cut the screen into cells.

Windows OCR drops whole rows and most of a dark terminal's prompt, and the words it finds
can only guess the row pitch; the pixels give it exactly. A scrollbar, a border or a mark
down the right of the body is not text: ink in a vertical run longer than one and a half
rows is left out, and the grid ends at the first such run right of all the text, so its
columns never reach it and a row is joined to the next only when its text runs to that
edge."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from jharokha.core.lazy import LazyNumpy
from jharokha.core.models import Box, Frame, Line, Word
from jharokha.core.models import centre_in as _inside

if TYPE_CHECKING:
    import numpy as np
else:
    np = LazyNumpy(globals())

Reader = Callable[[list[Box]], list[str]]

_FIT = 0.1  # how much worse than the best fit a period may be and still be the period
_RUN = 1.5  # pitches: a vertical run of ink this long is a scrollbar or a border, no glyph


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
