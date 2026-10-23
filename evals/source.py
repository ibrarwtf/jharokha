"""A Source with no window: generated eval screens as frames, read by the real Windows OCR
and the second engine, so the MCP server can be measured end to end without a session."""

from __future__ import annotations

from collections.abc import Callable

from evals.render import Screen, render
from jharokha.core.models import Frame, Point, WindowInfo
from jharokha.win.source import WindowsSource

TITLE = "Eval Remote Desktop"


class EvalSource(WindowsSource):
    """Production ocr() and reread(), so the upscale rule and the lazy engine loads are the
    server's own; window, frame and input are stand-ins. advance() draws the next screen,
    which is how a caller gets past the server's frame cache."""

    def __init__(self, make: Callable[[int], Screen], seeds: list[int]) -> None:
        super().__init__()
        self._make, self._seeds = make, seeds
        self._i = -1
        self._frame: Frame | None = None
        self.advance()

    def advance(self) -> Screen:
        self._i += 1
        screen = self._make(self._seeds[self._i % len(self._seeds)])
        f, _ = render(screen, "gdi", codec=True)
        self._frame = Frame(f.width, f.height, f.bgra, self._i + 1, f.ts)
        self.screen = screen
        return screen

    def window(self, window_title: str | None) -> WindowInfo | None:
        if window_title and window_title.lower() not in TITLE.lower():
            return None
        assert self._frame is not None
        size = (self._frame.width, self._frame.height)
        return WindowInfo(0, TITLE, "eval", False, True, size, 1.25)

    def candidates(self) -> list[str]:
        return [TITLE]

    def frame(self, w: WindowInfo) -> Frame:
        assert self._frame is not None
        f = self._frame
        # A capture is a new buffer every time; handing back the same object would let the
        # change check short-circuit on identity, which a live session never does.
        return Frame(f.width, f.height, bytes(bytearray(f.bgra)), f.frame_id, f.ts)

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        pass

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        pass

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        pass

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        pass
