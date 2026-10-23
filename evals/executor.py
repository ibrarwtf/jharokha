"""An action Executor over any Source (core.actions): read a frame, identify the app and view
on it, place that view's regions and the session's, and send input through the Source. Used
by the tests to run actions on the simulated session; the server's `do` tool is to do the
same over its own Screen."""

from __future__ import annotations

import time

from jharokha.core.actions import Snapshot
from jharokha.core.apps import Catalog
from jharokha.core.identify import identify
from jharokha.core.models import Box, Frame, Line, WindowInfo
from jharokha.core.panes import background, panes, reading_panes
from jharokha.core.screen import Source
from jharokha.core.text import looks_locked, reading_order

QUIET_S, MAX_SETTLE_S, POLL_S = 0.2, 3.0, 0.03


class SourceExecutor:
    def __init__(self, source: Source, catalog: Catalog, window_title: str | None = None) -> None:
        self.source, self.catalog, self.title = source, catalog, window_title
        self._snap: Snapshot | None = None
        self.reads = 0  # frames OCR'd
        self.inputs: list[str] = []  # what was sent, for tests

    def _window(self) -> WindowInfo:
        w = self.source.window(self.title)
        if w is None:
            raise RuntimeError("no session window")
        return w

    def _frame(self) -> Frame:
        return self.source.frame(self._window())

    def read(self) -> Snapshot:
        frame = self._frame()
        if self._snap and self._snap.frame_id == frame.frame_id:
            return self._snap
        self.reads += 1
        lines: list[Line] = reading_order(self.source.ocr(frame, None), reading_panes(frame))
        found = panes(frame)
        size = (frame.width, frame.height)
        spots = {
            k: (round(x * frame.width), round(y * frame.height))
            for k, (x, y) in self.catalog.spots.items()
        }
        regions = {}
        for name in dict.fromkeys([*self.catalog.session_cal, *self.catalog.session]):
            if p := self.catalog.session_box(name, found, *size):
                regions[name] = p[0]
        if looks_locked(lines):
            snap = Snapshot(frame.frame_id, size, lines, None, None, None, regions, spots, True)
        else:
            ident = identify(self.catalog.apps.values(), lines, found, background(frame), *size)
            if ident.app and ident.view:
                for name in self.catalog.apps[ident.app].views[ident.view].regions:
                    placed = self.catalog.region_box(
                        ident.app, ident.view, name, found, ident.found, *size
                    )
                    if placed:
                        regions[f"{ident.app}.{name}"] = placed[0]
            snap = Snapshot(
                frame.frame_id, size, lines, ident.app, ident.view, ident.ready, regions, spots
            )
        self._snap = snap
        return snap

    def zoom(self, box: Box) -> list[Line]:
        """box read on its own: a Source upscales a small one, as for a region read."""
        frame = self._frame()
        self.reads += 1
        return reading_order(self.source.ocr(frame, box), reading_panes(frame, box))

    def reread(self, boxes: list[Box]) -> list[str]:
        return self.source.reread(self._frame(), boxes)

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self.inputs.append(f"click {x},{y}")
        self.source.click(self._window(), x, y, button, double)

    def type(self, text: str) -> None:
        assert "\n" not in text and "\r" not in text, "a newline is a key, pressed on its own"
        self.inputs.append(f"type {text}")
        self.source.type_text(self._window(), text)

    def press(self, keys: str) -> None:
        self.inputs.append(f"press {keys}")
        self.source.press(self._window(), keys)

    def scroll(self, x: int, y: int, ticks: int) -> None:
        self.inputs.append(f"scroll {x},{y} {ticks}")
        self.source.scroll(self._window(), x, y, ticks)

    def settle(self) -> None:
        """Until the frame has not changed for QUIET_S, or MAX_SETTLE_S."""
        start = last = time.monotonic()
        fid = self._frame().frame_id
        while time.monotonic() - start < MAX_SETTLE_S:
            time.sleep(POLL_S)
            now_id = self._frame().frame_id
            if now_id != fid:
                fid, last = now_id, time.monotonic()
            elif time.monotonic() - last >= QUIET_S:
                return

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()
