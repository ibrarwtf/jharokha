"""Generated screens shaped like the work jharokha does: a PowerShell terminal, a Teams-like
chat, an Outlook-like mailbox and calendar, an Edge-like browser, lock and sign-in screens.
Each draws the generic UI text the built-in app definitions recognise it by. Every name,
ID, URL and log line comes from the lists below and a seeded random generator: nothing from
a real session.

Pane backgrounds, divider lines and the session's taskbar use the colours and positions
measured on a live session at 125% scaling; each screen records its true pane boxes.

Seeds below 100 are for tuning; the eval reports on seeds from 100 up, so what is tuned is
not what is scored."""

from __future__ import annotations

import math
import random

from PIL import ImageFont

from evals import items as truth_items
from evals.render import CASCADIA, RGB, Find, Screen, Text
from jharokha.core.models import Box

W, H = 1166, 1181  # the client size of the session window jharokha is used on

FIRST = ["Ana", "Ben", "Chen", "Dara", "Emil", "Farah", "Goran", "Hana", "Ivo", "Jin", "Kofi"]
LAST = ["Alder", "Brook", "Castell", "Dorn", "Ellery", "Fenwick", "Garr", "Holm", "Ivers"]
PROJECTS = ["crawler", "pricing", "quotes", "intake", "ledger", "portal"]
WORDS = (
    "the run on staging finished but two profiles returned an error after the retry "
    "window so I paused the schedule until we know why can you check the logs when "
    "you have a minute thanks the export is ready in the shared folder"
).split()

INK: RGB = (36, 36, 36)
GREY: RGB = (97, 97, 97)
WHITE: RGB = (255, 255, 255)
Rects = list[tuple[Box, RGB]]


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _id(rng: random.Random) -> str:
    """IDs where 0/O, 1/l/I, 5/S and 8/B sit next to each other."""
    return rng.choice(["INC", "REQ", "CHG"]) + "".join(rng.choice("0O1I5S8B") for _ in range(7))


def _sentence(rng: random.Random, n: int) -> str:
    start = rng.randrange(len(WORDS) - n)
    s = " ".join(WORDS[start : start + n])
    return s[0].upper() + s[1:]


# The session's own taskbar, light grey across the bottom under every app, with its clock.
TASKBAR_Y = 1121
TASKBAR: Box = (0, TASKBAR_Y, W, H - TASKBAR_Y)


def _taskbar(rng: random.Random) -> tuple[Rects, list[Text]]:
    clock = f"{rng.randint(8, 17)}:{rng.randint(10, 59)}"
    date = f"10/{rng.randint(10, 28)}/2026"
    texts = [
        Text(1092, TASKBAR_Y + 10, clock, "Segoe UI", 12, INK),
        Text(1074, TASKBAR_Y + 32, date, "Segoe UI", 12, INK),
    ]
    return [(TASKBAR, (238, 238, 238))], texts


# Windows Terminal: its default background and text colours, Cascadia Mono, rows 1.15 font
# heights apart, a tab bar above a divider at y=38 and a scrollbar at x=1156-1160.
TERM_BG: RGB = (12, 12, 12)
TERM_FG: RGB = (204, 204, 204)


def terminal(seed: int, px: int = 20) -> Screen:
    rng = random.Random(seed)
    proj = rng.choice(PROJECTS)
    prompt = f"PS C:\\work\\{proj}>"
    adv = math.ceil(ImageFont.truetype(str(CASCADIA), px).getlength("M" * 100) / 100)
    left, top, right = 8, 46, 1150
    cols = (right - left) // adv
    rows: list[str] = []  # logical lines, as the shell printed them
    finds: list[tuple[str, int, int]] = []  # query, logical line, start

    def out(line: str, find: str | None = None) -> None:
        if find is not None:
            finds.append((find, len(rows), line.index(find)))
        rows.append(line)

    for kind in rng.sample(["git", "dir", "sls", "log", "json", "wrap"], 3):
        if kind == "git":
            out(f"{prompt} git log --oneline -5")
            hashes = [_hex(rng, 7) for _ in range(5)]
            for i, h in enumerate(hashes):
                msg = rng.choice(["fix retry window", "add profile 07", "bump timeout", "log 400s"])
                out(f"{h} {msg}", h if i == 2 else None)
        elif kind == "dir":
            out(f"{prompt} Get-ChildItem output | select Mode, LastWriteTime, Length, Name")
            out("Mode                 LastWriteTime         Length Name")
            out("----                 -------------         ------ ----")
            for _ in range(4):
                day, size = rng.randint(10, 28), rng.randint(900, 90000)
                f = f"profile_{rng.randint(1, 15):02d}.json"
                out(f"-a---          10/{day}/2026  9:{rng.randint(10, 59)} AM {size:>14} {f}")
        elif kind == "sls":
            out(f"{prompt} sls max_retries -r src")
            for b in ("scraping_browser", "chromium"):
                n = rng.randint(1, 5)
                out(f"src\\{proj}\\{b}.py:{rng.randint(20, 90)}:    max_retries = {n}", f"{b}.py")
        elif kind == "log":
            out(f"{prompt} Get-Content run.log -Tail 5")
            for _ in range(5):
                p, code = rng.randint(1, 15), rng.choice([200, 200, 400, 429])
                level = "ERROR" if code >= 400 else "INFO"
                t = f"09:{rng.randint(10, 59)}:{rng.randint(10, 59)}"
                took = f"{rng.randint(1, 9)}.{rng.randint(10, 99)}s"
                out(f"2026-10-22 {t} {level} profile {p:02d} status {code} in {took}")
        elif kind == "json":
            out(f"{prompt} Get-Content meta.json")
            ref = _id(rng)
            out("{")
            out(f'  "profile": "P{rng.randint(1, 15):02d}",')
            out(f'  "ticket": "{ref}",', ref)
            out(f'  "elapsed": {rng.randint(0, 9)}.{rng.randint(10, 99)},')
            out(f'  "status": {rng.choice([200, 400])}')
            out("}")
        else:
            out(f"{prompt} Write-Output $env:EXPORT_URL")
            path = "/".join(_hex(rng, 6) for _ in range(cols // 6 + 4))
            out(f"https://files.example.net/exports/{path}/report.csv")
    out(prompt)

    texts: list[Text] = [Text(14, 10, "Windows PowerShell", "Segoe UI", 12, TERM_FG)]
    first_text: dict[int, int] = {}  # logical line -> index of its first drawn row
    pitch = round(px * 1.15)
    y = top
    for i, line in enumerate(rows):
        first_text[i] = len(texts)
        for c in range(0, max(1, len(line)), cols):
            if y + pitch > TASKBAR_Y - 6:
                break
            texts.append(Text(left, y, line[c : c + cols], "Cascadia Mono", px, TERM_FG))
            y += pitch
    shown = [i for i in range(len(rows)) if first_text[i] < len(texts)]
    find_list = [
        Find(q, first_text[i], s) for q, i, s in finds if i in shown and s + len(q) <= cols
    ]
    bar, clock = _taskbar(rng)
    thumb = rng.randint(500, 800)
    rects: Rects = [
        ((0, 0, W, 38), (32, 32, 32)),
        ((0, 38, W, 1), (64, 64, 64)),
        ((1156, thumb, 5, TASKBAR_Y - 20 - thumb), (110, 110, 110)),
        *bar,
    ]
    return Screen(
        f"terminal-{seed}-{px}",
        "terminal",
        W,
        H,
        TERM_BG,
        texts + clock,
        ["Windows PowerShell"] + [rows[i] for i in shown] + [t.text for t in clock],
        find_list,
        grid=(0, top - 4, 1156, TASKBAR_Y - top + 4),
        rects=rects,
        panes=[(0, 0, W, 38), (0, 39, W, TASKBAR_Y - 39), TASKBAR],
        view="shell",
    )


SHELLS = [  # a tab's title and its shell's prompt
    ("Windows PowerShell", "PS C:\\work\\{proj}>"),
    ("Command Prompt", "C:\\work\\{proj}>"),
    ("Ubuntu", "dev@build:~/{proj}$"),
]


def prompt(seed: int, px: int = 20, shell: int = 0, empty: bool = False) -> Screen:
    """A terminal just opened: its tab's title over a fresh prompt and nothing else, or
    (empty) the title only, as before the prompt shows. Identified by little text."""
    rng = random.Random(seed)
    title, line = SHELLS[shell]
    line = line.format(proj=rng.choice(PROJECTS))
    texts = [Text(14, 10, title, "Segoe UI", 12, TERM_FG)]
    if not empty:
        texts.append(Text(8, 46, line, "Cascadia Mono", px, TERM_FG))
    bar, clock = _taskbar(rng)
    return Screen(
        f"prompt-{seed}-{px}-{shell}{'-empty' if empty else ''}",
        "terminal",
        W,
        H,
        TERM_BG,
        texts + clock,
        [title] + ([] if empty else [line]) + [t.text for t in clock],
        grid=(0, 42, 1156, TASKBAR_Y - 42),
        rects=[((0, 0, W, 38), (32, 32, 32)), ((0, 38, W, 1), (64, 64, 64)), *bar],
        panes=[(0, 0, W, 38), (0, 39, W, TASKBAR_Y - 39), TASKBAR],
        view="shell",
    )


# New Teams: title bar above a divider at y=62, the app rail left of a divider at x=88, chat
# and thread headers above a divider at y=142, and a chat list told from its thread only by
# background colour.
TEAMS_BAR: RGB = (235, 235, 235)
TEAMS_LIST: RGB = (245, 245, 245)
DIVIDER: RGB = (224, 224, 224)


def teams(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    split, body = 440, TASKBAR_Y - 63
    put(320, 20, "Search (Ctrl+E)", px, GREY)
    for i, label in enumerate(["Activity", "Chat", "Teams", "Calendar", "Files"]):
        put(12, 90 + 64 * i, label, px - 3, GREY)
    put(100, 90, "Chat", px + 4, INK, True)
    people = [_name(rng) for _ in range(6)]
    entries, msgs = [], []
    for i, who in enumerate(people):
        y = 160 + 62 * i
        at = put(100, y, who, px, INK, True)
        if i == 2:
            finds.append(Find(who, at))
        last = put(100, y + 22, _sentence(rng, 5), px - 1, GREY)
        entries.append({"title": who, "subtitle": texts[last].text, "time": ""})
    other = people[0]
    put(456, 90, other, px + 2, INK, True)
    y = 170
    ref = _id(rng)
    for i in range(5):
        who = other if i % 2 == 0 else "You"
        t = f"{rng.randint(8, 17)}:{rng.randint(10, 59)}"
        put(456, y, f"{who}  {t}", px - 1, GREY)
        text = _sentence(rng, rng.randint(6, 11))
        if i == 3:
            text = f"the ticket is {ref}, can you check it"
        idx = put(456, y + 24, text)
        if i == 3:
            finds.append(Find(ref, idx, text.index(ref)))
        msgs.append({"sender": who, "time": t, "text": text, "own": who == "You"})
        y += 82
    compose = put(456, TASKBAR_Y - 80, "Type a message", px, GREY)
    finds.append(Find("Type a message", compose))
    bar, clock = _taskbar(rng)
    texts += clock
    truth += [t.text for t in clock]
    rects: Rects = [
        ((0, 0, W, 62), TEAMS_BAR),
        ((0, 62, W, 1), DIVIDER),
        ((0, 63, 88, body), TEAMS_BAR),
        ((88, 63, 1, body), DIVIDER),
        ((89, 63, split - 89, body), TEAMS_LIST),
        ((89, 142, W - 89, 1), DIVIDER),
        *bar,
    ]
    panes = [
        (0, 0, W, 62),
        (0, 63, 88, body),
        (89, 63, split - 89, 79),
        (89, 143, split - 89, TASKBAR_Y - 143),
        (split, 63, W - split, 79),
        (split, 143, W - split, TASKBAR_Y - 143),
        TASKBAR,
    ]
    return Screen(
        f"teams-{seed}-{px}",
        "teams",
        W,
        H,
        WHITE,
        texts,
        truth,
        finds,
        rects=rects,
        panes=panes,
        view="chat",
        items={
            "teams.list": truth_items.listed("entries", entries),
            "teams.thread": truth_items.listed("messages", msgs),
        },
    )


# New Outlook: a ribbon from y=103 to 154 between dividers, the app rail left of a divider
# at x=61, then folders, the message list and, past a divider at x=500, the reading pane.
OUTLOOK_TOP: RGB = (239, 242, 246)
OUTLOOK_PANE: RGB = (245, 245, 245)
OUTLOOK_LINE: RGB = (225, 225, 225)


def outlook(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    put(480, 40, "Search", px, GREY)
    ribbon = ("New mail", "Delete", "Reply", "Forward", "Flag")
    for x, label in zip((80, 200, 300, 390, 500), ribbon, strict=True):
        put(x, 120, label, px - 1)
    for i, folder in enumerate(["Inbox", "Drafts", "Sent Items", "Archive", "Deleted Items"]):
        put(76, 175 + 34 * i, folder, px)
    subjects = []
    mails = []
    for i in range(6):
        y = 170 + 74 * i
        who = _name(rng)
        subject = rng.choice(["Run report", "Profile export", "Access request", "Invite"])
        subject += f" {rng.randint(100, 999)}"
        subjects.append((put(236, y, who, px, INK, True), subject))
        put(236, y + 22, subject, px)
        preview = put(236, y + 44, _sentence(rng, 3), px - 1, GREY)
        mails.append({"title": who, "subtitle": subject, "text": texts[preview].text, "time": ""})
    first, subj = subjects[0][0], subjects[0][1]
    put(516, 170, subj, px + 4, INK, True)
    put(516, 210, f"{texts[first].text}  Thu {rng.randint(8, 17)}:{rng.randint(10, 59)}", px - 1)
    for i in range(5):
        put(516, 260 + 28 * i, _sentence(rng, 8), px)
    finds.append(Find(subj, first + 1))
    finds.append(Find("Archive", 9))
    bar, clock = _taskbar(rng)
    texts += clock
    truth += [t.text for t in clock]
    body = TASKBAR_Y - 155
    rects: Rects = [
        ((0, 0, W, 103), OUTLOOK_TOP),
        ((0, 103, W, 1), OUTLOOK_LINE),
        ((0, 104, W, 50), (250, 250, 250)),
        ((0, 154, W, 1), OUTLOOK_LINE),
        ((0, 155, 61, body), OUTLOOK_TOP),
        ((61, 155, 1, body), OUTLOOK_LINE),
        ((62, 155, 158, body), OUTLOOK_PANE),
        ((500, 155, 1, body), OUTLOOK_LINE),
        *bar,
    ]
    panes = [
        (0, 0, W, 103),
        (0, 104, W, 50),
        (0, 155, 61, body),
        (62, 155, 158, body),
        (220, 155, 280, body),
        (501, 155, W - 501, body),
        TASKBAR,
    ]
    return Screen(
        f"outlook-{seed}-{px}",
        "outlook",
        W,
        H,
        WHITE,
        texts,
        truth,
        finds,
        rects=rects,
        panes=panes,
        view="mail",
        items={"outlook.list": truth_items.listed("mails", mails)},
    )


# New Outlook's calendar, a work week: the same ribbon and rail as mail, the calendars on
# the pane colour, a date header above a line at y=215, then hours down the left and a
# line around every day and hour.
CAL_DAY, CAL_HOUR = 165, 86
CAL_LEFT, CAL_GRID, CAL_TOP = 280, 340, 250
CAL_EVENT: RGB = (222, 236, 249)
EVENTS = ["Standup", "Pricing review", "Release check", "Profile export", "Team sync"]


def calendar(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    put(480, 40, "Search", px, GREY)
    ribbon = ("New event", "Day", "Work week", "Week", "Month")
    for x, label in zip((80, 220, 290, 410, 490), ribbon, strict=True):
        at = put(x, 120, label, px - 1)
        if label == "New event":
            finds.append(Find(label, at))
    put(76, 175, "October 2026", px, INK, True)
    put(76, 230, "My calendars", px - 1, GREY)
    for i, name in enumerate(["Calendar", "Birthdays"]):
        put(90, 262 + 32 * i, name, px)
    day0 = rng.randint(5, 26)
    put(296, 175, "Today", px)
    put(380, 175, f"October {day0}-{day0 + 4}, 2026", px + 2, INK, True)
    hours = []
    for k in range(10):
        hour = 8 + k
        label = f"{(hour - 1) % 12 + 1} {'AM' if hour < 12 else 'PM'}"
        put(288, CAL_TOP + 6 + CAL_HOUR * k, label, px - 2, GREY)
        hours.append(label)
    slots = sorted(rng.sample([(d, h) for d in range(5) for h in range(10)], 6))
    rects: Rects = []
    days, week = [], {}
    for d, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        x = CAL_GRID + CAL_DAY * d
        days.append(f"{name} {day0 + d}")
        put(x + 8, 224, days[-1], px - 1, GREY)
        for sd, h in slots:
            if sd != d:
                continue
            y = CAL_TOP + CAL_HOUR * h
            rects.append(((x + 4, y + 4, CAL_DAY - 8, CAL_HOUR - 8), CAL_EVENT))
            title = rng.choice([*EVENTS, f"1:1 {_name(rng)}"])
            week[(d, h)] = title
            at = put(x + 12, y + 12, title, px - 1)
            if not any(f.query == title for f in finds) and len(finds) < 3:
                finds.append(Find(title, at))
    bar, clock = _taskbar(rng)
    texts += clock
    truth += [t.text for t in clock]
    body = TASKBAR_Y - 155
    grid_h = TASKBAR_Y - 216
    rects = [
        ((0, 0, W, 103), OUTLOOK_TOP),
        ((0, 103, W, 1), OUTLOOK_LINE),
        ((0, 104, W, 50), (250, 250, 250)),
        ((0, 154, W, 1), OUTLOOK_LINE),
        ((0, 155, 61, body), OUTLOOK_TOP),
        ((61, 155, 1, body), OUTLOOK_LINE),
        ((62, 155, CAL_LEFT - 62, body), OUTLOOK_PANE),
        ((CAL_LEFT, 215, W - CAL_LEFT, 1), OUTLOOK_LINE),
        *[((CAL_GRID + CAL_DAY * d, 216, 1, grid_h), OUTLOOK_LINE) for d in range(5)],
        *[
            ((CAL_GRID + 1, CAL_TOP + CAL_HOUR * k, W - CAL_GRID - 1, 1), OUTLOOK_LINE)
            for k in range(11)
        ],
        *rects,
        *bar,
    ]
    panes = [
        (0, 0, W, 103),
        (0, 104, W, 50),
        (0, 155, 61, body),
        (62, 155, CAL_LEFT - 62, body),
        (CAL_LEFT, 155, W - CAL_LEFT, 60),
        (CAL_LEFT, 216, CAL_GRID - CAL_LEFT, grid_h),
        TASKBAR,
    ]
    return Screen(
        f"calendar-{seed}-{px}",
        "outlook",
        W,
        H,
        WHITE,
        texts,
        truth,
        finds,
        rects=rects,
        panes=panes,
        view="calendar",
        items={"outlook.week": week_table(hours, days, week)},
    )


def week_table(hours: list[str], days: list[str], week: dict[tuple[int, int], str]) -> dict:
    """A work week's truth cells: hours down, days across, events at (day, hour)."""
    rows = [
        [label] + [week.get((d, k), "") for d in range(len(days))] for k, label in enumerate(hours)
    ]
    return truth_items.table(["", *days], rows)


# Edge: the tab strip, the toolbar with the address box from y=49 to 94 and the favourites
# bar at y=142 on one background, a divider at y=190, and the page from y=191.
EDGE_TABS: RGB = (225, 225, 225)
EDGE_BAR: RGB = (247, 247, 247)
TITLES = ["Refund for order", "Access to share", "New laptop", "Reset MFA", "VPN drops"]


def browser(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    proj = rng.choice(PROJECTS)
    put(24, 15, f"Request queue - {proj.title()}", px - 2)
    url = f"https://{proj}.example.net/requests/queue?view=open&page={rng.randint(2, 9)}"
    at = put(140, 61, url, px)
    finds.append(Find(f"{proj}.example.net", at, url.index(proj)))
    favourites = ("Docs", "Requests", "Status board", "Wiki", "Other favourites")
    for x, label in zip((20, 100, 190, 320, 1000), favourites, strict=True):
        put(x, 134, label, px - 1)
    heading = put(40, 215, "Open requests", px + 10, INK, True)
    finds.append(Find("Open requests", heading))
    put(40, 265, _sentence(rng, 10), px)
    header = ("ID", "Title", "Owner", "Status")
    for x, label in zip((40, 190, 560, 780), header, strict=True):
        put(x, 310, label, px, GREY, True)
    rows = []
    for i in range(6):
        y = 345 + 36 * i
        ref = _id(rng)
        at = put(40, y, ref, px)
        if i == 2:
            finds.append(Find(ref, at))
        put(190, y, f"{rng.choice(TITLES)} {rng.randint(100, 999)}", px)
        put(560, y, _name(rng), px)
        put(780, y, rng.choice(["Open", "In progress", "Waiting"]), px)
        rows.append([t.text for t in texts[-4:]])
    bar, clock = _taskbar(rng)
    texts += clock
    truth += [t.text for t in clock]
    rects: Rects = [
        ((0, 0, W, 40), EDGE_TABS),
        ((8, 8, 250, 32), EDGE_BAR),
        ((0, 40, W, 150), EDGE_BAR),
        ((120, 49, 880, 45), WHITE),
        ((0, 190, W, 1), (220, 220, 220)),
        *bar,
    ]
    panes = [(0, 0, W, 40), (0, 40, W, 150), (0, 191, W, TASKBAR_Y - 191), TASKBAR]
    return Screen(
        f"browser-{seed}-{px}",
        "browser",
        W,
        H,
        WHITE,
        texts,
        truth,
        finds,
        rects=rects,
        panes=panes,
        view="page",
        items={"browser.page": truth_items.table(header, rows)},
    )


LOCK_BG: RGB = (16, 40, 72)


def lock(seed: int, variant: int) -> Screen:
    """The screens a locked or signed-out session shows, which must read as locked."""
    rng = random.Random(seed)
    who = _name(rng)
    lines = [
        [who, "Password", "Sign-in options"],
        [who, "PIN", "I forgot my PIN", "Sign-in options"],
        [f"{rng.randint(8, 17)}:{rng.randint(10, 59)}", "Friday, October 23"],
        ["Press Ctrl+Alt+Del to unlock."],
        [who, "Locked", "Password"],
    ][variant]
    texts = [
        Text(W // 2 - 8 * len(s) // 2, 420 + 70 * i, s, "Segoe UI", 28 if i == 0 else 16, WHITE)
        for i, s in enumerate(lines)
    ]
    return Screen(
        f"lock-{seed}-v{variant}", "lock", W, H, LOCK_BG, texts, lines, locked=True, panes=[]
    )


def ids(seed: int, px: int = 16) -> Screen:
    rng = random.Random(seed)
    lines = [
        f"Ticket {_id(rng)} assigned to {_name(rng)}",
        f"Host VM-PRD-{rng.choice(['0l7', 'O17', '01I'])} is up",
        f"Serial {''.join(rng.choice('5S8B0O1I') for _ in range(8))}",
        f"Order AB{rng.randint(10, 99)} shipped, AB{rng.randint(10, 99)} pending",
    ]
    texts = [Text(20, 20 + 30 * i, s, "Segoe UI", px) for i, s in enumerate(lines)]
    return Screen(f"ids-{seed}-{px}", "ids", W, H, WHITE, texts, lines)


def suite(quick: bool) -> list[Screen]:
    """Quick: one of each kind at the size the session shows; full: three sizes, more
    seeds, every lock variant."""
    seeds = [100] if quick else [100, 101, 102]
    sizes = [16] if quick else [13, 16, 20]
    term = [20] if quick else [16, 20, 24]  # 12 pt at 125%: rows 23 px apart
    small = [14] if quick else [12, 14, 16]
    out: list[Screen] = []
    for s in seeds:
        out += [terminal(s, px) for px in term]
        out += [prompt(s, px, (s - 100 + i) % len(SHELLS)) for i, px in enumerate(term)]
        out.append(prompt(s, term[-1], (s - 100) % len(SHELLS), empty=True))
        out += [teams(s, px) for px in small]
        out += [outlook(s, px) for px in small]
        out += [calendar(s, px) for px in small]
        out += [browser(s, px) for px in small]
        out += [ids(s, px) for px in sizes]
    out += [lock(100 + v, v) for v in range(5)]
    return out
