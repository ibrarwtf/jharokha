"""Text-less elements from pixels (core/elements.py), the ONNX detector's numpy parts
(win/detect.py) and the detection eval's scoring, on synthetic frames."""

from __future__ import annotations

import time

import numpy as np
from PIL import Image, ImageDraw

from evals.detect_eval import draw_icon, match
from jharokha.core.elements import away_from_text, elements
from jharokha.core.models import Box, Frame, Line, Word
from jharokha.win.detect import _nms, _starts


def _frame(img: Image.Image) -> Frame:
    return Frame(img.width, img.height, img.convert("RGBA").tobytes("raw", "BGRA"), 1, time.time())


def _screen() -> tuple[Frame, list[Line], list[Box], list[Box]]:
    """A bar at the bottom with three icons, a page above with a checkbox, a word OCR read
    beside it, a 'ring' OCR took for an "O" left of a label, and a divider."""
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 250, 399, 299), fill=(238, 238, 238))
    d.line((0, 249, 399, 249), fill=(224, 224, 224))
    truth = []
    for i in range(3):
        x = 140 + 40 * i
        d.rounded_rectangle((x, 260, x + 29, 289), radius=6, fill=(0, 120, 212))
        truth.append((x, 260, 30, 30))
    d.rectangle((20, 40, 31, 51), outline=(97, 97, 97))
    truth.append((20, 40, 12, 12))
    d.rectangle((44, 41, 120, 51), fill=(36, 36, 36))  # the word OCR read, as a dark bar
    d.ellipse((20, 100, 39, 119), outline=(97, 97, 97), width=2)
    truth.append((20, 100, 20, 20))
    d.rectangle((50, 104, 110, 114), fill=(36, 36, 36))
    lines = [
        Line("INC0012345", (44, 41, 77, 11), (Word("INC0012345", (44, 41, 77, 11)),)),
        Line(
            "O Settings",
            (20, 100, 91, 20),
            (Word("O", (20, 100, 20, 20)), Word("Settings", (50, 104, 61, 11))),
        ),
    ]
    panes = [(0, 0, 400, 249), (0, 250, 400, 50)]
    return _frame(img), lines, panes, truth


def test_elements_finds_icons_not_text_or_dividers():
    frame, lines, panes, truth = _screen()
    found = elements(frame, lines, panes)
    assert match(found, truth) == len(truth)
    assert len(found) == len(truth)


def test_elements_drops_words_ocr_left_out_of_a_line():
    """A terminal row whose middle word OCR dropped: the blob between two read words, the
    words' height, is text, not an icon."""
    img = Image.new("RGB", (400, 60), (12, 12, 12))
    d = ImageDraw.Draw(img)
    for x0, x1 in ((10, 60), (70, 100), (110, 200)):
        d.rectangle((x0, 20, x1, 33), fill=(204, 204, 204))
    words = (Word("2026-10-22", (10, 20, 51, 14)), Word("ERROR", (110, 20, 91, 14)))
    lines = [Line("2026-10-22 ERROR", (10, 20, 191, 14), words)]
    assert elements(_frame(img), lines) == []


def test_away_from_text_keeps_only_boxes_off_the_words():
    _, lines, _, _ = _screen()
    boxes = [(44, 41, 77, 11), (20, 40, 12, 12), (140, 260, 30, 30)]
    assert away_from_text(boxes, lines) == [(20, 40, 12, 12), (140, 260, 30, 30)]


def test_nms_and_tiles():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [20, 20, 30, 30]], np.float32)
    assert _nms(boxes, np.array([0.9, 0.8, 0.7], np.float32), 0.45) == [0, 2]
    assert _starts(500) == [0]
    starts = _starts(1181)
    assert starts[0] == 0 and starts[-1] == 1181 - 640
    assert all(b - a <= 640 - 64 for a, b in zip(starts, starts[1:], strict=False))


def test_icon_truth_is_its_ink_and_matching_pairs_once():
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    box = draw_icon(img, ("ring", 10, 10, 20, (0, 0, 0)))
    assert box == (11, 11, 18, 18)
    assert match([box, box], [box]) == 1
    assert match([(50, 50, 10, 10)], [box]) == 0
