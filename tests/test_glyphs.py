"""core/glyphs.py on a synthetic terminal: a made-up monospaced font (each glyph a fixed
random dot pattern, so any two glyphs differ) drawn on a grid, read back by matching."""

from __future__ import annotations

import random
import time

import numpy as np
import pytest

from jharokha.core import glyphs
from jharokha.core.models import Frame
from jharokha.core.rows import Grid

CW, PITCH = 10, 18
OX, OY = 6, 4
COLS, ROWS = 60, 20
W, H = 640, 400
BG = (12, 12, 12)
INK = (204, 204, 204)


def _font() -> dict[str, np.ndarray]:
    """One 8 x 15 dot pattern per printable glyph, a pixel inside its 10 x 18 cell."""
    rng = random.Random(7)
    out = {}
    for code in range(33, 127):
        pat = np.zeros((15, 8), bool)
        for _ in range(24):
            pat[rng.randrange(15), rng.randrange(8)] = True
        out[chr(code)] = pat
    return out


FONT = _font()


def draw(
    rows: list[str],
    ink: tuple[int, int, int] = INK,
    inverse_rows: frozenset[int] = frozenset(),
    dy: int = 0,
    extra: dict[str, np.ndarray] | None = None,
) -> Frame:
    """rows of text on the grid; inverse_rows draw ink on a light cell; dy shifts the whole
    picture down (a grid that no longer fits)."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    font = {**FONT, **(extra or {})}
    for r, text in enumerate(rows):
        for c, ch in enumerate(text):
            if ch == " " or ch not in font:
                continue
            y, x = OY + r * PITCH + dy, OX + c * CW
            if r in inverse_rows:
                img[y : y + PITCH, x : x + CW] = (235, 235, 235)
                fg = (10, 10, 10)
            else:
                fg = ink
            pat = font[ch]
            ys, xs = np.nonzero(pat)
            img[y + 1 + ys, x + 1 + xs] = fg
    bgra = np.dstack([img[..., 2], img[..., 1], img[..., 0], np.full((H, W), 255, np.uint8)])
    return Frame(W, H, bgra.tobytes(), 1, time.time())


GRID = Grid(float(OY), float(PITCH), float(OX), float(CW), ROWS, COLS)
BOX = (0, 0, W, H)
ALPHA_ROWS = [glyphs.ALPHABET[0], glyphs.ALPHABET[1]]


def learned() -> glyphs.Store:
    store = glyphs.Store()
    n = glyphs.learn_alphabet(glyphs.cells(draw(ALPHA_ROWS), GRID, BOX), store)
    assert n == 94 and store.learned == "alphabet" and store.grid == GRID
    return store


def texts(frame: Frame, store: glyphs.Store, **kw) -> list[str]:
    got = glyphs.read(frame, GRID, BOX, store, **kw)
    return [t or "" for t in got.texts]


def test_the_alphabet_teaches_every_glyph_and_reads_back_exactly():
    store = learned()
    rows = ["PS C:\\x> Get-Item 0O1lI5S8B2Z6G", "", "  a.b/c\\d --f=1 {x:1} [y] |z| ~`^"]
    got = texts(draw(rows), store)
    assert got[:3] == rows
    assert glyphs.read(draw(rows), GRID, BOX, store).unknown == 0


def test_colour_and_inverse_video_read_as_the_same_glyphs():
    store = learned()
    rows = ["yellow 0O1lI5S8", "inverse 0O1lI5S8", "red path/to/file.txt"]
    frame = draw(rows, ink=(240, 200, 40), inverse_rows=frozenset({1}))
    assert texts(frame, store)[:3] == rows


def test_an_unknown_glyph_is_flagged_and_read_by_the_engine_not_learned():
    store = learned()
    box_glyph = np.zeros((15, 8), bool)
    box_glyph[7, :] = True
    box_glyph[:, 3] = True
    frame = draw(["wide + box + end"], extra={"+": box_glyph})
    calls: list[list] = []

    def engine(boxes):
        calls.append(boxes)
        return ["┼"] * len(boxes)

    got = glyphs.read(frame, GRID, BOX, store, engine)
    assert got.unknown == 2 and len(calls[0]) == 2
    assert got.texts[0] == "wide ┼ box ┼ end"
    assert len(store) == 94 and store.learned == "alphabet"  # the engine teaches nothing


def test_a_scrollbar_thumb_in_the_last_column_is_not_a_glyph():
    store = learned()
    frame = draw(["abc"])
    px = np.frombuffer(frame.bgra, np.uint8).reshape(H, W, 4).copy()
    x = OX + (COLS - 1) * CW + 4  # a 2 px wide bar down the last column, two rows tall
    px[OY : OY + 2 * PITCH, x : x + 2, :3] = (90, 90, 90)
    thumbed = Frame(W, H, px.tobytes(), 2, frame.ts)
    got = glyphs.read(thumbed, GRID, BOX, store)
    assert got.unknown == 0 and got.texts[0] == "abc"
    assert glyphs.learn_alphabet(glyphs.cells(draw(ALPHA_ROWS), GRID, BOX), glyphs.Store()) == 94


def test_without_an_engine_unknown_cells_stay_blank_and_count():
    store = learned()
    frame = draw(["abc"], extra={"b": np.ones((15, 8), bool)})
    got = glyphs.read(frame, GRID, BOX, store)
    assert got.unknown == 1 and got.texts[0] == "a c"


def test_unchanged_rows_are_not_read_again():
    store = learned()
    frame = draw(["one", "two"])
    first = glyphs.read(frame, GRID, BOX, store)
    again = glyphs.read(frame, GRID, BOX, store, prev=first)
    assert again.texts == first.texts and again.digests == first.digests


def test_refine_puts_a_shifted_grid_back_on_its_cells():
    store = learned()
    frame = draw(ALPHA_ROWS * 2 + ["0O1lI5S8B2Z6G"], dy=3)
    assert glyphs.read(frame, GRID, BOX, store).distance > glyphs.OFF_GRID
    fixed = glyphs.refine(frame, GRID, BOX, store)
    assert fixed.origin_y == pytest.approx(OY + 3, abs=0.5) and fixed.pitch == PITCH
    got = glyphs.read(frame, fixed, BOX, store)
    assert got.texts[4] == "0O1lI5S8B2Z6G" and got.distance < glyphs.OFF_GRID


def test_the_alphabet_command_prints_the_alphabet_lines():
    assert "[char[]](33..79)" in glyphs.COMMAND["powershell"]
    assert len(glyphs.ALPHABET[0]) == len(glyphs.ALPHABET[1]) == 47
    assert "".join(glyphs.ALPHABET) == "".join(chr(c) for c in range(33, 127))


def test_snapped_puts_a_grid_on_whole_pixels():
    from jharokha.core.rows import Grid as G

    g = glyphs.snapped(G(48.67, 23.13, 9.78, 12.0, 46, 95))
    assert (g.origin_y, g.pitch, g.origin_x, g.char_w) == (49.0, 23.0, 10.0, 12.0)
    assert glyphs.snapped(G(1.0, 23.5, 1.0, 11.4, 1, 1)).pitch == 23.5  # too far: kept
