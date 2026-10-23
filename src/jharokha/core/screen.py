"""What the server needs from a platform: a window, a frame of it, text in the frame.
`win/` implements it for Windows; tests implement it with synthetic screens."""

from __future__ import annotations

import io
import time
from typing import Protocol

from PIL import Image

from jharokha.core.cache import FrameCache
from jharokha.core.models import Box, Frame, Line, Point, WindowInfo
from jharokha.core.panes import reading_panes
from jharokha.core.text import clean, reading_order
from jharokha.core.watch import changed_bbox


class ScreenError(Exception):
    """code is one of: not_windows, window_not_found, minimized, locked, no_frames,
    ocr_unavailable, region_out_of_bounds, timeout, covered, not_foreground, bad_key,
    verify_unavailable, clipboard_busy, bad_prompt, bad_argument."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class Source(Protocol):
    def window(self, window_title: str | None) -> WindowInfo | None: ...
    def candidates(self) -> list[str]: ...
    def frame(self, w: WindowInfo) -> Frame: ...
    def ocr(self, frame: Frame, region: Box | None) -> list[Line]: ...
    def reread(self, frame: Frame, boxes: list[Box], margin: float = 0.5) -> list[str]:
        """The second engine's text in each box, read with margin x its height around it:
        0.5 for a line Windows OCR found, 0 for a terminal row strip (core.rows)."""
        ...

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None: ...
    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None: ...
    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None: ...
    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None: ...


class Screen:
    """One reading of the window: frame plus OCR lines, cached across calls."""

    def __init__(self, source: Source) -> None:
        self.source = source
        self.cache = FrameCache()

    def window(self, window_title: str | None) -> WindowInfo:
        w = self.source.window(window_title)
        if w is None:
            names = ", ".join(repr(c) for c in self.source.candidates()) or "none"
            raise ScreenError(
                "window_not_found",
                f"No remote-desktop window found. Candidates: {names}. "
                "Open the remote session, or pass window_title=<part of its title>.",
            )
        if w.minimized:
            raise ScreenError(
                "minimized",
                f"'{w.title}' is minimized and RDP sends no frames to a minimized window. "
                "Restore it (it may stay behind other windows) and retry.",
            )
        return w

    def frame(self, window_title: str | None) -> tuple[WindowInfo, Frame]:
        w = self.window(window_title)
        frame = self.source.frame(w)
        if not any(frame.bgra):
            raise ScreenError(
                "no_frames", f"'{w.title}' rendered black; the session may be disconnected."
            )
        return w, frame

    def settle(self, window_title: str | None, max_s: float = 3.0) -> int:
        """Wait until the window stops changing after input: at least MIN_SETTLE_S, since the
        RDP client takes a moment to paint anything, then QUIET_S without a change, or
        max_s for screens that never stop (a spinner). Returns the milliseconds waited."""
        start = time.monotonic()
        _, prev = self.frame(window_title)
        quiet_since = start
        while True:
            time.sleep(POLL_S)
            _, cur = self.frame(window_title)
            now = time.monotonic()
            if changed_bbox(prev, cur) is not None:
                quiet_since = now
            prev = cur
            settled = now - start >= MIN_SETTLE_S and now - quiet_since >= QUIET_S
            if settled or now - start >= max_s:
                return int((now - start) * 1000)

    def read(self, window_title: str | None, region: Box | None) -> tuple[Frame, list[Line]]:
        w, frame = self.frame(window_title)
        if region:
            check_region(region, frame)
        lines = self.cache.get(frame, region)
        if lines is None:
            lines = reading_order(self.source.ocr(frame, region), reading_panes(frame, region))
            self.cache.put(frame, region, lines)
        return frame, lines

    def verify(self, frame: Frame, lines: list[Line], which: list[int]) -> dict[int, str]:
        """Second readings of lines[i] for i in which (at most MAX_VERIFY), by index."""
        which = which[:MAX_VERIFY]
        texts = self.source.reread(frame, [lines[i].box for i in which]) if which else []
        return {i: clean(t) for i, t in zip(which, texts, strict=True)}


MIN_SETTLE_S, QUIET_S, POLL_S = 0.4, 0.3, 0.1
MAX_VERIFY = 40  # about 30 ms each: a second or two at most


def check_region(region: Box, frame: Frame) -> None:
    x, y, w, h = region
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > frame.width or y + h > frame.height:
        raise ScreenError(
            "region_out_of_bounds",
            f"region {list(region)} is outside the {frame.width}x{frame.height} client area; "
            "region is [x, y, width, height] in client pixels.",
        )


def png(frame: Frame, region: Box | None, max_long_edge: int) -> tuple[bytes, Box, float]:
    """PNG of the region (or whole frame), downscaled so the long edge fits. Returns the
    bytes, the region used, and the scale from client pixels to image pixels."""
    img = frame.image()
    box = region or (0, 0, frame.width, frame.height)
    if region:
        check_region(region, frame)
        img = img.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    scale = min(1.0, max_long_edge / max(img.size))
    if scale < 1.0:
        img = img.resize(
            (round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue(), box, scale
