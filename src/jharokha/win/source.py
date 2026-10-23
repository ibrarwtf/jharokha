"""The Windows Source: allowlisted client window, PrintWindow frame, Windows OCR."""

from __future__ import annotations

import os
from typing import Any

from jharokha.core.models import Box, Frame, Line, Point, WindowInfo
from jharokha.core.screen import ScreenError
from jharokha.win import capture, ocr, recognize, window


class WindowsSource:
    def __init__(self) -> None:
        window.ensure_dpi_aware()
        env = os.environ.get("JHAROKHA_PROCESSES")
        self.processes = (
            tuple(p.strip().lower() for p in env.split(",")) if env else window.DEFAULT_PROCESSES
        )
        self._ocr: ocr.Ocr | None = None
        self._recognizer: recognize.Recognizer | None = None

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

    def reread(self, frame: Frame, boxes: list[Box], margin: float = 0.5) -> list[str]:
        try:
            if self._recognizer is None:
                self._recognizer = recognize.Recognizer()
            return self._recognizer.read(frame, boxes, margin)
        except recognize.RecognizerUnavailable as e:
            raise ScreenError("verify_unavailable", str(e)) from e

    def _input(self, w: WindowInfo, fn: str, *args: Any) -> None:
        from jharokha.win import input

        try:
            getattr(input, fn)(w.hwnd, *args)
        except input.Covered as e:
            raise ScreenError(
                "covered", f"{e} on '{w.title}'. Bring the session window to the front and retry."
            ) from e
        except input.NotForeground as e:
            raise ScreenError(
                "not_foreground",
                f"{e} ('{w.title}'). Pass focus_at, a blank point in the session to click first.",
            ) from e
        except input.ClipboardBusy as e:
            raise ScreenError("clipboard_busy", f"{e}; nothing was sent. Retry.") from e
        except ValueError as e:
            raise ScreenError("bad_key", str(e)) from e

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        self._input(w, "scroll", x, y, ticks)

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        self._input(w, "click", x, y, button, double)

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        self._input(w, "paste_text" if paste else "type_text", text, focus_at)

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        self._input(w, "press", combo, focus_at)
