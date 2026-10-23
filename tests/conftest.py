"""A synthetic Source: screens are lists of (text, x, y) drawn with PIL, so OCR-free tests
know the ground truth and the real OCR can be checked against the same picture."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest
from PIL import Image, ImageDraw

from jharokha.core.models import Box, Frame, Line, Point, WindowInfo, Word
from jharokha.core.screen import ScreenError

SIZE = (640, 400)


def render(items: list[tuple[str, int, int]], size: tuple[int, int] = SIZE) -> Frame:
    img = Image.new("RGBA", size, "white")
    d = ImageDraw.Draw(img)
    for text, x, y in items:
        d.text((x, y), text, fill="black")
    return Frame(size[0], size[1], img.tobytes("raw", "BGRA"), frame_id=1, ts=time.time())


def lines_for(items: list[tuple[str, int, int]]) -> list[Line]:
    """Ground-truth lines: words 7 px wide each, 12 px tall, one space between."""
    out = []
    for text, x, y in items:
        words, cx = [], x
        for w in text.split():
            words.append(Word(w, (cx, y, 7 * len(w), 12)))
            cx += 7 * len(w) + 7
        out.append(Line(text, (x, y, cx - x - 7, 12), tuple(words)))
    return out


@dataclass
class FakeSource:
    items: list[tuple[str, int, int]] = field(default_factory=list)
    title: str = "Remote Desktop - test"
    minimized: bool = False
    present: bool = True
    ocr_calls: int = 0
    frame_id: int = 0
    misreads: dict[str, str] = field(default_factory=dict)  # what the first OCR gets wrong
    reread_boxes: list[Box] = field(default_factory=list)

    def window(self, window_title: str | None) -> WindowInfo | None:
        if not self.present or (window_title and window_title.lower() not in self.title.lower()):
            return None
        return WindowInfo(1, self.title, "fake.exe", self.minimized, True, SIZE, 1.0)

    def candidates(self) -> list[str]:
        return [self.title] if self.present else []

    def frame(self, w: WindowInfo) -> Frame:
        self.frame_id += 1
        f = render(self.items)
        return Frame(f.width, f.height, f.bgra, self.frame_id, f.ts)

    def ocr(self, frame: Frame, region: Box | None) -> list[Line]:
        self.ocr_calls += 1
        lines = lines_for([(self.misreads.get(t, t), x, y) for t, x, y in self.items])
        if region is None:
            return lines
        x, y, w, h = region
        return [ln for ln in lines if x <= ln.box[0] < x + w and y <= ln.box[1] < y + h]

    def reread(self, frame: Frame, boxes: list[Box], margin: float = 0.5) -> list[str]:
        """The truth for each line, found by its top-left corner. Row strips (margin 0) are
        what a source without the second engine refuses, so grids fall back to OCR words."""
        if margin == 0:
            raise ScreenError("verify_unavailable", "the fake reads only line boxes")
        self.reread_boxes.extend(boxes)
        truth = {(x, y): t for t, x, y in self.items}
        return [truth[(b[0], b[1])] for b in boxes]

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        self.items = [(t, ix, iy + 20 * ticks) for t, ix, iy in self.items]

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        self.items.append((f"clicked {button}{' x2' if double else ''} at {x},{y}", 10, 200))

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        at = f" after a click at {focus_at[0]},{focus_at[1]}" if focus_at else ""
        self.items.append((f"{'pasted' if paste else 'typed'} {text}{at}", 10, 230))

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        self.items.append((f"pressed {combo}", 10, 260))


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(
        [
            ("Build succeeded in 12s", 10, 10),
            ("Error: file not found", 10, 40),
            ("Order AB12 shipped", 10, 70),
            ("second column", 300, 10),
        ]
    )
