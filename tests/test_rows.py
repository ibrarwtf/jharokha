"""core.rows on synthetic terminals drawn with Cascadia Mono: the grid measured from the
pixels, strips and changed rows, and rows put together from a reader that drops the spaces
between columns, as the second engine does."""

from __future__ import annotations

import io
import re
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from jharokha.core import rows
from jharokha.core.models import Box, Frame, Line, Word

FONT = Path(__file__).parents[1] / "evals" / "fonts" / "CascadiaMono-Regular.ttf"
BG, FG = (12, 12, 12), (204, 204, 204)
SIZE = (900, 520)
BOX: Box = (0, 30, 880, 490)
LEFT, TOP = 8, 40

SCREEN = [
    "PS C:\\work\\ledger> git log --oneline -3",
    "a1b2c3d fix retry window",
    "",
    "Mode                 LastWriteTime         Length Name",
    "----                 -------------         ------ ----",
    "-a---          10/24/2026  9:42 AM           7259 profile_11.json",
    "",
    "",
    '  "ticket": "INC0O1I5S8",',
    "  status_code = 400",
    "PS C:\\work\\ledger> Get-ChildItem | select Name",
    "PS C:\\work\\ledger>",
]


def draw(
    texts: list[str], px: int = 20, size: tuple[int, int] = SIZE, scrollbar: bool = False
) -> tuple[Frame, int, float]:
    """The frame, the row pitch and the character advance."""
    font = ImageFont.truetype(str(FONT), px)
    pitch = round(px * 1.15)
    img = Image.new("RGBA", size, BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, size[0], 20), fill=(32, 32, 32))
    for i, t in enumerate(texts):
        d.text((LEFT, TOP + i * pitch), t, fill=FG, font=font)
    if scrollbar:
        d.rectangle((size[0] - 14, 120, size[0] - 10, 400), fill=(110, 110, 110))
    frame = Frame(size[0], size[1], img.tobytes("raw", "BGRA"), 1, time.time())
    return frame, pitch, font.getlength("M" * 100) / 100


def strip_reader(
    grid: rows.Grid, texts: list[str], calls: list[int]
) -> Callable[[list[Box]], list[str]]:
    """A reader that knows the screen: each strip's row, with the runs of spaces between
    columns dropped and single spaces kept, as the engine reads a terminal."""

    def read(boxes: list[Box]) -> list[str]:
        calls.append(len(boxes))
        out = []
        r0 = round((TOP - grid.origin_y) / grid.pitch)
        for _, y, _, _ in boxes:
            r = round((y - grid.origin_y) / grid.pitch) - r0
            t = texts[r] if 0 <= r < len(texts) else ""
            out.append(re.sub(r" {2,}", "", t).strip())
        return out

    return read


def check_grid(g: rows.Grid | None, pitch: int, adv: float, texts: list[str]) -> rows.Grid:
    assert g is not None
    assert abs(g.pitch - pitch) < 0.1
    assert abs(g.char_w - adv) < 0.05
    # Each drawn row's ink lies inside its strip, so row r is the r-th drawn row.
    first = next(i for i, t in enumerate(texts) if t)
    r0 = round((TOP - g.origin_y) / g.pitch)
    assert r0 >= 0 and abs(g.origin_y + r0 * g.pitch - TOP) < 4
    assert first >= 0
    assert abs(g.origin_x - LEFT) < adv / 2
    return g


@pytest.mark.parametrize("px", [14, 16, 20, 24])
@pytest.mark.parametrize("codec", [False, True])
def test_calibrate_from_pixels_alone(px, codec):
    """No OCR lines at all, as when Windows OCR found nothing on a dark terminal; also
    through lossy compression with 4:2:0 chroma, as the RDP client's codecs do."""
    frame, pitch, adv = draw(SCREEN, px)
    if codec:
        buf = io.BytesIO()
        frame.image().convert("RGB").save(buf, "JPEG", quality=80, subsampling=2)
        img = Image.open(buf).convert("RGBA")
        frame = Frame(frame.width, frame.height, img.tobytes("raw", "BGRA"), 1, 0.0)
    check_grid(rows.calibrate(frame, [], BOX), pitch, adv, SCREEN)


def test_calibrate_takes_the_greatest_common_spacing_not_the_smallest_gap():
    """Rows two and three pitches apart, never one: the pitch is what divides them all."""
    texts = []
    for i, t in enumerate(SCREEN[:6]):
        texts += [t] + [""] * (1 + i % 2)
    frame, pitch, adv = draw(texts, 20, (900, 700))
    g = check_grid(rows.calibrate(frame, [], (0, 30, 880, 660)), pitch, adv, texts)
    assert g.rows >= len(texts)


def test_calibrate_with_most_rows_missing_from_ocr_and_a_scrollbar():
    frame, pitch, adv = draw(SCREEN, 20, scrollbar=True)
    # Windows OCR found two words on the whole screen, one of them at the wrong height.
    found = [
        Line("PS", (LEFT, TOP + 3, 22, 16), (Word("PS", (LEFT, TOP + 3, 22, 16)),)),
        Line("profile_11.json", (500, TOP + 5 * pitch, 180, 17)),
    ]
    check_grid(rows.calibrate(frame, found, (0, 30, 900, 490)), pitch, adv, SCREEN)


def test_calibrate_none_on_a_blank_box():
    frame, *_ = draw([])
    assert rows.calibrate(frame, [], BOX) is None


def test_row_boxes_are_strips_one_pitch_tall_across_the_grid():
    g = rows.Grid(40.4, 23.0, 8.0, 12.0, 3, 10)
    assert rows.row_boxes(g) == [(8, 40, 120, 23), (8, 63, 120, 23), (8, 86, 120, 23)]
    assert rows.row_boxes(g, [2]) == [(8, 86, 120, 23)]


def test_read_rows_puts_the_spaces_back_and_keeps_blank_rows():
    frame, *_ = draw(SCREEN)
    g = rows.calibrate(frame, [], BOX)
    assert g is not None
    calls: list[int] = []
    read = rows.read_rows(frame, BOX, strip_reader(g, SCREEN, calls), g)
    assert read is not None and len(read.lines) == g.rows
    r0 = round((TOP - g.origin_y) / g.pitch)
    got = [ln.text for ln in read.lines[r0 : r0 + len(SCREEN)]]
    assert got == SCREEN
    assert all(not ln.text for ln in read.lines[r0 + len(SCREEN) :])
    # Only inked rows are read.
    assert calls == [sum(1 for t in SCREEN if t)]
    # Words are boxed by their cells, so a click lands on them.
    name = next(w for ln in read.lines for w in ln.words if w.text == "profile_11.json")
    col = SCREEN[5].index("profile_11.json")
    assert abs(name.box[0] - (LEFT + col * g.char_w)) <= 1


def test_read_rows_rereads_only_changed_rows_and_follows_a_scroll():
    frame, *_ = draw(SCREEN)
    g = rows.calibrate(frame, [], BOX)
    assert g is not None
    first = rows.read_rows(frame, BOX, strip_reader(g, SCREEN, []), g)
    assert first is not None

    edited = [*SCREEN[:-1], "PS C:\\work\\ledger> exit"]
    frame2, *_ = draw(edited)
    calls: list[int] = []
    second = rows.read_rows(frame2, BOX, strip_reader(g, edited, calls), g, prev=first)
    assert second is not None and calls == [1]
    r0 = round((TOP - g.origin_y) / g.pitch)
    assert [ln.text for ln in second.lines[r0 : r0 + len(edited)]] == edited

    scrolled = [*edited[1:], "PS C:\\work\\ledger> cls"]
    frame3, *_ = draw(scrolled)
    calls = []
    third = rows.read_rows(frame3, BOX, strip_reader(g, scrolled, calls), g, prev=second)
    assert third is not None
    assert [ln.text for ln in third.lines[r0 : r0 + len(scrolled)]] == scrolled
    assert calls == [1]  # every other row only moved up


def test_changed_rows_ignores_a_caret_but_not_a_character():
    frame, *_ = draw(SCREEN)
    g = rows.calibrate(frame, [], BOX)
    assert g is not None
    last = round((TOP + (len(SCREEN) - 1) * g.pitch - g.origin_y) / g.pitch)
    assert rows.changed_rows(None, frame, g) == list(range(g.rows))
    assert rows.changed_rows(frame, frame, g) == []
    img = frame.image().copy()
    x = round(g.origin_x + 19 * g.char_w)
    _, y, _, h = g.row_box(last)
    ImageDraw.Draw(img).rectangle((x, y + 3, x + 1, y + h - 4), fill=FG)  # a bar caret
    caret = Frame(frame.width, frame.height, img.tobytes("raw", "BGRA"), 2, time.time())
    assert rows.changed_rows(frame, caret, g) == []
    typed, *_ = draw([*SCREEN[:-1], SCREEN[-1] + " x"])
    assert rows.changed_rows(frame, typed, g) == [last]


def test_assemble_aligns_a_reading_with_a_missing_or_extra_character():
    g = rows.Grid(0, 20, 0, 10, 1, 12)
    cell = rows.Cell(4, 15, 6, False)
    # `ab  cd  ef` on cells 0-1, 4-5, 8-9.
    ink = [[cell if c in (0, 1, 4, 5, 8, 9) else None for c in range(12)]]
    assert rows.assemble(g, ["abcdef"], [], ink)[0].text == "ab  cd  ef"
    # One character missed: the engine's space still puts `ef` in its column.
    assert rows.assemble(g, ["abc ef"], [], ink)[0].text == "ab  c   ef"
    # One read twice by CTC, one extra.
    assert rows.assemble(g, ["abbcdef"], [], ink)[0].text == "ab  cd  ef"
    # Windows OCR read the missing one.
    win = [Line("cd", (40, 2, 20, 16), (Word("cd", (40, 2, 20, 16)),))]
    assert rows.assemble(g, ["abc ef"], win, ink)[0].text == "ab  cd  ef"
    # A row the engine did not read comes from Windows OCR alone; a blank row stays.
    assert rows.assemble(g, [None], win, ink)[0].text == "    cd"
    assert rows.assemble(g, ["x"], [], [[None] * 12])[0].text == ""


def test_assemble_fixes_zeros_and_full_width_forms():
    g = rows.Grid(0, 20, 0, 10, 1, 40)
    assert rows.assemble(g, ["elapsed：3.2e"])[0].text == "elapsed:3.20"
    assert rows.assemble(g, ["INF0 ERR0R P07"])[0].text == "INFO ERROR P07"


def test_shape_settles_what_the_engine_confuses():
    texts = ["----  ____  0O0O  pPcC | x", "ABDEFGH amenr 123456"]
    # The engine's confusions on exactly these: bars, zeros, case, and a skipped `|`.
    engine = ["____ ---- OOOO PPcc x", texts[1]]
    frame, *_ = draw(texts)
    g = rows.calibrate(frame, [], BOX)
    assert g is not None
    r0 = round((TOP - g.origin_y) / g.pitch)

    def read(boxes: list[Box]) -> list[str]:
        return [engine[round((b[1] - g.origin_y) / g.pitch) - r0] for b in boxes]

    out = rows.read_rows(frame, BOX, read, g)
    assert out is not None
    assert [ln.text for ln in out.lines[r0 : r0 + 2]] == texts


def test_unwrap_joins_a_row_that_runs_to_the_edge():
    g = rows.Grid(0, 20, 0, 10, 3, 6)
    lines = rows.assemble(g, ["abcdef", "gh", "ij"])
    assert [ln.text for ln in rows.unwrap(lines, g)] == ["abcdefgh", "ij"]
