"""A synthetic Source: screens are lists of (text, x, y) drawn with PIL, so OCR-free tests
know the ground truth and the real OCR can be checked against the same picture."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest
from PIL import Image, ImageDraw

from jharokha.core.models import Box, Frame, Line, WindowInfo, Word

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
        lines = lines_for(self.items)
        if region is None:
            return lines
        x, y, w, h = region
        return [ln for ln in lines if x <= ln.box[0] < x + w and y <= ln.box[1] < y + h]

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        self.items = [(t, ix, iy + 20 * ticks) for t, ix, iy in self.items]


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
