"""SimSource: the Source protocol over a simulated desktop, so the real server, the real
Windows OCR and the second engine run against apps that react to input, without a session.

A frame is the app in front drawn with GDI and ClearType (evals.render), optionally through
the codec pass, at the session's 1166x1181; it is drawn again only when something changed,
so polling an idle screen costs nothing. Reading is real: Windows OCR, the second engine
for rereads. Input changes the simulation's state; nothing reaches a real window.

Input is guarded as WindowsSource guards it, with the same decisions: lease_root gives the
window a real lease file there (another Lease on the same root and HWND, under another live
pid, is a second agent), user_idle stands in for this laptop's last-input clock (seconds,
or None for no input but ours; asked before each input), user_keys_at for the hooks that
watch while text is sent (the user presses a key while that many characters have gone;
once), and covered for what covers the window (core.guard.covered_parts)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Any

from evals.render import render
from evals.screens import H, W
from evals.sim.desktop import Desktop
from jharokha.core import apps
from jharokha.core.apps import Catalog
from jharokha.core.guard import (
    USER_IDLE_S,
    InputLeased,
    TypingInterrupted,
    UserActive,
    check_user_idle,
    refusal,
    warnings,
)
from jharokha.core.models import Box, Frame, Line, Point, WindowInfo
from jharokha.core.screen import ScreenError

if TYPE_CHECKING:
    from jharokha.win.guard import Lease
    from jharokha.win.ocr import Ocr
    from jharokha.win.recognize import Recognizer

TITLE = "Sim Remote Desktop - jharokha"
HWND = 0x51
_engines_lock = threading.Lock()


# One OCR engine and one recognizer for every SimSource in the process: the scenarios and
# tests build many.
@cache
def _ocr() -> Ocr:
    from jharokha.win.ocr import Ocr

    with _engines_lock:
        return Ocr()


@cache
def _recognizer() -> Recognizer:
    from jharokha.win.recognize import Recognizer

    with _engines_lock:
        return Recognizer()


class SimSource:
    def __init__(
        self,
        desktop: Desktop | None = None,
        codec: bool = False,
        title: str = TITLE,
        lease_root: str | os.PathLike[str] | None = None,
        user_idle: Callable[[], float | None] | None = None,
    ) -> None:
        self.desk = desktop or Desktop()
        self.codec, self.title = codec, title
        self.present, self.minimized = True, False
        self.lease_root = lease_root
        self.user_idle = user_idle or (lambda: None)
        self.user_keys_at: int | None = None
        self.covered: list[dict] = []
        self.renders = 0  # frames drawn
        self.render_s = 0.0  # time spent drawing them
        self._frame: Frame | None = None
        self._version = -1
        self._lock = threading.RLock()  # the watcher captures from its own thread

    def catalog(self) -> Catalog:
        """The built-in definitions plus the taskbar icons as session spots
        (session.taskbar.<app>), as a local apps.toml records them."""
        spots = ", ".join(
            f'"taskbar.{a}" = [{x / W:.4f}, {y / H:.4f}]' for a, (x, y) in self.desk.spots().items()
        )
        return apps.catalog(apps.parse(f"format = 1\n[calibration.session]\nspots = {{ {spots} }}"))

    def window(self, window_title: str | None) -> WindowInfo | None:
        if not self.present or (window_title and window_title.lower() not in self.title.lower()):
            return None
        return WindowInfo(HWND, self.title, "sim.exe", self.minimized, True, (W, H), 1.25)

    def candidates(self) -> list[str]:
        return [self.title] if self.present else []

    def frame(self, w: WindowInfo) -> Frame:
        with self._lock:
            self.desk.tick()
            if self._frame is None or self.desk.version != self._version:
                t = time.perf_counter()
                self._version = self.desk.version
                f, _ = render(self.desk.screen(), "gdi", self.codec)
                self.renders += 1
                self._frame = Frame(f.width, f.height, f.bgra, self.renders, time.time())
                self.render_s += time.perf_counter() - t
            return self._frame

    def ocr(self, frame: Frame, region: Box | None) -> list[Line]:
        # As WindowsSource: a small region is a zoom, upscaled so small text resolves.
        small = region is not None and region[2] * region[3] * 4 <= frame.width * frame.height
        return _ocr().recognize(frame, region, upscale=2.0 if small else 1.0)

    def reread(self, frame: Frame, boxes: list[Box], margin: float = 0.5) -> list[str]:
        return _recognizer().read(frame, boxes, margin)

    # Input, guarded

    def _lease(self) -> Lease | None:
        from jharokha.win.guard import Lease

        return Lease(HWND, root=self.lease_root) if self.lease_root is not None else None

    def _guard(self) -> None:
        try:
            if (lease := self._lease()) is not None:
                lease.take()
            check_user_idle(self.user_idle())
        except (InputLeased, UserActive) as e:
            raise ScreenError(*refusal(e)) from e

    def guard_state(self, w: WindowInfo) -> dict[str, Any]:
        other = None
        if (lease := self._lease()) is not None and (e := lease.other()) is not None:
            other = {"pid": e.owner_pid, "idle_s": round(e.idle_s, 1)}
        return {
            "warnings": warnings(self.covered),
            "input": {"lease": other, "user_idle_s": self.user_idle()},
        }

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        with self._lock:
            self._guard()
            self.desk.scroll(x, y, ticks)

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        with self._lock:
            self._guard()
            self.desk.click(x, y, button, double)

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        with self._lock:
            self._guard()
            # As win/input sends: stopped before the next character (a paste before its
            # final Enter) once the hooks have seen the user's key.
            n, self.user_keys_at = self.user_keys_at, None
            if paste:
                n = len(text) - 1 if n is not None and text.endswith("\n") else None
            if n is None or n >= len(text):
                (self.desk.paste if paste else self.desk.type_text)(text, focus_at)
                return
            (self.desk.paste if paste else self.desk.type_text)(text[:n], focus_at)
            raise ScreenError(*refusal(TypingInterrupted(0.0, USER_IDLE_S, n, text)))

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        with self._lock:
            self._guard()
            try:
                self.desk.press(combo, focus_at)
            except ValueError as e:
                raise ScreenError("bad_key", str(e)) from e

    def hover(self, w: WindowInfo, x: int, y: int) -> None:
        with self._lock:
            self._guard()
            self.desk.hover(x, y)
