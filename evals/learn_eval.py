"""Score learning apps from the screen (core/learn.py) on the generated app screens.

    uv run python -m evals.learn_eval [--held N] [--clean] [--show]

Each app (terminal, Teams chat, Outlook mail, Outlook calendar, the browser) is treated as
unknown: its built-in definition is hidden and learned from seed 1 (`one`), or from seed 1
then verified on seed 2 and refined (`verified`). Outlook's calendar is learned as a new view
of the Outlook learned from mail. Frames are drawn with GDI and the codec pass (`--clean`:
without it) and read by the real Windows OCR. Scored on held-out seeds 100 and up:

- id: the learned app and view win identification against the other built-in apps; conf
  is the mean confidence; fp the share of other apps' frames taken for the learned app
- all_id: the same with every app learned at once and no built-in app but generic
- regions: mean best IoU of each built-in region placed on a pane with a learned region;
  panes: the same against the panes the screen drew (taskbar left out)
- readers: share of built-in regions whose best-matching learned region reads the same way
- anchors: share of chosen anchors that every held-out screen draws where they belong, so
  UI text and not content; the others are listed. seen: share of (anchor, frame) pairs
  where OCR read the anchor there (small text at 12 px is missed by OCR, not by learning)
- problems: verify's problems on the held-out frames

Then onboarding (`--onboard` alone): a ticket queue no built-in knows (evals.sim.tickets) on
the simulated session, taught through the MCP tools only, as an agent with the user would:
status, scan, name_icons (tooltips), propose with names, a scroll and verify, zoom, save
(the change shown) and save with confirm, then status identifies it, its list reads as
items, a hand-written action (below) opens a ticket by clicking a named icon and an item,
and a two-line reply typed into it stays a draft, Enter held back by the default policy the
save wrote. Reported: each goal, calls, tokens returned, wall time, and the session's events
(nothing sent, no click before the save)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import mean
from typing import Any

from evals.render import Screen, render
from evals.screens import TASKBAR, TASKBAR_Y, browser, calendar, ids, outlook, teams, terminal
from evals.sim.desktop import Desktop
from jharokha.core import apps, learn
from jharokha.core.apps import App
from jharokha.core.identify import identify
from jharokha.core.models import Box, iou
from jharokha.core.text import fold
from jharokha.win.ocr import Ocr

KINDS: dict[str, tuple[Callable[[int, int], Screen], str, str]] = {
    "terminal": (terminal, "terminal", "shell"),
    "teams": (teams, "teams", "chat"),
    "mail": (outlook, "outlook", "mail"),
    "calendar": (calendar, "outlook", "calendar"),
    "browser": (browser, "browser", "page"),
}
# Text sizes: learned at the size the session shows, held out at all three.
SIZES = {"terminal": (20, 16, 24)}
SMALL = (14, 12, 16)
MODES = ("one", "verified")
Frame = tuple[int, int]  # seed, size


class Frames:
    """Scans of generated screens, each drawn and read once."""

    def __init__(self, codec: bool) -> None:
        self.ocr, self.codec = Ocr(), codec
        self.cache: dict[tuple[str, Frame], tuple[learn.Scan, Screen]] = {}
        self.scan_ms: list[float] = []

    def get(self, kind: str, at: Frame) -> tuple[learn.Scan, Screen]:
        if (kind, at) not in self.cache:
            screen = KINDS[kind][0](*at)
            frame, _ = render(screen, "gdi", self.codec)
            lines = self.ocr.recognize(frame)
            t = time.perf_counter()
            s = learn.scan(frame, lines)
            self.scan_ms.append((time.perf_counter() - t) * 1000)
            self.cache[(kind, at)] = (s, screen)
        return self.cache[(kind, at)]


def sizes(kind: str) -> tuple[int, ...]:
    return SIZES.get(kind, SMALL)


def held_out(kind: str, seeds: list[int], all_sizes: bool) -> list[Frame]:
    return [(s, px) for s in seeds for px in (sizes(kind) if all_sizes else sizes(kind)[:1])]


def builtin_apps() -> dict[str, App]:
    return {a.id: a for d in apps.builtin() for a in d.apps.values()}


def learn_one(
    frames: Frames, kind: str, mode: str, known: App | None, show: bool
) -> learn.Proposal:
    _, app_id, view = KINDS[kind]
    px = sizes(kind)[0]
    s1, _ = frames.get(kind, (1, px))
    p = learn.propose(s1, app_id, view, known=known)
    if mode == "verified":
        s2, _ = frames.get(kind, (2, px))
        others = [a for a in builtin_apps().values() if a.id != app_id]
        v = learn.verify(p.app, view, [s1, s2], others)
        p = learn.refine(p, v)
    if show:
        print(learn.describe(s1))
        print(p.text)
    return p


def ready_while_loading(frames: Frames, known: App | None) -> tuple[str | None, list[str]]:
    """Outlook's calendar in the simulated session, captured as it loads (the ribbon at
    once, the calendars at 0.3 s, the grid and "Today" at 1.0 s): the ready cue verify
    finds, and its problems on the frames before the view is whole."""
    clock = [0.0]
    desk = Desktop(seed=100, front="outlook", clock=lambda: clock[0])
    desk.press("ctrl+2")
    scans = []
    for t in (0.0, 0.5, 1.5):
        clock[0] = t
        desk.tick()
        frame, _ = render(desk.screen(), "gdi", frames.codec)
        scans.append(learn.scan(frame, frames.ocr.recognize(frame)))
    p = learn.propose(scans[-1], "outlook", "calendar", known=known)
    v = learn.verify(p.app, "calendar", scans, loading=True)
    return learn.refine(p, v).app.views["calendar"].ready, list(v.problems)


def placed(app: App, view: str, s: learn.Scan) -> dict[str, tuple[Box, str]]:
    out = {}
    for r in app.views[view].regions.values():
        got = apps.place(r, list(s.raw), s.width, s.height)
        if got:
            out[r.name] = got
    return out


def score(
    frames: Frames,
    kind: str,
    p: learn.Proposal,
    held: list[int],
    all_sizes: bool,
    others: list[App],
) -> dict[str, object]:
    _, app_id, view = KINDS[kind]
    truth = builtin_apps()[app_id]
    pool = [*others, p.app]
    ids, confs, region_iou, pane_iou, readers = [], [], [], [], []
    stable = dict.fromkeys(p.anchors, True)
    seen: list[bool] = []
    scans = []
    for at in held_out(kind, held, all_sizes):
        s, screen = frames.get(kind, at)
        scans.append(s)
        who = identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)
        ok = (who.app, who.view) == (app_id, view)
        ids.append(ok)
        if ok:
            confs.append(who.confidence)
        mine = placed(p.app, view, s)
        for r in truth.views[view].regions.values():
            if not r.at:
                continue  # a box-only part (a compose box) is not a pane to learn
            want = apps.place(r, list(s.raw), s.width, s.height)
            if not want:
                continue
            target = want[0]
            best = max(mine.items(), key=lambda kv: iou(kv[1][0], target), default=None)
            got = iou(best[1][0], target) if best else 0.0
            region_iou.append(got)
            if best and got >= 0.5:
                readers.append(p.app.views[view].regions[best[0]].reader == r.reader)
            else:
                readers.append(False)
        for pane in screen.panes:
            if pane != TASKBAR:
                pane_iou.append(max((iou(b, pane) for b, _ in mine.values()), default=0.0))
        for a in p.anchors:
            cue = next(
                (c for c in p.app.cues + p.app.views[view].cues if c.text and c.text[0] == a),
                None,
            )
            if cue is None:
                continue
            d = learn._distance(cue, s)
            seen.append(d is not None and d <= cue.near)
            # Content is text the screen did not draw where the anchor belongs; OCR missing
            # a label drawn there is counted in `seen`, not here.
            key = fold(a).replace(" ", "")
            drawn = any(
                key in fold(t.text).replace(" ", "")
                and min(abs(t.x / s.width - x) + abs(t.y / s.height - y) for x, y in cue.at)
                <= 2 * cue.near
                for t in screen.texts
            )
            stable[a] = stable[a] and drawn
    fp = []
    for other in KINDS:
        if KINDS[other][1] == app_id:
            continue
        for at in held_out(other, held, all_sizes):
            s, _ = frames.get(other, at)
            who = identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)
            fp.append(who.app == app_id)
    v = learn.verify(p.app, view, scans, others)
    return {
        "id": mean(ids),
        "conf": mean(confs) if confs else 0.0,
        "fp": mean(fp),
        "regions": mean(region_iou) if region_iou else 0.0,
        "panes": mean(pane_iou) if pane_iou else 0.0,
        "readers": mean(readers) if readers else 0.0,
        "anchors": mean(stable.values()) if stable else 0.0,
        "seen": mean(seen) if seen else 0.0,
        "n_anchors": len(stable),
        "content": [a for a, ok in stable.items() if not ok],
        "problems": len(v.problems),
    }


# Onboarding a new app through the tools

# Written by hand once the app is learned, as a user would add it to apps.toml: refresh the
# queue by its icon (a spot learned from its tooltip), open a ticket by its title in the
# list region, and read the detail region.
TICKETS_ACTION = """format = 1
[action.tickets.open]
summary = "Refresh the queue, open the ticket with this title and return its details."
max_risk = "reversible"
args.title = { type = "text", max_len = 100 }

[[action.tickets.open.step]]
do = "focus"
switch = true

[[action.tickets.open.step]]
do = "click"
target = { spot = "refresh" }
risk = "reversible"

[[action.tickets.open.step]]
do = "click"
target = { text = "{title}", region = "queue" }
risk = "reversible"
expect = { text = "{title}", region = "detail" }

[[action.tickets.open.step]]
do = "read"
region = "detail"
into = "ticket"
"""


def names_from(scan: str, tips: str) -> dict[str, str]:
    """What an agent would name, from the scan and name_icons as the tools return them: the
    list pane `queue`, the focus pane `detail`, each icon by its tooltip."""
    names: dict[str, str] = {}
    for m in re.finditer(r"^P(\d+) (\w+) reader=\w+( focus)?", scan, re.M):
        if m[3]:
            names[f"P{m[1]}"] = "detail"
        elif m[2] == "list":
            names[f"P{m[1]}"] = "queue"
    if m := re.search(r"^names: (\{.*\})$", tips, re.M):
        names |= json.loads(m[1])
    return names


@dataclass
class Onboarding:
    goals: dict[str, bool] = field(default_factory=dict)
    steps: list[tuple[str, int, int]] = field(default_factory=list)  # step, calls, tokens
    calls: int = 0
    tokens: int = 0
    wall_s: float = 0.0
    before_save: list[str] = field(default_factory=list)  # the session's events till then
    sent: int = 0
    lines: int = 0  # of the apps.toml written


async def onboard(codec: bool, seed: int = 100) -> Onboarding:
    from mcp import Client

    from evals.scenarios import Agent, body, seen
    from evals.sim import SimSource
    from jharokha.core import actions
    from jharokha.server import build_server

    src = SimSource(Desktop(seed, front="tickets", tickets=True), codec=codec)
    path = Path(tempfile.mkdtemp()) / "apps.toml"
    acts = {**actions.builtin(), **actions.parse(TICKETS_ACTION, "hand-written")}
    server = build_server(src, True, src.catalog(), None, acts, path)
    o = Onboarding()
    goals = o.goals
    t0 = time.perf_counter()
    async with Client(server) as c:
        a = Agent(c, src)

        async def step(name: str, tool: str, **kw: Any) -> str:
            calls, tokens = a.calls, a.tokens
            out = await a.call(tool, **kw)
            o.steps.append((name, a.calls - calls, a.tokens - tokens))
            return out

        s = json.loads(await step("status", "jharokha_status"))
        goals["unknown at first"] = s["app"] == "generic"
        scan = await step("scan", "jharokha_learn", step="scan", app="tickets", view="queue")
        tips = await step("name_icons", "jharokha_learn", step="name_icons")
        names = names_from(scan, tips)
        goals["icons named by tooltip"] = sum(k.startswith("I") for k in names) == 5
        r = await step("propose", "jharokha_learn", step="propose", names=names)
        goals["proposal identifies"] = " ok, confidence 1.0" in r
        await step("scroll", "jharokha_act", op="scroll", target=[200, 600], ticks=-3, show="none")
        r = await step("verify", "jharokha_learn", step="verify")
        goals["verified after a scroll"] = "problems:\n(none)" in r
        r = await step("zoom", "jharokha_learn", step="zoom", steps="-1..1")
        goals["zoom variants, zoom put back"] = (
            "back to step +0" in r and src.desk.tickets.zoom == 0
        )
        r = await step("save (shown)", "jharokha_learn", step="save")
        goals["save shows the change first"] = r.startswith("not saved yet") and not path.exists()
        goals["the change shows the default policy"] = (
            "+[app.tickets.policy]" in r
            and '+irreversible_keys = ["enter", "ctrl+enter", "alt+s"]' in r
        )
        o.before_save = sorted({e.kind for e in src.desk.events})
        r = await step("save (confirm)", "jharokha_learn", step="save", confirm=True)
        goals["saved"] = r.startswith("saved tickets.queue")
        s = json.loads(await step("status", "jharokha_status"))
        goals["status identifies it"] = (s["app"], s["view"]) == ("tickets", "queue")
        r = await step("read list", "jharokha_read", region="tickets.queue")
        items = body(r) if "reader=list" in r else []
        shown = [src.desk.tickets.tickets[i].title for i, _ in src.desk.tickets.shown()]
        goals["list read as items"] = len(items) == len(shown) and all(
            seen(json.dumps(items), t) for t in shown
        )
        want = shown[2]
        r = await step("do", "jharokha_do", action="tickets.open", args={"title": want})
        out = body(r) if 'untrusted="true">' in r else {}
        detail = "\n".join(out.get("lines", {}).get("ticket", []))
        t = src.desk.tickets
        goals["action opened it by a learned spot"] = (
            out.get("ok") is True
            and t.tickets[t.open].title == want
            and seen(detail, "Status: Open")
            and any(e.kind == "refresh" for e in src.desk.events)
        )
        # The reply box sends on Enter; the learned app holds it back by default.
        reply = "Looking into it.\nBack within the hour."
        box = [800, TASKBAR_Y - 40]
        kw = {"show": "none", "expect": "tickets"}
        await step("type a reply", "jharokha_act", op="type", target=box, text=reply, **kw)
        r = await step("press enter", "jharokha_act", op="press", keys="enter", **kw)
        goals["enter held back in the learned app"] = (
            r.startswith("irreversible:") and t.reply == reply and not t.sent
        )
    o.calls, o.tokens = a.calls, a.tokens
    o.wall_s = round(time.perf_counter() - t0, 1)
    o.sent = sum(e.kind == "sent" for e in src.desk.events)
    o.lines = path.read_text(encoding="utf-8").count("\n") if path.exists() else 0
    return o


def print_onboarding(codec: bool) -> None:
    o = asyncio.run(onboard(codec))
    print(f"onboarding a new app (ticket queue, GDI{' + codec' if codec else ''}):")
    for name, calls, tokens in o.steps:
        print(f"  {name:16} {calls} call{'s' if calls != 1 else ''} {tokens:6} tokens")
    print(
        f"  total {o.calls} calls, {o.tokens} tokens, {o.wall_s} s; goals "
        f"{sum(o.goals.values())} of {len(o.goals)}; apps.toml {o.lines} lines; the "
        f"session's events before the save: {', '.join(o.before_save) or 'none'}; "
        f"messages sent {o.sent}"
    )
    for name, ok in o.goals.items():
        if not ok:
            print(f"  missed: {name}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.learn_eval")
    ap.add_argument("--held", type=int, default=5, help="held-out seeds from 100")
    ap.add_argument("--clean", action="store_true", help="no codec pass")
    ap.add_argument("--show", action="store_true", help="print each scan and proposal")
    ap.add_argument("--one-size", action="store_true", help="held out at the learned size only")
    ap.add_argument("--onboard", action="store_true", help="only the onboarding scenario")
    args = ap.parse_args()
    if args.onboard:
        print_onboarding(not args.clean)
        return
    frames = Frames(codec=not args.clean)
    held = list(range(100, 100 + args.held))
    every = not args.one_size
    print(
        f"learn eval: GDI{'' if args.clean else ' + codec'}, held-out seeds {held}, "
        f"{'three text sizes' if every else 'the learned size'}"
    )
    cols = "id conf fp regions panes readers anchors seen problems".split()
    print(f"{'app':10} {'mode':9} " + " ".join(f"{c:>8}" for c in cols) + "  content chosen")
    for mode in MODES:
        learned: dict[str, App] = {}
        for kind in KINDS:
            _, app_id, view = KINDS[kind]
            known = learned.get(app_id) if kind == "calendar" else None
            p = learn_one(frames, kind, mode, known, args.show)
            learned[app_id] = p.app
            others = [a for a in builtin_apps().values() if a.id != app_id]
            r = score(frames, kind, p, held, every, others)
            nums = " ".join(
                f"{r[c]:>8.2f}" if isinstance(r[c], float) else f"{r[c]:>8}" for c in cols
            )
            print(f"{kind:10} {mode:9} {nums}  {r['content'] or ''} ({r['n_anchors']} anchors)")
        # Every app learned at once: nothing built in but generic to fall back on.
        pool = [builtin_apps()[apps.GENERIC], *learned.values()]
        hits = []
        for kind, (_, app_id, view) in KINDS.items():
            for at in held_out(kind, held, every):
                s, _ = frames.get(kind, at)
                who = identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)
                hits.append((who.app, who.view) == (app_id, view))
        # A screen of no app (lookalike IDs on white) must stay generic.
        none = []
        for seed in held:
            frame, _ = render(ids(seed), "gdi", frames.codec)
            s = learn.scan(frame, frames.ocr.recognize(frame))
            who = identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)
            none.append(who.app == apps.GENERIC)
        print(
            f"{'all':10} {mode:9} all_id {mean(hits):.2f} ({sum(hits)}/{len(hits)}), "
            f"no app stays generic {mean(none):.2f}"
        )
        if mode == MODES[0]:
            want = builtin_apps()
            teams_ready = learned["teams"].views["chat"].ready
            print(f"ready: teams.chat from one screen {teams_ready!r}", end="")
            print(f" (built-in {want['teams'].views['chat'].ready!r})")
            mail_only = replace(
                learned["outlook"], views={"mail": learned["outlook"].views["mail"]}
            )
            got, problems = ready_while_loading(frames, mail_only)
            print(f"ready: outlook.calendar while loading {got!r}", end="")
            print(f" (built-in {want['outlook'].views['calendar'].ready!r}); verify said:")
            for p in problems:
                print(f"  {p}")
    print(f"scan: median {sorted(frames.scan_ms)[len(frames.scan_ms) // 2]:.0f} ms a frame")
    print_onboarding(not args.clean)


if __name__ == "__main__":
    main()
