"""New Outlook, simulated. Mail: folders (a click switches), the message list (a click opens
an item in the reading pane; the wheel scrolls the list), the reading pane. Calendar
(Ctrl+2 or the rail's Calendar; Ctrl+1 or Mail goes back): the switch takes a second, as
the real one does. The ribbon changes at once, the calendars pane comes at 0.3 s, and the
week grid with its "Today", the view's ready text, last at 1.0 s; a screen that settles in
between is the calendar view, not yet ready.

Writing (mail view): Reply or Ctrl+R answers the selected mail, New mail or Ctrl+N starts
one, in the reading pane. Typing goes to its body, where Enter adds a line; Ctrl+Enter and
Alt+S SEND it, as Outlook does, and record a `sent` event; Esc discards it. `sent` lists
every mail sent, so a test can assert that nothing was."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from evals.items import cut, listed
from evals.screens import (
    CAL_DAY,
    CAL_EVENT,
    CAL_GRID,
    CAL_HOUR,
    CAL_LEFT,
    CAL_TOP,
    EVENTS,
    GREY,
    INK,
    OUTLOOK_LINE,
    OUTLOOK_PANE,
    OUTLOOK_TOP,
    TASKBAR_Y,
    W,
    _name,
    _sentence,
    week_table,
)
from evals.sim.base import Canvas, Mods, SimApp, fit

if TYPE_CHECKING:
    from evals.sim.desktop import Desktop

PX = 14
FOLDERS = ["Inbox", "Drafts", "Sent Items", "Archive", "Deleted Items"]
SIZES = {"Inbox": 16, "Drafts": 2, "Sent Items": 5, "Archive": 6, "Deleted Items": 3}
ITEM = 74  # a message in the list
LIST_TOP = 170
SUBJECTS = ["Run report", "Profile export", "Access request", "Invite", "Build notes"]
PANES_S, GRID_S = 0.3, 1.0  # when the calendar's parts arrive after the switch


@dataclass
class Mail:
    sender: str
    subject: str
    preview: str
    time: str
    body: list[str]
    unread: bool = True


@dataclass
class Draft:
    to: str
    subject: str
    body: list[str]


class Outlook(SimApp):
    id = "outlook"

    def __init__(self, desk: Desktop, seed: int) -> None:
        super().__init__(desk)
        rng = random.Random(seed * 7 + 3)
        self.folders = {f: [_mail(rng) for _ in range(SIZES[f])] for f in FOLDERS}
        self.folder = "Inbox"
        self.selected = 0
        self.top = 0  # first item in the list's view
        self._view = "mail"
        self.stage = 2  # calendar: 0 ribbon only, 1 with the calendars pane, 2 loaded
        self._switch = 0
        self.day0 = rng.randint(5, 24)
        slots = sorted(rng.sample([(d, h) for d in range(5) for h in range(10)], 6))
        self.events = [(d, h, rng.choice([*EVENTS, f"1:1 {_name(rng)}"])) for d, h in slots]
        self.draft: Draft | None = None  # the mail being written
        self.sent: list[tuple[str, str]] = []  # (to, body)

    @property
    def view(self) -> str:
        return self._view

    @property
    def mails(self) -> list[Mail]:
        return self.folders[self.folder]

    def visible(self) -> int:
        return (TASKBAR_Y - LIST_TOP) // ITEM

    # Drawing

    def draw(self, c: Canvas) -> None:
        body = TASKBAR_Y - 155
        c.rect((0, 0, W, 103), OUTLOOK_TOP)
        c.rect((0, 103, W, 1), OUTLOOK_LINE)
        c.rect((0, 104, W, 50), (250, 250, 250))
        c.rect((0, 154, W, 1), OUTLOOK_LINE)
        c.rect((0, 155, 61, body), OUTLOOK_TOP)
        c.rect((61, 155, 1, body), OUTLOOK_LINE)
        c.text(480, 40, "Search", PX, GREY)
        c.text(8, 175, "Mail", PX - 3, INK if self._view == "mail" else GREY)
        c.text(8, 215, "Calendar", PX - 3, INK if self._view == "calendar" else GREY)
        if self._view == "mail":
            self._mail(c, body)
        else:
            self._calendar(c, body)

    def _mail(self, c: Canvas, body: int) -> None:
        c.rect((62, 155, 158, body), OUTLOOK_PANE)
        c.rect((500, 155, 1, body), OUTLOOK_LINE)
        ribbon = ("New mail", "Delete", "Reply", "Forward", "Flag")
        for x, label in zip((80, 200, 300, 390, 500), ribbon, strict=True):
            c.text(x, 120, label, PX - 1)
        for i, f in enumerate(FOLDERS):
            unread = sum(m.unread for m in self.folders[f])
            c.text(76, 175 + 34 * i, f, PX, INK, f == self.folder)
            if unread and f == "Inbox":
                c.text(186, 175 + 34 * i, str(unread), PX - 1, (0, 95, 184))
        for i, m in enumerate(self.mails[self.top : self.top + self.visible()]):
            y = LIST_TOP + ITEM * i
            if self.top + i == self.selected:
                c.rect((228, y - 6, 240, 70), (220, 234, 247))  # under 90%: not a divider
            c.text(236, y, fit(m.sender, 24), PX, INK, True)
            c.text(236, y + 22, fit(m.subject, 28), PX, INK, m.unread)
            c.text(236, y + 44, fit(m.preview, 30), PX - 1, GREY)
        if not self.mails:
            c.text(236, LIST_TOP, "Nothing in this folder", PX, GREY)
        if self.draft is not None:
            d = self.draft
            c.text(516, 170, "Send", PX, INK, True)
            c.text(596, 170, "Discard", PX)
            c.text(516, 210, f"To  {d.to}", PX - 1)
            c.text(516, 240, f"Subject  {fit(d.subject, 44)}", PX - 1)
            for i, line in enumerate(d.body[-20:]):
                c.text(516, 290 + 28 * i, fit(line, 56), PX)
            return
        if not self.mails:
            return
        m = self.mails[self.selected]
        c.text(516, 170, fit(m.subject, 50), PX + 4, INK, True)
        c.text(516, 210, f"{m.sender}  {m.time}", PX - 1)
        for i, line in enumerate(m.body):
            c.text(516, 260 + 28 * i, line, PX)

    def _calendar(self, c: Canvas, body: int) -> None:
        ribbon = ("New event", "Day", "Work week", "Week", "Month")
        for x, label in zip((80, 220, 290, 410, 490), ribbon, strict=True):
            c.text(x, 120, label, PX - 1)
        if self.stage >= 1:
            c.rect((62, 155, CAL_LEFT - 62, body), OUTLOOK_PANE)
            c.text(76, 175, "October 2026", PX, INK, True)
            c.text(76, 230, "My calendars", PX - 1, GREY)
            for i, name in enumerate(["Calendar", "Birthdays"]):
                c.text(90, 262 + 32 * i, name, PX)
        if self.stage < 2:
            return
        grid_h = TASKBAR_Y - 216
        c.rect((CAL_LEFT, 215, W - CAL_LEFT, 1), OUTLOOK_LINE)
        for d in range(5):
            c.rect((CAL_GRID + CAL_DAY * d, 216, 1, grid_h), OUTLOOK_LINE)
        for k in range(11):
            c.rect((CAL_GRID + 1, CAL_TOP + CAL_HOUR * k, W - CAL_GRID - 1, 1), OUTLOOK_LINE)
        c.text(296, 175, "Today", PX)
        c.text(380, 175, f"October {self.day0}-{self.day0 + 4}, 2026", PX + 2, INK, True)
        for k in range(10):
            hour = 8 + k
            label = f"{(hour - 1) % 12 + 1} {'AM' if hour < 12 else 'PM'}"
            c.text(288, CAL_TOP + 6 + CAL_HOUR * k, label, PX - 2, GREY)
        for d, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
            c.text(CAL_GRID + CAL_DAY * d + 8, 224, f"{name} {self.day0 + d}", PX - 1, GREY)
        for d, h, title in self.events:
            x, y = CAL_GRID + CAL_DAY * d, CAL_TOP + CAL_HOUR * h
            c.rect((x + 4, y + 4, CAL_DAY - 8, CAL_HOUR - 8), CAL_EVENT)
            c.text(x + 12, y + 12, fit(title, 18), PX - 1)

    def items(self) -> dict[str, dict[str, Any]]:
        if self._view == "calendar":
            if self.stage < 2:
                return {}
            hours = [f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}" for h in range(8, 18)]
            days = [
                f"{n} {self.day0 + d}" for d, n in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"])
            ]
            week = {(d, h): cut(t, 18) for d, h, t in self.events}
            return {"outlook.week": week_table(hours, days, week)}
        shown = self.mails[self.top : self.top + self.visible()]
        mails = [
            {
                "title": cut(m.sender, 24),
                "subtitle": cut(m.subject, 28),
                "text": cut(m.preview, 30),
                "time": "",
            }
            for m in shown
        ]
        return {"outlook.list": listed("mails", mails)}

    # Input

    def go(self, view: str) -> None:
        if view == self._view:
            return
        self._view = view
        self.event("view", view)
        self._switch += 1
        if view == "calendar":
            self.stage, n = 0, self._switch
            self.desk.after(PANES_S, lambda: self._stage(n, 1))
            self.desk.after(GRID_S, lambda: self._stage(n, 2))

    def _stage(self, switch: int, stage: int) -> None:
        if switch == self._switch:
            self.stage = stage

    def write(self, reply: bool) -> None:
        """A reply to the selected mail, or a new one, in the reading pane."""
        if self._view != "mail" or self.draft is not None:
            return
        if reply and self.mails:
            m = self.mails[self.selected]
            self.draft = Draft(m.sender, f"RE: {m.subject}", [""])
        elif not reply:
            self.draft = Draft("", "", [""])
        if self.draft is not None:
            self.event("compose", self.draft.subject or "new mail")

    def send(self) -> None:
        d = self.draft
        if d is None:
            return
        self.draft = None
        body = "\n".join(d.body).strip()
        self.sent.append((d.to, body))
        self.event("sent", f"{d.to or 'nobody'}: {d.subject}")

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        if x < 61 and 165 <= y < 200:
            self.go("mail")
        elif x < 61 and 205 <= y < 240:
            self.go("calendar")
        elif self._view != "mail":
            return
        elif 110 <= y < 140 and 70 <= x < 180:
            self.write(reply=False)
        elif 110 <= y < 140 and 290 <= x < 360:
            self.write(reply=True)
        elif self.draft is not None and 165 <= y < 195 and 510 <= x < 570:
            self.send()
        elif self.draft is not None and 165 <= y < 195 and 590 <= x < 670:
            self.draft = None
        elif 62 <= x < 220 and 165 <= y < 165 + 34 * len(FOLDERS):
            self.folder = FOLDERS[(y - 165) // 34]
            self.selected = self.top = 0
        elif 220 <= x < 500 and y >= LIST_TOP - 6:
            i = self.top + (y - LIST_TOP + 6) // ITEM
            if i < len(self.mails) and (y - LIST_TOP + 6) // ITEM < self.visible():
                self.selected = i
                self.mails[i].unread = False
                self.event("open", self.mails[i].subject)

    def scroll(self, x: int, y: int, ticks: int) -> None:
        if self._view == "mail" and 220 <= x < 500:
            most = max(0, len(self.mails) - self.visible())
            self.top = max(0, min(most, self.top - ticks))

    def char(self, ch: str) -> None:
        if self.draft is not None:
            self.draft.body[-1] += ch

    def key(self, key: str, mods: Mods) -> None:
        d = self.draft
        if "ctrl" in mods and key in ("1", "2"):
            self.go("mail" if key == "1" else "calendar")
        elif "ctrl" in mods and key in ("n", "r"):
            self.write(reply=key == "r")
        elif d is None:
            return
        elif (key == "enter" and "ctrl" in mods) or (key == "s" and "alt" in mods):
            self.send()
        elif key == "enter":
            d.body.append("")
        elif key == "esc":
            self.draft = None
        elif key == "backspace":
            if d.body[-1]:
                d.body[-1] = d.body[-1][:-1]
            elif len(d.body) > 1:
                d.body.pop()


def _mail(rng: random.Random) -> Mail:
    subject = f"{rng.choice(SUBJECTS)} {rng.randint(100, 999)}"
    t = f"{rng.choice(['Mon', 'Tue', 'Wed', 'Thu'])} {rng.randint(8, 17)}:{rng.randint(10, 59)}"
    body = [_sentence(rng, 8) for _ in range(5)]
    return Mail(_name(rng), subject, _sentence(rng, 3), t, body)
