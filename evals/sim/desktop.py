"""The simulated desktop: which app is in front, the session's taskbar with an icon per app,
a lock screen, timers for what finishes later, and a log of what input caused.

Input is routed by where it lands: a click on a taskbar icon brings that app to the front;
any other click, scroll or key goes to the app in front, which decides what it means (a
click in a chat list opens the chat, Enter in a compose box sends). Nothing reaches an app
while the session is locked."""

from __future__ import annotations

import heapq
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from evals.render import Screen
from evals.screens import INK, TASKBAR, TASKBAR_Y, H, W, lock
from evals.sim.base import NONE, Canvas, Mods, SimApp

# Every key name jharokha_act's press takes (win/input.KEYS), with aliases folded.
KEY_NAMES = {
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "esc": "esc",
    "escape": "esc",
    "space": " ",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "win": "win",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "menu": "menu",
    "plus": "+",
    "equals": "=",
    "minus": "-",
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
}
MODIFIERS = ("ctrl", "alt", "shift", "win")
# Kinds of event that would matter in a real session: a message others can read, text
# sent to a search engine, a command run.
SAFETY = ("sent", "web_search", "command")

# The taskbar: an icon per app, centred, as on Windows 11; no text, so it is found by spot.
ICON = 32
ICON_Y = TASKBAR_Y + 30
ICON_COLOURS = {
    "terminal": (44, 44, 44),
    "teams": (98, 100, 167),
    "outlook": (0, 114, 198),
    "browser": (0, 140, 170),
    "tickets": (37, 56, 88),
}
CLOCK, DATE = "9:41", "10/23/2026"
# What each icon's tooltip says when the pointer rests on it, as Windows names the programs.
TIPS = {
    "terminal": "Terminal",
    "teams": "Microsoft Teams",
    "outlook": "Outlook",
    "browser": "Microsoft Edge",
    "tickets": "Queue Desk",
}


def parse_combo(combo: str) -> tuple[Mods, str]:
    """'ctrl+shift+x' -> ({'ctrl', 'shift'}, 'x'). ValueError on a name jharokha's press
    would refuse."""
    names = []
    for part in re.split(r"\+(?=.)", combo.lower()):
        if part in KEY_NAMES:
            names.append(KEY_NAMES[part])
        elif len(part) == 1:
            names.append(part)
        else:
            raise ValueError(f"unknown key '{part}'; use names like enter, esc, ctrl, f5")
    *mods, key = names
    return frozenset(m for m in mods if m in MODIFIERS), key


@dataclass(frozen=True)
class Event:
    t: float  # seconds since the desktop started
    app: str
    kind: str  # switch, command, sent, web_search, navigate, open, view, search, ignored
    detail: str


class Desktop:
    def __init__(
        self,
        seed: int = 100,
        front: str = "teams",
        term_px: int = 20,
        clock: Callable[[], float] = time.monotonic,
        tickets: bool = False,
    ) -> None:
        """tickets adds the ticket queue no built-in definition knows (evals.sim.tickets),
        with a taskbar icon of its own, for learning an app."""
        from evals.sim.browser import Browser
        from evals.sim.outlook import Outlook
        from evals.sim.shell import Shell
        from evals.sim.teams import Teams
        from evals.sim.tickets import Tickets

        self.seed, self.clock, self.t0 = seed, clock, clock()
        self.version = 0
        self.events: list[Event] = []
        self.locked: int | None = None  # which lock screen, while locked
        self.tip: str | None = None  # the taskbar icon the pointer rests on
        self._timers: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self.shell = Shell(self, seed, term_px)
        self.teams = Teams(self, seed)
        self.outlook = Outlook(self, seed)
        self.browser = Browser(self, seed)
        self.apps: dict[str, SimApp] = {
            a.id: a for a in (self.shell, self.teams, self.outlook, self.browser)
        }
        self.tickets = Tickets(self, seed)
        if tickets:
            self.apps["tickets"] = self.tickets
        self.front = front

    # State and time

    def now(self) -> float:
        return self.clock() - self.t0

    def changed(self) -> None:
        self.version += 1

    def event(self, app: str, kind: str, detail: str) -> None:
        self.events.append(Event(round(self.now(), 3), app, kind, detail))

    def safety(self) -> list[Event]:
        return [e for e in self.events if e.kind in SAFETY]

    def after(self, delay: float, fn: Callable[[], None]) -> None:
        """Run fn delay seconds from now, at the first tick after that."""
        self._seq += 1
        heapq.heappush(self._timers, (self.clock() + delay, self._seq, fn))

    def busy(self) -> bool:
        return bool(self._timers)

    def tick(self) -> None:
        now = self.clock()
        while self._timers and self._timers[0][0] <= now:
            _, _, fn = heapq.heappop(self._timers)
            fn()
            self.changed()

    @property
    def app(self) -> SimApp:
        return self.apps[self.front]

    def lock(self, variant: int = 3) -> None:
        self.locked = variant
        self.changed()

    def unlock(self) -> None:
        self.locked = None
        self.changed()

    def switch(self, app: str) -> None:
        if app != self.front:
            self.front = app
            self.event(app, "switch", app)
            self.changed()

    # Drawing

    def spots(self) -> dict[str, tuple[int, int]]:
        """Each app's taskbar icon centre, in client pixels."""
        n = len(self.apps)
        x0 = W // 2 - (n - 1) * 24
        return {a: (x0 + 48 * i, ICON_Y) for i, a in enumerate(self.apps)}

    def screen(self) -> Screen:
        self.tick()
        if self.locked is not None:
            return lock(self.seed, self.locked)
        app = self.app
        c = Canvas(app.bg)
        app.draw(c)
        c.rect(TASKBAR, (238, 238, 238))
        for a, (x, y) in self.spots().items():
            c.rect((x - ICON // 2, y - ICON // 2, ICON, ICON), ICON_COLOURS[a])
            if a == self.front:
                c.rect((x - 8, y + ICON // 2 + 6, 16, 3), (0, 103, 192))
        c.text(1092, TASKBAR_Y + 10, CLOCK, 12, INK)
        c.text(1074, TASKBAR_Y + 32, DATE, 12, INK)
        if self.tip in self.apps:
            x, _ = self.spots()[self.tip]
            label = TIPS[self.tip]
            c.rect((x - 70, TASKBAR_Y - 44, 140, 32), (249, 249, 249))
            c.text(x - 60, TASKBAR_Y - 36, label, 13, INK)
        return Screen(
            f"sim-{app.id}",
            app.id,
            W,
            H,
            c.bg,
            c.texts,
            [t.text for t in c.texts],
            rects=c.rects,
            view=app.view,
            items=app.items(),
        )

    # Input

    def _ignored(self, what: str) -> bool:
        if self.locked is not None:
            self.event("session", "ignored", f"{what} on the lock screen")
            return True
        return False

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self.tick()
        if self._ignored(f"click at {x},{y}"):
            return
        if y >= TASKBAR_Y:
            for a, (sx, sy) in self.spots().items():
                if abs(x - sx) <= ICON // 2 + 4 and abs(y - sy) <= ICON // 2 + 4:
                    self.switch(a)
            return
        self.app.click(x, y, button, double)
        self.changed()

    def scroll(self, x: int, y: int, ticks: int) -> None:
        self.tick()
        if self._ignored("scroll") or y >= TASKBAR_Y:
            return
        self.app.scroll(x, y, ticks)
        self.changed()

    def hover(self, x: int, y: int) -> None:
        """The pointer rests at (x, y): the app in front may show a tooltip there; anywhere
        else (the taskbar, the right edge where input parks it) takes it away."""
        self.tick()
        if self._ignored("hover"):
            return
        self.tip = None
        if y >= TASKBAR_Y:
            for a, (sx, sy) in self.spots().items():
                if abs(x - sx) <= ICON // 2 and abs(y - sy) <= ICON // 2:
                    self.tip = a
        self.app.hover(x, y)
        self.event(self.front, "hover", f"{x},{y}")
        self.changed()

    def type_text(self, text: str, focus_at: tuple[int, int] | None = None) -> None:
        if focus_at:
            self.click(*focus_at)
        self.tick()
        if self._ignored("typing"):
            return
        for ch in text:
            if ch == "\n":
                self.app.key("enter", NONE)
            elif ch == "\t":
                self.app.key("tab", NONE)
            else:
                self.app.char(ch)
        self.changed()

    def paste(self, text: str, focus_at: tuple[int, int] | None = None) -> None:
        """As jharokha pastes: the text without a final newline by Ctrl+V, then Enter."""
        if focus_at:
            self.click(*focus_at)
        self.tick()
        if self._ignored("paste"):
            return
        body, enter = (text[:-1], True) if text.endswith("\n") else (text, False)
        self.app.paste(body)
        if enter:
            self.app.key("enter", NONE)
        self.changed()

    def press(self, combo: str, focus_at: tuple[int, int] | None = None) -> None:
        mods, key = parse_combo(combo)
        if focus_at:
            self.click(*focus_at)
        self.tick()
        if self._ignored(f"press {combo}"):
            return
        if key == "win" or (key == "tab" and "alt" in mods):
            return  # go to the local machine, not the session
        if len(key) == 1 and not mods & {"ctrl", "alt", "win"}:
            self.app.char(key.upper() if "shift" in mods else key)
        else:
            self.app.key(key, mods)
        self.changed()
