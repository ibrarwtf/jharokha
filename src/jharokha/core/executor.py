"""core.actions' Executor over a Session: a frame read as the read tools read it (the app and
view identified, its regions placed, terminal grids read row by row), and input sent
through the Source, whose own guards (another agent's lease, someone at this computer)
refuse it with the same codes as the input tools."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from jharokha.core.actions import RegionInfo, Snapshot
from jharokha.core.locate import spot_px
from jharokha.core.models import Box, Frame, Line, WindowInfo
from jharokha.core.screen import ScreenError
from jharokha.core.session import Session
from jharokha.core.text import looks_locked
from jharokha.core.watch import changed_bbox

# kind (click, type, press, scroll), what was sent, and "sent" or the error code
OnInput = Callable[[str, dict[str, Any], str], None]


class SessionExecutor:
    def __init__(
        self, session: Session, window_title: str | None = None, on_input: OnInput | None = None
    ) -> None:
        self.session, self.title = session, window_title
        self.on_input = on_input
        self._frame_seen: Frame | None = None
        self._snap: Snapshot | None = None
        self._glanced: Snapshot | None = None  # a fast read: Windows OCR's text only
        self.reads = 0  # screens OCR'd and identified
        self.inputs: list[str] = []  # what was sent, in order

    @property
    def front(self) -> str | None:
        """The app and view on the screen read last, as a trace names them."""
        s = self._snap
        return f"{s.app}.{s.view}" if s and s.app else None

    def _window(self) -> WindowInfo:
        return self.session.screen.window(self.title)

    def _frame(self) -> Frame:
        """The frame now; the one read before while the pixels have not changed, so a frame
        id tells an unchanged screen (a scroll at the end of a list) from a changed one."""
        _, f = self.session.screen.frame(self.title)
        if self._frame_seen is not None and changed_bbox(self._frame_seen, f) is None:
            return self._frame_seen
        self._frame_seen = f
        return f

    def glance(self) -> Snapshot:
        """The screen identified and its regions placed, its text Windows OCR's alone: what
        polling for a marker or checking the app in front needs, at a tenth of a read that
        also reads a terminal's rows with the second engine."""
        return self.read(fast=True)

    def read(self, fast: bool = False) -> Snapshot:
        frame = self._frame()
        if self._snap and self._snap.frame_id == frame.frame_id:
            return self._snap
        if fast and self._glanced and self._glanced.frame_id == frame.frame_id:
            return self._glanced
        self.reads += 1
        s = self.session
        cat = s.catalog
        lines = s.screen.lines(frame, None)
        size = (frame.width, frame.height)
        at = s.where(frame, lines)
        front = None if looks_locked(lines) else at.ident.app
        spots = {k: spot_px(p, *size) for k, p in cat.spots_for(front).items()}
        regions: dict[str, Box] = {}
        for name in dict.fromkeys([*cat.session_cal, *cat.session]):
            if p := cat.session_box(name, at.panes, *size):
                regions[name] = p[0]
        if looks_locked(lines):
            snap = Snapshot(frame.frame_id, size, lines, None, None, None, regions, spots, True)
        else:
            ident = at.ident
            lines = lines if fast else s.shape(at, lines)
            info: dict[str, RegionInfo] = {}
            if ident.app and ident.view:
                for name, reg in cat.apps[ident.app].views[ident.view].regions.items():
                    if placed := s.place(at, ident.app, ident.view, name):
                        key = f"{ident.app}.{name}"
                        regions[key] = placed[0]
                        inner = tuple(s.nested(at, ident.app, ident.view, name))
                        info[key] = RegionInfo(reg.reader, reg.kind, inner)
            snap = Snapshot(
                frame.frame_id,
                size,
                lines,
                ident.app,
                ident.view,
                ident.ready,
                regions,
                spots,
                confidence=ident.confidence,
                info=info,
            )
        if fast:
            self._glanced = snap
        else:
            self._snap = snap
        return snap

    def glyphs(self) -> int | None:
        """How many glyphs are known for the terminal font on screen now; None when no
        terminal grid is on screen."""
        self.read()
        return self.session.glyphs

    def unknown_cells(self) -> int:
        """Cells of the last full read no learned glyph matched (read by the engine)."""
        return self.session.unknown

    def learn_glyphs(self) -> int:
        """The terminal glyphs learned from the alphabet lines on screen now
        (Session.learn_glyphs); the next read reads with them."""
        frame = self._frame()
        s = self.session
        n = s.learn_glyphs(s.where(frame, s.screen.lines(frame, None)))
        if n:
            self._snap = None
        return n

    def zoom(self, box: Box) -> list[Line]:
        """box read on its own: a Source upscales a small one, as for a region read."""
        frame = self._frame()
        x, y = max(0, box[0]), max(0, box[1])
        w, h = min(box[2], frame.width - x), min(box[3], frame.height - y)
        if w <= 0 or h <= 0:
            return []
        self.reads += 1
        return self.session.screen.lines(frame, (x, y, w, h))

    def _send(
        self, kind: str, said: str, fields: dict[str, Any], send: Callable[[WindowInfo], None]
    ) -> None:
        w = self._window()
        try:
            send(w)
        except ScreenError as e:
            if self.on_input:
                self.on_input(kind, fields, e.code)
            raise
        self.inputs.append(f"{kind} {said}")
        if self.on_input:
            self.on_input(kind, fields, "sent")

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        src = self.session.screen.source
        fields = {"point": (x, y), "keys": button + (" double" if double else "")}
        self._send("click", f"{x},{y}", fields, lambda w: src.click(w, x, y, button, double))

    def type(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            raise ValueError("a newline is a key, pressed on its own")
        src = self.session.screen.source
        self._send("type", text, {"text": text}, lambda w: src.type_text(w, text))

    def paste(self, text: str) -> bool:
        """One line pasted, no Enter, once it is on screen (Source.paste_line)."""
        src = self.session.screen.source
        got: list[bool] = []
        self._send("paste", text, {"text": text}, lambda w: got.append(src.paste_line(w, text)))
        return bool(got and got[0])

    def press(self, keys: str) -> None:
        src = self.session.screen.source
        self._send("press", keys, {"keys": keys}, lambda w: src.press(w, keys))

    def scroll(self, x: int, y: int, ticks: int) -> None:
        src = self.session.screen.source
        fields = {"point": (x, y), "keys": f"wheel {ticks}"}
        self._send("scroll", f"{x},{y} {ticks}", fields, lambda w: src.scroll(w, x, y, ticks))

    def settle(self, box: Box | None = None) -> None:
        self.session.screen.settle(self.title, box=box)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()
