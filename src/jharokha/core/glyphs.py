"""A terminal read by matching each cell of its character grid against the glyphs seen
before, instead of OCR.

A terminal draws one font at one size on a fixed grid, so every `l` on screen is the same
few hundred pixels as every other `l`, and the RDP codec leaves them nearly untouched
(measured live: the same glyph in two places differs by 0.01, the nearest other glyph is 2
away). Reading is then a nearest-neighbour lookup over about a hundred templates: exact
where OCR confuses `l`, `I` and `1` or `O` and `0`, and tens of milliseconds for a screen
where the second engine took seconds.

Templates are learned from the screen itself, per font and size (a Store per grid pitch and
character width), from a line that prints the printable ASCII range in a known order
(`terminal.glyphs`). Never from the second engine: live it read `O` as `0` in a row Windows
OCR read the same way, and a template learned wrong is wrong for good. A cell no template
is close to (a box-drawing character, an accented letter) is read by the engine for that
run of cells and reported as unknown, never guessed.

Each cell is compared as its ink against its own background (the median colour of its
border ring), so foreground colours and inverse video read as the same glyph. A read whose
cells all sit far from every template means the grid is off by a pixel or a zoom step, not
that the glyphs changed: `refine` searches the phase that brings them back."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from jharokha.core.lazy import LazyNumpy
from jharokha.core.models import Box, Frame, Line, Word
from jharokha.core.rows import _RUN, Grid, Reader, _box_ink, _clip, _ink_params, _long_runs, _pixels

if TYPE_CHECKING:
    import numpy as np
else:
    np = LazyNumpy(globals())

ALPHABET = ("".join(chr(c) for c in range(33, 80)), "".join(chr(c) for c in range(80, 127)))
"""The two lines `terminal.glyphs` prints: every printable ASCII glyph but the space, in
order, 47 to a line."""
COMMAND = {
    "powershell": "-join [char[]](33..79); -join [char[]](80..126)",
    "cmd": 'powershell -c "-join [char[]](33..79); -join [char[]](80..126)"',
    "bash": (
        'awk \'BEGIN{for(i=33;i<80;i++)printf "%c",i;print "";'
        'for(i=80;i<127;i++)printf "%c",i;print ""}\''
    ),
}
"""What prints ALPHABET in each shell."""
KNOWN = 1.5  # L2 distance to the nearest template at or under which a cell is that glyph
# Median distance of a read's inked cells above which the grid is off its cells (a pixel
# moves every glyph past KNOWN), when the read held at least OFF_GRID_CELLS of them.
OFF_GRID = 2.5
OFF_GRID_CELLS = 150
_INK_CELL = 3  # ink pixels that make a cell more than codec noise (as core.rows)


@dataclass(frozen=True)
class Cells:
    """Every cell of a grid on a frame: its ink as a vector (0..1, against the cell's own
    background), and which cells hold ink."""

    grid: Grid
    f: np.ndarray  # (rows, cols, h * w) float32, each cell scaled to its own peak
    inked: np.ndarray  # (rows, cols) bool


def cells(frame: Frame, grid: Grid, box: Box, which: list[int] | None = None) -> Cells:
    """The grid's cells cut from the frame (rows in which, default all). A cell's background
    is the median colour of its border ring, so an inverse-video or highlighted cell reads
    its glyph as any other; the ink threshold is the box's (core.rows._ink_params)."""
    px = _pixels(frame).astype(np.int16)
    bx, by, bw, bh = box
    _, thr = _ink_params(px[by : by + bh, bx : bx + bw].astype(np.uint8))
    h, w = round(grid.pitch), round(grid.char_w)
    rows_ = np.arange(grid.rows) if which is None else np.array(sorted(which), int)
    ys = np.rint(grid.origin_y + rows_ * grid.pitch).astype(int)
    xs = np.rint(grid.origin_x + np.arange(grid.cols) * grid.char_w).astype(int)
    ok_r = (ys >= 0) & (ys + h <= frame.height)
    ok_c = (xs >= 0) & (xs + w <= frame.width)
    f = np.zeros((len(rows_), grid.cols, h * w), np.float32)
    if ok_r.any() and ok_c.any():
        yy = ys[ok_r][:, None] + np.arange(h)[None, :]
        xx = xs[ok_c][:, None] + np.arange(w)[None, :]
        blocks = px[yy[:, :, None, None], xx[None, None, :, :]].transpose(0, 2, 1, 3, 4)
        ring = np.concatenate(
            (blocks[:, :, 0], blocks[:, :, -1], blocks[:, :, :, 0], blocks[:, :, :, -1]), axis=2
        )
        bg = np.median(ring, axis=2)
        diff = np.abs(blocks - bg[:, :, None, None, :]).max(axis=4).astype(np.float32)
        f[np.ix_(np.flatnonzero(ok_r), np.flatnonzero(ok_c))] = diff.reshape(
            diff.shape[0], diff.shape[1], -1
        )
    inked = (f > thr).sum(axis=2) >= _INK_CELL
    _no_thumb(f, inked, thr, h, w)
    peak = np.maximum(f.max(axis=2, keepdims=True), 1.0)
    return Cells(grid, f / peak, inked)


def _no_thumb(f: np.ndarray, inked: np.ndarray, thr: float, h: int, w: int) -> None:
    """A scrollbar's thumb or arrow too short to be a long vertical run (core.rows leaves
    those out of the grid) shows as inked cells in the last two columns after a gap of
    blank cells: not glyphs, so not inked."""
    cols = inked.shape[1]
    edge = max(0, cols - 2)
    for r in np.flatnonzero(inked[:, edge:].any(axis=1)):
        on = np.flatnonzero(inked[r])
        text = on[on < edge]
        before = int(text[-1]) if len(text) else -_THUMB_GAP - 1
        if int(on[on >= edge][0]) - before > _THUMB_GAP:
            inked[r, edge:] = False


_THUMB_GAP = 3  # blank cells before a thin last cell that make it a scrollbar's thumb


@dataclass
class Store:
    """The glyphs known for one font and size: a label per template (one character), and
    the templates as one matrix for the lookup. learned says how: 'alphabet' once
    terminal.glyphs printed every glyph, else 'engine' or '' (nothing yet)."""

    labels: list[str] = field(default_factory=list)
    _t: np.ndarray | None = None
    learned: str = ""
    # The grid the templates were cut on: cells are read on it, never on a grid measured
    # again, since a pixel of difference in the cut puts every glyph past KNOWN.
    grid: Grid | None = None

    def __len__(self) -> int:
        return len(self.labels)

    def add(self, label: str, vec: np.ndarray) -> None:
        self.labels.append(label)
        v = vec[None].astype(np.float32)
        self._t = v if self._t is None else np.vstack((self._t, v))

    def match(self, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """For each vector in f (n, d): the index of the nearest template and its distance;
        no templates: every distance is infinite."""
        if self._t is None or not len(f):
            return np.zeros(len(f), int), np.full(len(f), np.inf, np.float32)
        d = (f * f).sum(axis=1)[:, None] + (self._t * self._t).sum(axis=1)[None, :]
        d -= 2 * f @ self._t.T
        best = d.argmin(axis=1)
        return best, np.sqrt(np.maximum(d[np.arange(len(f)), best], 0))

    def key(self) -> int:
        return len(self.labels)


def store_key(grid: Grid) -> tuple[int, int]:
    """Templates hold for one pitch and character width: a zoom step is another font."""
    return (round(grid.pitch), round(grid.char_w))


def snapped(grid: Grid) -> Grid:
    """The grid on whole pixels: a terminal draws its cells a whole number of pixels apart
    and wide, and a sub-pixel origin or pitch would cut the same glyph differently in two
    columns or two frames. Values within a fifth of a pixel of a whole one are taken as
    it; others (a calibration that says otherwise) stay."""

    def near(v: float) -> float:
        return float(round(v)) if abs(v - round(v)) <= 0.2 else v

    return replace(
        grid,
        origin_y=float(round(grid.origin_y)),
        origin_x=float(round(grid.origin_x)),
        pitch=near(grid.pitch),
        char_w=near(grid.char_w),
    )


@dataclass(frozen=True)
class Read:
    """A grid read: one text per row (None for a row with no ink), the lines, how many
    inked cells no template matched, and per-row digests to skip unchanged rows next time."""

    grid: Grid
    texts: tuple[str | None, ...]
    lines: tuple[Line, ...]
    unknown: int
    digests: tuple[bytes, ...]
    frame_id: int
    store_size: int
    distance: float = 0.0  # the median distance of the inked cells read to their templates


def learn_alphabet(c: Cells, store: Store) -> int:
    """Templates from the two ALPHABET lines on screen: the first two rows holding exactly
    47 inked cells from column 0 on, taken as the two lines in order. Replaces what the
    store had. Returns how many glyphs were learned (0 when the lines are not there)."""
    found = [r for r in range(c.f.shape[0]) if c.inked[r, :47].all() and not c.inked[r, 47:].any()]
    if len(found) < 2:
        return 0
    store.labels, store._t = [], None
    for r, text in zip(found[:2], ALPHABET, strict=True):
        for col, ch in enumerate(text):
            store.add(ch, c.f[r, col])
    store.learned = "alphabet"
    store.grid = c.grid
    return len(store)


def read(
    frame: Frame,
    grid: Grid,
    box: Box,
    store: Store,
    engine: Reader | None = None,
    prev: Read | None = None,
) -> Read:
    """The grid read cell by cell. A run of inked cells no template is close to goes to
    engine (strips in client pixels, as core.rows' reader) when there is one, and its text
    stands in for them: it is never learned, since the engine's misreads (`O` for `0`)
    would be learned with it. prev skips rows whose pixels have not changed."""
    digests = _digests(frame, grid)
    rows_todo = list(range(grid.rows))
    texts: list[str | None] = [None] * grid.rows
    distance = prev.distance if prev is not None else 0.0
    if prev is not None and prev.grid == grid and prev.store_size == len(store):
        for r in range(grid.rows):
            if r < len(prev.digests) and prev.digests[r] == digests[r]:
                texts[r] = prev.texts[r]
                rows_todo.remove(r)
    unknown = 0
    if rows_todo:
        c = cells(frame, grid, box, rows_todo)
        unknown, distance = _read_rows(c, rows_todo, texts, store, frame, engine)
    lines = tuple(_line(grid, r, t or "") for r, t in enumerate(texts))
    return Read(
        grid, tuple(texts), lines, unknown, tuple(digests), frame.frame_id, len(store), distance
    )


def _read_rows(
    c: Cells,
    rows_todo: list[int],
    texts: list[str | None],
    store: Store,
    frame: Frame,
    engine: Reader | None,
) -> tuple[int, float]:
    n_rows, cols, dim = c.f.shape
    flat = c.f.reshape(n_rows * cols, dim)
    inked = c.inked.reshape(-1)
    idx, dist = store.match(flat[inked])
    distance = float(np.median(dist)) if len(dist) >= OFF_GRID_CELLS and len(store) else 0.0
    label = np.full(n_rows * cols, " ", dtype=object)
    known = dist <= KNOWN
    label[np.flatnonzero(inked)[known]] = [store.labels[i] for i in idx[known]]
    unknown_mask = np.zeros(n_rows * cols, bool)
    unknown_mask[np.flatnonzero(inked)[~known]] = True
    label = label.reshape(n_rows, cols)
    unknown_mask = unknown_mask.reshape(n_rows, cols)
    unknown = int(unknown_mask.sum())
    runs: list[tuple[int, int, int]] = []  # row index in c, first col, last col
    for i in range(n_rows):
        on = np.flatnonzero(unknown_mask[i])
        if len(on):
            start = prev_col = int(on[0])
            for col in on[1:]:
                if col - prev_col > 2:  # a blank or a known cell between: another run
                    runs.append((i, start, prev_col))
                    start = int(col)
                prev_col = int(col)
            runs.append((i, start, prev_col))
    if runs and engine is not None:
        g = c.grid
        boxes = [
            (
                max(0, g.col_x(c0 - 1)),
                round(g.origin_y + rows_todo[i] * g.pitch),
                min(frame.width, g.col_x(c1 + 2)) - max(0, g.col_x(c0 - 1)),
                round(g.pitch),
            )
            for i, c0, c1 in runs
        ]
        for (i, c0, c1), text in zip(runs, engine(boxes), strict=True):
            chars = [ch for ch in text if not ch.isspace()]
            cols_in = [col for col in range(c0, c1 + 1) if unknown_mask[i, col]]
            if len(chars) == len(cols_in):
                for col, ch in zip(cols_in, chars, strict=True):
                    label[i, col] = ch
            else:
                # Not one glyph per cell: the engine's text, laid from the run's start.
                for k, ch in enumerate(chars[: c1 - c0 + 1]):
                    label[i, c0 + k] = ch
    for i, r in enumerate(rows_todo):
        texts[r] = "".join(str(x) for x in label[i]).rstrip() if c.inked[i].any() else None
    return unknown, distance


def refine(frame: Frame, grid: Grid, box: Box, store: Store | None = None) -> Grid:
    """The grid moved to where its cells fit the glyphs: with templates (store), to where
    the inked cells of a dozen rows sit nearest to them; without, to where the least ink
    falls on the cell borders, since glyphs sit inside their cells. The origin is searched
    ±4 px down and ±2 px across, whole pixels then halves, the pitch as given and rounded
    (a terminal draws its cells a whole number of pixels apart). The given grid when the
    box holds no ink."""
    if store is not None and len(store):
        return _refine_by_glyphs(frame, grid, box, store)
    bx, by, bw, bh = _clip(box, frame)
    if bw < 4 or bh < 4:
        return grid
    ink = _box_ink(frame, (bx, by, bw, bh))
    ink = ink & ~_long_runs(ink, _RUN * grid.pitch)
    total = float(ink.sum())
    if total < 50:
        return grid
    rowsum, colsum = ink.sum(axis=1), ink.sum(axis=0)
    h, w = round(grid.pitch), round(grid.char_w)

    def border(profile: np.ndarray, start: float, step: float, n: int, size: int) -> float:
        at = np.rint(start + np.arange(n) * step).astype(int)
        at = at[(at >= 0) & (at + size - 1 < len(profile))]
        return float(profile[at].sum() + profile[at + size - 1].sum()) / total

    pitches = [grid.pitch]
    if round(grid.pitch) != grid.pitch:
        pitches.append(float(round(grid.pitch)))
    best_y = (border(rowsum, grid.origin_y - by, grid.pitch, grid.rows, h), grid.pitch, 0.0)
    for pitch in pitches:
        for dy in np.arange(-4.0, 4.5, 0.5):
            c = border(rowsum, grid.origin_y + dy - by, pitch, grid.rows, h)
            if c < best_y[0] - 1e-9:
                best_y = (c, pitch, float(dy))
    best_x = (border(colsum, grid.origin_x - bx, grid.char_w, grid.cols, w), 0.0)
    for dx in np.arange(-2.0, 2.5, 0.5):
        c = border(colsum, grid.origin_x + dx - bx, grid.char_w, grid.cols, w)
        if c < best_x[0] - 1e-9:
            best_x = (c, float(dx))
    _, pitch, dy = best_y
    return replace(
        grid, pitch=pitch, origin_y=grid.origin_y + dy, origin_x=grid.origin_x + best_x[1]
    )


def _refine_by_glyphs(frame: Frame, grid: Grid, box: Box, store: Store) -> Grid:
    probe = cells(frame, grid, box)
    with_ink = [int(r) for r in np.flatnonzero(probe.inked.any(axis=1))][:12]
    if not with_ink:
        return grid

    def cost(g: Grid) -> float:
        c = cells(frame, g, box, with_ink)
        inked = c.inked.reshape(-1)
        if inked.sum() < 10:
            return float("inf")
        _, d = store.match(c.f.reshape(-1, c.f.shape[2])[inked])
        return float(d.mean())

    def search(g: Grid, axis: str, steps: Sequence[float]) -> Grid:
        best = (cost(g), g)
        for step in steps:
            cand = replace(g, **{axis: getattr(g, axis) + step})
            if (cst := cost(cand)) < best[0] - 1e-6:
                best = (cst, cand)
        return best[1]

    best = grid
    pitches = [grid.pitch] + ([float(round(grid.pitch))] if round(grid.pitch) != grid.pitch else [])
    for pitch in pitches:
        g = replace(grid, pitch=pitch)
        g = search(g, "origin_y", [float(d) for d in range(-4, 5) if d])
        g = search(g, "origin_y", [-0.5, 0.5])
        g = search(g, "origin_x", [float(d) for d in range(-2, 3) if d])
        g = search(g, "origin_x", [-0.5, 0.5])
        if cost(g) < cost(best) - 1e-6:
            best = g
    return best


def _digests(frame: Frame, grid: Grid) -> list[bytes]:
    import hashlib

    px = _pixels(frame)
    out = []
    for r in range(grid.rows):
        x, y, w, h = grid.row_box(r)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(frame.width, x + w), min(frame.height, y + h)
        block = px[y0:y1, x0:x1].tobytes() if x1 > x0 and y1 > y0 else b""
        out.append(hashlib.blake2b(block, digest_size=16).digest())
    return out


def _line(grid: Grid, r: int, text: str) -> Line:
    """A row's line, its words boxed by the cells they sit in."""
    _, y, _, h = grid.row_box(r)
    words = []
    col = 0
    for part in text.split(" "):
        if part:
            x0, x1 = grid.col_x(col), grid.col_x(col + len(part))
            words.append(Word(part, (x0, y, x1 - x0, h)))
        col += len(part) + 1
    if not words:
        x, y, w, h = grid.row_box(r)
        return Line("", (x, y, w, h), ())
    x0 = words[0].box[0]
    x1 = words[-1].box[0] + words[-1].box[2]
    return Line(text, (x0, y, x1 - x0, h), tuple(words))
