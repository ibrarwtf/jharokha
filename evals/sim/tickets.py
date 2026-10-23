"""A ticket queue no built-in definition knows ("Queue Desk"), simulated for onboarding: a
title bar with a search box, a toolbar of five icons with no text (new, refresh, assign to
me, reply, close) that show a tooltip while the pointer rests on them, a list of open
tickets the wheel scrolls, a click on one opening it in the detail pane (fields, a
description, a reply box at its foot), and zoom on Ctrl+= / Ctrl+- / Ctrl+0 that scales the
whole app as a browser or Electron app does.

The toolbar's clicks act at once (assign sets "Assigned to: You", close closes the ticket)
and record an event; Enter or Ctrl+Enter in the reply box sends the reply (a `sent` event)
and Shift+Enter adds a line, so a test can assert that onboarding sent nothing and that a
learned app's default policy keeps a typed reply a draft."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from evals.items import cut, listed
from evals.screens import DIVIDER, FIRST, GREY, INK, LAST, TASKBAR_Y, WHITE, W, _sentence
from evals.sim.base import Canvas, Mods, SimApp, fit

if TYPE_CHECKING:
    from evals.sim.desktop import Desktop

ZOOMS = {-3: 0.8, -2: 0.85, -1: 0.9, 0: 1.0, 1: 1.1, 2: 1.2}
TITLE_BAR = (37, 56, 88)
TOOLBAR = (243, 243, 243)
LIST_BG = (250, 250, 250)
SELECTED = (222, 234, 248)
TIP_BG = (45, 45, 45)
TIP_FG = (240, 240, 240)
# The toolbar, left to right: what each icon does and its tooltip.
TOOLS = [
    ("new", "New ticket", (0, 120, 212)),
    ("refresh", "Refresh", (16, 124, 16)),
    ("assign", "Assign to me", (135, 100, 184)),
    ("reply", "Reply", (202, 80, 16)),
    ("close", "Close ticket", (164, 38, 44)),
]
ISSUES = [
    "Printer offline",
    "VPN drops hourly",
    "Password reset",
    "Laptop fan noise",
    "Shared drive full",
    "Badge not working",
    "Monitor flickers",
    "Audio echo in calls",
    "Mailbox almost full",
    "Install request",
    "Slow wifi upstairs",
    "Calendar not syncing",
    "Docking station dead",
    "Keyboard keys stuck",
    "Access to finance share",
    "New starter laptop",
]
PRIORITIES = ["Low", "Normal", "High"]


@dataclass
class Ticket:
    title: str
    requester: str
    priority: str
    opened: str
    text: list[str]
    status: str = "Open"
    assigned: str = "Nobody"


class Tickets(SimApp):
    id = "tickets"
    bg = LIST_BG

    def __init__(self, desk: Desktop, seed: int) -> None:
        super().__init__(desk)
        rng = random.Random(seed * 13 + 5)
        titles = rng.sample(ISSUES, len(ISSUES))
        self.tickets = [
            Ticket(
                t,
                f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                rng.choice(PRIORITIES),
                f"{rng.randint(7, 9)}:{rng.randint(10, 59)}",
                [_sentence(rng, rng.randint(6, 9)) for _ in range(rng.randint(2, 4))],
            )
            for t in titles
        ]
        self.open = 0
        self.top = 0  # the first ticket the list shows
        self.zoom = 0
        self.pointer: tuple[int, int] | None = None
        self.focus = "none"  # reply, search or none
        self.reply = ""
        self.search = ""
        self.sent: list[str] = []

    @property
    def view(self) -> str:
        return "queue"

    # Geometry, all of it scaled by the zoom

    @property
    def z(self) -> float:
        return ZOOMS[self.zoom]

    def s(self, v: float) -> int:
        return round(v * self.z)

    def bars(self) -> tuple[int, int, int, int]:
        """The title bar's height, the toolbar's bottom, the list header's bottom and the
        list's right edge."""
        title = self.s(48)
        tools = title + 1 + self.s(44)
        head = tools + 1 + self.s(40)
        return title, tools, head, self.s(420)

    def icons(self) -> list[tuple[str, str, tuple[int, int, int, int]]]:
        title, tools, _, _ = self.bars()
        size, step = self.s(24), self.s(40)
        y = title + 1 + (tools - title - 1 - size) // 2
        return [
            (k, tip, (self.s(16) + step * i, y, size, size)) for i, (k, tip, _) in enumerate(TOOLS)
        ]

    def row(self) -> int:
        return self.s(64)

    def shown(self) -> list[tuple[int, int]]:
        """(index, y) of each ticket in view."""
        _, _, head, _ = self.bars()
        out, y = [], head + self.s(14)
        for i in range(self.top, len(self.tickets)):
            if y + self.row() > TASKBAR_Y - 8:
                break
            out.append((i, y))
            y += self.row()
        return out

    # Drawing

    def draw(self, c: Canvas) -> None:
        title, tools, head, split = self.bars()
        px = self.s(16)
        c.rect((0, 0, W, title), TITLE_BAR)
        c.text(self.s(16), self.s(12), "Queue Desk", self.s(18), WHITE, True)
        if self.search or self.focus == "search":
            c.text(self.s(380), self.s(15), fit(self.search, 30) or "Search tickets", px, WHITE)
        else:
            c.text(self.s(380), self.s(15), "Search tickets", px, (200, 210, 225))
        c.rect((0, title, W, 1), DIVIDER)
        c.rect((0, title + 1, W, tools - title - 1), TOOLBAR)
        for (_, _, (x, y, w, h)), (_, _, colour) in zip(self.icons(), TOOLS, strict=True):
            c.rect((x, y, w, h), colour)
            c.rect((x + w // 4, y + h // 2 - 2, w // 2, 4), WHITE)  # a glyph inside
        c.rect((0, tools, W, 1), DIVIDER)
        c.rect((split, tools + 1, 1, TASKBAR_Y - tools - 1), DIVIDER)
        c.rect((split + 1, tools + 1, W - split - 1, TASKBAR_Y - tools - 1), WHITE)
        c.text(self.s(16), tools + 1 + self.s(9), "Open tickets", self.s(17), INK, True)
        c.rect((0, head, split, 1), DIVIDER)
        for i, y in self.shown():
            t = self.tickets[i]
            if i == self.open:
                # Under 90% of the list's width: a highlight, not a divider.
                c.rect((self.s(6), y - self.s(8), self.s(360), self.row() - self.s(8)), SELECTED)
            c.text(self.s(16), y, fit(t.title, 34), px, INK, True)
            c.text(self.s(16), y + self.s(24), f"{t.requester}  {t.opened}", px - 1, GREY)
        self.draw_detail(c, split)
        self.draw_tip(c)

    def draw_detail(self, c: Canvas, split: int) -> None:
        t = self.tickets[self.open]
        x = split + self.s(20)
        _, tools, _, _ = self.bars()
        y = tools + self.s(18)
        c.text(x, y, t.title, self.s(20), INK, True)
        y += self.s(38)
        for label, value in (
            ("Status", t.status),
            ("Priority", t.priority),
            ("Requester", t.requester),
            ("Assigned to", t.assigned),
        ):
            c.text(x, y, f"{label}: {value}", self.s(16), INK)
            y += self.s(28)
        y += self.s(20)
        for line in t.text:
            c.text(x, y, fit(line, 56), self.s(16), INK)
            y += self.s(24)
        foot = TASKBAR_Y - self.s(60)
        if self.reply:
            lines = self.reply.split("\n")
            for i, line in enumerate(lines):
                c.text(x, foot - self.s(24) * (len(lines) - 1 - i), fit(line, 56), self.s(16), INK)
        else:
            c.text(x, foot, "Write a reply", self.s(16), GREY)

    def draw_tip(self, c: Canvas) -> None:
        """A tooltip under the icon the pointer rests on."""
        tool = self.tool_at(*self.pointer) if self.pointer else None
        if tool is None:
            return
        _, tip, (x, y, w, h) = tool
        px = self.s(14)
        box = (x, y + h + self.s(8), round(len(tip) * px * 0.6) + self.s(16), self.s(28))
        c.rect(box, TIP_BG)
        c.text(box[0] + self.s(8), box[1] + self.s(5), tip, px, TIP_FG)

    def tool_at(self, x: int, y: int) -> tuple[str, str, tuple[int, int, int, int]] | None:
        for k, tip, (ix, iy, w, h) in self.icons():
            if ix <= x < ix + w and iy <= y < iy + h:
                return k, tip, (ix, iy, w, h)
        return None

    def items(self) -> dict[str, dict[str, Any]]:
        entries = [
            {
                "title": cut(self.tickets[i].title, 34),
                "subtitle": f"{self.tickets[i].requester}  {self.tickets[i].opened}",
                "time": "",
            }
            for i, _ in self.shown()
        ]
        return {"tickets.queue": listed("entries", entries)}

    # Input

    def hover(self, x: int, y: int) -> None:
        self.pointer = (x, y)

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        self.pointer = None  # a click parks the pointer away
        title, tools, head, split = self.bars()
        if y < title:
            self.focus = "search" if self.s(370) <= x <= self.s(700) else self.focus
        elif y < tools:
            tool = self.tool_at(x, y)
            if tool:
                self.use(tool[0])
        elif x < split and y >= head:
            for i, ty in self.shown():
                if ty - self.s(8) <= y < ty - self.s(8) + self.row():
                    self.open, self.reply = i, ""
                    self.event("open", self.tickets[i].title)
        elif x > split and y >= TASKBAR_Y - self.s(90):
            self.focus = "reply"
        elif x > split:
            self.focus = "none"

    def use(self, tool: str) -> None:
        t = self.tickets[self.open]
        if tool == "assign":
            t.assigned = "You"
        elif tool == "close":
            t.status = "Closed"
        elif tool == "reply":
            self.focus = "reply"
        self.event(tool, t.title)

    def scroll(self, x: int, y: int, ticks: int) -> None:
        if x >= self.bars()[3]:
            return
        last = max(0, len(self.tickets) - len(self.shown()))
        self.top = max(0, min(last, self.top - ticks))

    def char(self, ch: str) -> None:
        if self.focus == "reply":
            self.reply += ch
        elif self.focus == "search":
            self.search += ch

    def key(self, key: str, mods: Mods) -> None:
        if "ctrl" in mods and key in ("=", "+", "-", "0"):
            step = {"=": 1, "+": 1, "-": -1, "0": -self.zoom}[key]
            self.zoom = max(min(ZOOMS), min(max(ZOOMS), self.zoom + step))
            self.top = 0
            return
        if self.focus == "search":
            if key == "backspace":
                self.search = self.search[:-1]
            elif key == "esc":
                self.search, self.focus = "", "none"
        elif self.focus == "reply":
            if key == "backspace":
                self.reply = self.reply[:-1]
            elif key == "enter" and "shift" in mods:
                self.reply += "\n"
            elif key == "enter" and self.reply.strip():
                self.sent.append(self.reply)
                self.event("sent", f"{self.tickets[self.open].title}: {self.reply}")
                self.reply = ""
