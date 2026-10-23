"""Teams' chat view, simulated: the chat list, a thread of other people's messages and your
own, older messages above that the wheel scrolls back to (animated, a message at a time),
a click on a chat opening it, and a compose box that typing goes to.

Enter in the compose box SENDS what is in it, as Teams does, and records a `sent` event;
Shift+Enter adds a line, and so does Enter once format mode is on (Ctrl+Shift+X). A paste
keeps its newlines as lines. `sent` lists every message sent, so a test can assert that
nothing was. Each chat keeps its own draft."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from evals.screens import (
    DIVIDER,
    GREY,
    INK,
    TASKBAR_Y,
    TEAMS_BAR,
    TEAMS_LIST,
    WHITE,
    W,
    _id,
    _name,
    _sentence,
)
from evals.sim.base import Canvas, Mods, SimApp, fit

if TYPE_CHECKING:
    from evals.sim.desktop import Desktop

PX = 14
SPLIT = 440  # the chat list's right edge
ROW = 62  # a chat in the list
THREAD_X = 456
THREAD_TOP = 160
COMPOSE_Y = TASKBAR_Y - 80  # where "Type a message" sits
THREAD_BOTTOM = TASKBAR_Y - 110
STEP_S = 0.06  # one message of scroll animation


@dataclass
class Msg:
    who: str
    time: str
    lines: list[str]

    @property
    def height(self) -> int:
        return 24 + 22 * len(self.lines) + 36


@dataclass
class Chat:
    name: str
    msgs: list[Msg]
    draft: list[str] = field(default_factory=lambda: [""])


class Teams(SimApp):
    id = "teams"

    def __init__(self, desk: Desktop, seed: int) -> None:
        super().__init__(desk)
        rng = random.Random(seed * 7 + 2)
        names = list(dict.fromkeys(_name(rng) for _ in range(12)))[:6]
        self.chats = [_chat(rng, who, i) for i, who in enumerate(names)]
        self.open = 0
        self.focus = "compose"  # compose, search or none
        self.search = ""
        self.format = False
        self.offset = 0  # messages scrolled back from the newest
        self.target = 0
        self.sent: list[tuple[str, str]] = []  # (chat, text)

    @property
    def view(self) -> str:
        return "chat"

    @property
    def chat(self) -> Chat:
        return self.chats[self.open]

    def shown(self) -> list[tuple[int, Msg]]:
        """The messages in view and the y of each, oldest first."""
        out, bottom = [], THREAD_BOTTOM
        for m in reversed(self.chat.msgs[: len(self.chat.msgs) - self.offset]):
            if bottom - m.height < THREAD_TOP:
                break
            bottom -= m.height
            out.append((bottom + 10, m))
        return out[::-1]

    def draw(self, c: Canvas) -> None:
        body = TASKBAR_Y - 63
        c.rect((0, 0, W, 62), TEAMS_BAR)
        c.rect((0, 62, W, 1), DIVIDER)
        c.rect((0, 63, 88, body), TEAMS_BAR)
        c.rect((88, 63, 1, body), DIVIDER)
        c.rect((89, 63, SPLIT - 89, body), TEAMS_LIST)
        c.rect((89, 142, W - 89, 1), DIVIDER)
        if self.search or self.focus == "search":
            c.text(320, 20, fit(self.search, 40) or "Search", PX, INK if self.search else GREY)
        else:
            c.text(320, 20, "Search (Ctrl+E)", PX, GREY)
        for i, label in enumerate(["Activity", "Chat", "Teams", "Calendar", "Files"]):
            c.text(12, 90 + 64 * i, label, PX - 3, GREY)
        c.text(100, 90, "Chat", PX + 4, INK, True)
        for i, chat in enumerate(self.chats):
            y = THREAD_TOP + ROW * i
            if i == self.open:
                c.rect((95, y - 8, 300, 56), WHITE)  # under 90% of the list: not a divider
            c.text(100, y, chat.name, PX, INK, True)
            c.text(100, y + 22, fit(" ".join(chat.msgs[-1].lines), 36), PX - 1, GREY)
        c.text(THREAD_X, 90, self.chat.name, PX + 2, INK, True)
        for y, m in self.shown():
            c.text(THREAD_X, y, f"{m.who}  {m.time}", PX - 1, GREY)
            for k, line in enumerate(m.lines):
                c.text(THREAD_X, y + 24 + 22 * k, fit(line, 90), PX)
        draft = self.chat.draft
        if any(draft):
            for k, line in enumerate(draft[-3:]):
                c.text(THREAD_X, COMPOSE_Y - 22 * (len(draft[-3:]) - 1 - k), fit(line, 90), PX)
        else:
            c.text(THREAD_X, COMPOSE_Y, "Type a message", PX, GREY)

    # Input

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        if y < 62:
            self.focus = "search" if 300 <= x <= 700 else self.focus
        elif 89 <= x < SPLIT and y >= THREAD_TOP - 8:
            i = (y - THREAD_TOP + 8) // ROW
            if i < len(self.chats):
                self.open, self.offset, self.target = i, 0, 0
                self.focus = "compose"
        elif x >= SPLIT and y >= COMPOSE_Y - 30:
            self.focus = "compose"
        elif x >= SPLIT:
            self.focus = "none"

    def scroll(self, x: int, y: int, ticks: int) -> None:
        if x < SPLIT or not 143 <= y < COMPOSE_Y - 30:
            return
        self.target = max(0, min(len(self.chat.msgs) - 1, self.target + ticks))
        self._step()

    def _step(self) -> None:
        if self.offset != self.target:
            self.offset += 1 if self.target > self.offset else -1
            if self.offset != self.target:
                self.desk.after(STEP_S, self._step)

    def char(self, ch: str) -> None:
        if self.focus == "search":
            self.search += ch
        elif self.focus == "compose":
            self.chat.draft[-1] += ch

    def paste(self, text: str) -> None:
        if self.focus == "compose":
            first, *rest = text.replace("\r\n", "\n").split("\n")
            self.chat.draft[-1] += first
            self.chat.draft += rest
        else:
            for ch in text.replace("\n", " "):
                self.char(ch)

    def key(self, key: str, mods: Mods) -> None:
        if "ctrl" in mods and key == "e":
            self.focus = "search"
        elif "ctrl" in mods and "shift" in mods and key == "x":
            self.format = not self.format
        elif self.focus == "search":
            if key == "backspace":
                self.search = self.search[:-1]
            elif key == "esc":
                self.search, self.focus = "", "compose"
            elif key == "enter" and self.search:
                self.event("search", self.search)
        elif self.focus == "compose":
            draft = self.chat.draft
            if key == "enter" and ("shift" in mods or self.format):
                draft.append("")
            elif key == "enter":
                self.send()
            elif key == "backspace":
                if draft[-1]:
                    draft[-1] = draft[-1][:-1]
                elif len(draft) > 1:
                    draft.pop()

    def send(self) -> None:
        lines = list(self.chat.draft)
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return
        self.chat.msgs.append(Msg("You", "9:41", lines))
        self.chat.draft = [""]
        self.offset = self.target = 0
        text = "\n".join(lines)
        self.sent.append((self.chat.name, text))
        self.event("sent", f"{self.chat.name}: {text}")


def _chat(rng: random.Random, who: str, i: int) -> Chat:
    """A thread of 24 messages from 6:30 on, before the clock's 9:41, most of them theirs;
    the first chat's sixth newest names a ticket."""
    msgs = []
    for k in range(24):
        mine = rng.random() < 0.35
        t = f"{(390 + 7 * k) // 60}:{(390 + 7 * k) % 60:02d}"
        text = _sentence(rng, rng.randint(5, 10))
        if i == 0 and k == 18:
            text = f"the ticket is {_id(rng)}, can you check it"
        msgs.append(Msg("You" if mine else who, t, [text]))
    return Chat(who, msgs)
