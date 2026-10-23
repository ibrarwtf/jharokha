"""The Windows Source: allowlisted client window, PrintWindow frame, Windows OCR."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from jharokha.core.guard import CHUNK_MARGIN_MS, InputLeased, UserActive, refusal, warnings
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
        # How long input waits for this laptop's keyboard and mouse to go idle before it is
        # refused: 0 for the server (the agent is told), a few seconds in a shell, where the
        # Enter that started the command is the user's own last input.
        self.wait_idle_s = 0.0

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

    def guard_state(self, w: WindowInfo) -> dict[str, Any]:
        """What jharokha_status reports for input: parts of the client hidden from view,
        another process's lease on this window, and the user's idle time."""
        from jharokha.win import guard

        out: dict[str, Any] = {"warnings": warnings(guard.covered(w.hwnd))}
        other = guard.Lease(w.hwnd).other()
        out["input"] = {
            "lease": {"pid": other.owner_pid, "idle_s": round(other.idle_s, 1)} if other else None,
            "user_idle_s": _round(guard.user_idle_s()),
        }
        return out

    def _input(self, w: WindowInfo, fn: str, *args: Any, chunked: bool = False) -> None:
        """Every input goes through here: this process takes the window's lease (another
        jharokha sending to it refuses), nobody may have used this laptop in the last few
        seconds, and long text is checked again between chunks."""
        from jharokha.win import guard, input

        kw: dict[str, Any] = {}
        if chunked:
            kw["between"] = lambda: guard.ensure_user_idle(margin_ms=CHUNK_MARGIN_MS)
        try:
            guard.Lease(w.hwnd).take()
            self._wait_idle(guard.ensure_user_idle)
            getattr(input, fn)(w.hwnd, *args, **kw)
        except (InputLeased, UserActive) as e:
            raise ScreenError(*refusal(e)) from e
        except input.Covered as e:
            what = _covering(guard.covered(w.hwnd), args)
            raise ScreenError(
                "covered",
                f"{e}{what} on '{w.title}'; nothing was sent. Bring the session window to the "
                "front, or move what covers it, and retry; jharokha_status lists warnings.",
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

    def _wait_idle(self, ensure: Callable[[], None]) -> None:
        deadline = time.monotonic() + self.wait_idle_s
        while True:
            try:
                return ensure()
            except UserActive:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        self._input(w, "scroll", x, y, ticks)

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        self._input(w, "click", x, y, button, double)

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        self._input(w, "paste_text" if paste else "type_text", text, focus_at, chunked=True)

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        self._input(w, "press", combo, focus_at)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _covering(parts: list[dict], args: tuple[Any, ...]) -> str:
    """What covers the click point (args start with x, y for a click or scroll): the
    local taskbar, another app, or nothing on screen."""
    if len(args) < 2 or not all(isinstance(a, int) for a in args[:2]):
        return ""
    x, y = args[0], args[1]
    for p in parts:
        if p["kind"] == "taskbar":
            a, b = p.get("rows") or p["cols"]
            if a <= (y if "rows" in p else x) < b:
                return " (this computer's taskbar; auto-hide it or make the session full screen)"
        else:
            bx, by, bw, bh = p["box"]
            if bx <= x < bx + bw and by <= y < by + bh:
                return f" ({p.get('app') or 'off screen'})"
    return ""
