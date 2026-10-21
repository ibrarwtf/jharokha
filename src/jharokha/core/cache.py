"""Skip OCR when the screen has not changed. A blinking cursor must not count as a change."""

from __future__ import annotations

from jharokha.core.models import Box, Frame, Line
from jharokha.core.watch import changed_bbox


class FrameCache:
    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._lines: dict[Box | None, list[Line]] = {}

    def _same(self, frame: Frame) -> bool:
        return self._frame is not None and changed_bbox(self._frame, frame) is None

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
