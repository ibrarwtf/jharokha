"""Generated screens shaped like the work jharokha does: a PowerShell terminal, a Teams-like
chat, an Outlook-like mailbox, lock and sign-in screens. Every name, ID and log line comes
from the lists below and a seeded random generator: nothing from a real session.

Seeds below 100 are for tuning; the eval reports on seeds from 100 up, so what is tuned is
not what is scored."""

from __future__ import annotations

import random

from evals.render import RGB, Find, Screen, Text

W, H = 1166, 1181  # the client size of the session window jharokha is used on

FIRST = ["Ana", "Ben", "Chen", "Dara", "Emil", "Farah", "Goran", "Hana", "Ivo", "Jin", "Kofi"]
LAST = ["Alder", "Brook", "Castell", "Dorn", "Ellery", "Fenwick", "Garr", "Holm", "Ivers"]
PROJECTS = ["crawler", "pricing", "quotes", "intake", "ledger", "portal"]
WORDS = (
    "the run on staging finished but two profiles returned an error after the retry "
    "window so I paused the schedule until we know why can you check the logs when "
    "you have a minute thanks the export is ready in the shared folder"
).split()


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


# Windows Terminal's default colours, and Consolas: its character advance is 0.55 of the
# font height, which sets how many columns fit before a line wraps.
TERM_BG: RGB = (12, 12, 12)
TERM_FG: RGB = (204, 204, 204)


def terminal(seed: int, px: int = 16) -> Screen:
    rng = random.Random(seed)
    proj = rng.choice(PROJECTS)
    prompt = f"PS C:\\work\\{proj}>"
    adv = round(0.5498 * px)
    left, top, right = 8, 40, W - 25  # the tab bar above, the scrollbar on the right
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
    pitch = round(px * 1.2)
    y = top
    for i, line in enumerate(rows):
        first_text[i] = len(texts)
        for c in range(0, max(1, len(line)), cols):
            if y + pitch > H - 10:
                break
            texts.append(Text(left, y, line[c : c + cols], "Consolas", px, TERM_FG))
            y += pitch
    shown = [i for i in range(len(rows)) if first_text[i] < len(texts)]
    find_list = [
        Find(q, first_text[i], s) for q, i, s in finds if i in shown and s + len(q) <= cols
    ]
    return Screen(
        f"terminal-{seed}-{px}",
        "terminal",
        W,
        H,
        TERM_BG,
        texts,
        ["Windows PowerShell"] + [rows[i] for i in shown],
        find_list,
        grid=(0, top - 4, W - 17, H - top),
    )


TEAMS_BG: RGB = (245, 245, 245)
INK: RGB = (36, 36, 36)
GREY: RGB = (97, 97, 97)


def teams(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    put(320, 10, "Search (Ctrl+E)", px, GREY)
    for i, label in enumerate(["Activity", "Chat", "Teams", "Calendar", "Files"]):
        put(12, 60 + 64 * i, label, px - 3, GREY)
    put(96, 64, "Chat", px + 4, INK, True)
    people = [_name(rng) for _ in range(6)]
    for i, who in enumerate(people):
        y = 110 + 62 * i
        at = put(96, y, who, px, INK, True)
        if i == 2:
            finds.append(Find(who, at))
        put(96, y + 22, _sentence(rng, 5), px - 1, GREY)
    other = people[0]
    put(456, 64, other, px + 2, INK, True)
    y = 120
    ref = _id(rng)
    for i in range(5):
        who = other if i % 2 == 0 else "You"
        t = f"{rng.randint(8, 17)}:{rng.randint(10, 59)}"
        put(456, y, f"{who}  {t}", px - 1, GREY)
        body = _sentence(rng, rng.randint(6, 11))
        if i == 3:
            body = f"the ticket is {ref}, can you check it"
        idx = put(456, y + 24, body)
        if i == 3:
            finds.append(Find(ref, idx, body.index(ref)))
        y += 82
    compose = put(456, H - 120, "Type a message", px, GREY)
    finds.append(Find("Type a message", compose))
    return Screen(f"teams-{seed}-{px}", "teams", W, H, TEAMS_BG, texts, truth, finds)


def outlook(seed: int, px: int = 14) -> Screen:
    rng = random.Random(seed)
    texts: list[Text] = []
    truth: list[str] = []
    finds: list[Find] = []

    def put(x: int, y: int, s: str, size: int = px, color: RGB = INK, bold: bool = False) -> int:
        texts.append(Text(x, y, s, "Segoe UI", size, color, bold))
        truth.append(s)
        return len(texts) - 1

    for i, folder in enumerate(["Inbox", "Drafts", "Sent Items", "Archive", "Deleted Items"]):
        put(16, 40 + 34 * i, folder, px)
    subjects = []
    for i in range(6):
        y = 40 + 74 * i
        who = _name(rng)
        subject = rng.choice(["Run report", "Profile export", "Access request", "Invite"])
        subject += f" {rng.randint(100, 999)}"
        subjects.append((put(200, y, who, px, INK, True), subject))
        put(200, y + 22, subject, px)
        put(200, y + 44, _sentence(rng, 5), px - 1, GREY)
    first, subj = subjects[0][0], subjects[0][1]
    put(560, 40, subj, px + 4, INK, True)
    put(560, 80, f"{texts[first].text}  Thu {rng.randint(8, 17)}:{rng.randint(10, 59)}", px - 1)
    for i in range(5):
        put(560, 130 + 28 * i, _sentence(rng, 8), px)
    finds.append(Find(subj, first + 1))
    finds.append(Find("Archive", 3))
    return Screen(f"outlook-{seed}-{px}", "outlook", W, H, (255, 255, 255), texts, truth, finds)


LOCK_BG: RGB = (16, 40, 72)
WHITE: RGB = (255, 255, 255)


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
    return Screen(f"lock-{seed}-v{variant}", "lock", W, H, LOCK_BG, texts, lines, locked=True)


def ids(seed: int, px: int = 16) -> Screen:
    rng = random.Random(seed)
    lines = [
        f"Ticket {_id(rng)} assigned to {_name(rng)}",
        f"Host VM-PRD-{rng.choice(['0l7', 'O17', '01I'])} is up",
        f"Serial {''.join(rng.choice('5S8B0O1I') for _ in range(8))}",
        f"Order AB{rng.randint(10, 99)} shipped, AB{rng.randint(10, 99)} pending",
    ]
    texts = [Text(20, 20 + 30 * i, s, "Segoe UI", px) for i, s in enumerate(lines)]
    return Screen(f"ids-{seed}-{px}", "ids", W, H, (255, 255, 255), texts, lines)


# The layouts file an eval reads with: one table per app, anchored on text the generator
# always draws.
LAYOUTS = """
[teams]
anchor = "Search (Ctrl+E)"
focus = "thread"
list = [0.07, 0.04, 0.3, 0.9]
thread = [0.38, 0.08, 0.62, 0.8]

[outlook]
anchor = "Inbox"
reading = [0.47, 0, 0.53, 1]

[terminal]
anchor = "Windows PowerShell"
grid = "body"
focus = "body"
body = [0, 0.03, 0.985, 0.97]
"""


def suite(quick: bool) -> list[Screen]:
    """Quick: one of each kind at the size the session shows; full: three sizes, more
    seeds, every lock variant."""
    seeds = [100] if quick else [100, 101, 102]
    sizes = [16] if quick else [13, 16, 20]
    small = [14] if quick else [12, 14, 16]
    out: list[Screen] = []
    for s in seeds:
        out += [terminal(s, px) for px in sizes]
        out += [teams(s, px) for px in small]
        out += [outlook(s, px) for px in small]
        out += [ids(s, px) for px in sizes]
    out += [lock(100 + v, v) for v in range(5 if not quick else 5)]
    return out
