"""Skip OCR when the screen has not changed. A blinking cursor must not count as a change."""

from __future__ import annotations

from PIL import Image, ImageChops

from jharokha.core.models import Box, Frame, Line

_MAX_CHANGED_AREA = 100  # px^2: a text caret, not a character


class FrameCache:
    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._lines: dict[Box | None, list[Line]] = {}

    def _same(self, frame: Frame) -> bool:
        old = self._frame
        if old is None or (old.width, old.height) != (frame.width, frame.height):
            return False
        if old.bgra == frame.bgra:
            return True
        a = Image.frombuffer("RGBA", (old.width, old.height), old.bgra, "raw", "BGRA", 0, 1)
        b = Image.frombuffer("RGBA", (frame.width, frame.height), frame.bgra, "raw", "BGRA", 0, 1)
        bbox = ImageChops.difference(a, b).getbbox(alpha_only=False)
        return bbox is None or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) <= _MAX_CHANGED_AREA

    def get(self, frame: Frame, region: Box | None) -> list[Line] | None:
        if self._same(frame):
            return self._lines.get(region)
        self._frame = frame
        self._lines = {}
        return None

    def put(self, frame: Frame, region: Box | None, lines: list[Line]) -> None:
        if self._frame is not frame and not self._same(frame):
            self._frame = frame
            self._lines = {}
        self._lines[region] = lines
