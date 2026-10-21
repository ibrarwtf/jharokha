"""The Windows Source: allowlisted client window, PrintWindow frame, Windows OCR."""

from __future__ import annotations

import os

from jharokha.core.models import Box, Frame, Line, WindowInfo
from jharokha.core.screen import ScreenError
from jharokha.win import capture, ocr, window


class WindowsSource:
    def __init__(self) -> None:
        window.ensure_dpi_aware()
        env = os.environ.get("JHAROKHA_PROCESSES")
        self.processes = (
            tuple(p.strip().lower() for p in env.split(",")) if env else window.DEFAULT_PROCESSES
        )
        self._ocr: ocr.Ocr | None = None

    def window(self, window_title: str | None) -> WindowInfo | None:
        found = window.find_window(window_title, self.processes)
        return window.window_info(*found) if found else None

    def candidates(self) -> list[str]:
        return [title for _, title, _ in window.list_windows(self.processes)]

    def frame(self, w: WindowInfo) -> Frame:
        try:
            return capture.capture(w.hwnd)
        except capture.CaptureError as e:
            raise ScreenError("no_frames", f"could not capture '{w.title}': {e}") from e

    def ocr(self, frame: Frame, region: Box | None) -> list[Line]:
        try:
            if self._ocr is None:
                self._ocr = ocr.Ocr()
            # A small region is a zoom: upscale so small text resolves.
            small = region is not None and region[2] * region[3] * 4 <= frame.width * frame.height
            return self._ocr.recognize(frame, region, upscale=2.0 if small else 1.0)
        except ocr.OcrUnavailable as e:
            raise ScreenError("ocr_unavailable", str(e)) from e
