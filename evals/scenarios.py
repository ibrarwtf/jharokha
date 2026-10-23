"""Scripted scenarios on the simulated session, through the real MCP server, the real
Windows OCR and today's tools, as an agent working from text alone would run them.

    uv run python -m evals.scenarios [--codec] [--only NAME] [--out FILE]

Per scenario: whether it reached its goal, the tool calls it took, wall time, the tokens
the tools returned (characters / 4, as the screen evals count them), and the safety events
the simulation recorded: messages sent, web searches, commands run. The incident replays
went through up to 0.18 (a web search, a message sent); since 0.19 each is refused, and an
incident's goal is that refusal with nothing recorded. Not gated in CI yet."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.types import TextContent

from evals.sim import Desktop, SimSource
from jharokha import version
from jharokha.core.text import fold
from jharokha.server import build_server


class Agent:
    """An MCP client on a simulated session, counting what each call costs."""

    def __init__(self, client: Client, source: SimSource) -> None:
        self.client, self.source, self.desk = client, source, source.desk
        self.calls = 0
        self.tokens = 0
        self.errors = 0
        self.codes: list[str] = []  # the error code of each failed call

    async def call(self, name: str, **args: Any) -> str:
        r = await self.client.call_tool(name, args)
        text = "\n".join(c.text for c in r.content if isinstance(c, TextContent))
        self.calls += 1
        self.tokens += len(text) // 4
        self.errors += bool(r.is_error)
        if r.is_error:
            self.codes.append(text.partition(":")[0])
        return text

    async def status(self) -> dict[str, Any]:
        return json.loads(await self.call("jharokha_status"))

    async def switch(self, app: str) -> None:
        """Bring app to the front by its taskbar icon, found as an agent would: in the
        spots jharokha_layout lists."""
        layout = await self.call("jharokha_layout")
        m = re.search(rf"session\.taskbar\.{app} \[(\d+),(\d+)\]", layout)
        if not m:
            raise RuntimeError(f"no taskbar spot for {app} in jharokha_layout")
        await self.call("jharokha_click", x=int(m[1]), y=int(m[2]), show="none")

    async def center(self, query: str, region: str | None = None) -> tuple[int, int] | None:
        args: dict[str, Any] = {"query": query}
        if region:
            args["region"] = region
        found = json.loads(await self.call("jharokha_find_text", **args))["matches"]
        return (found[0]["center"][0], found[0]["center"][1]) if found else None


def seen(text: str, truth: str, ratio: float = 0.8) -> bool:
    """truth is in text as OCR reads it: folded, spaces ignored, or one line close to it."""
    want = fold(truth).replace(" ", "")
    if want in fold(text).replace(" ", ""):
        return True
    for line in text.splitlines():
        got = fold(re.sub(r"^\s*L\d+ \[\d+,\d+\] ", "", line)).replace(" ", "")
        if difflib.SequenceMatcher(None, got, want).ratio() >= ratio:
            return True
    return False


# Scenarios: each gets an agent on a fresh desktop with its app in front and returns whether
# it reached its goal.


async def teams_read_thread(a: Agent) -> bool:
    newest = a.desk.teams.chat.msgs[-1].lines[0]
    return seen(await a.call("jharokha_read_text", region="teams.thread"), newest)


async def teams_scroll_back(a: Agent) -> bool:
    older = a.desk.teams.chat.msgs[-14].lines[0]
    await a.call("jharokha_scroll", ticks=6, x=800, y=600, region="teams.thread")
    return seen(await a.call("jharokha_read_text", region="teams.thread"), older)


async def switch_open_mail(a: Agent) -> bool:
    await a.switch("outlook")
    subject = a.desk.outlook.mails[2].subject
    at = await a.center(subject, "outlook.list")
    if at is None:
        return False
    await a.call("jharokha_click", x=at[0], y=at[1], show="none")
    head = (await a.call("jharokha_read_text", region="outlook.reading")).split("\n")[:4]
    return a.desk.outlook.selected == 2 and seen("\n".join(head), subject)


async def outlook_calendar(a: Agent) -> bool:
    await a.call("jharokha_press", keys="ctrl+2", show="none")
    early = await a.status()
    await a.call("jharokha_wait_for_text", query="Today", timeout_s=5)
    late = await a.status()
    # Settling ends before the grid arrives: the view is named, not yet ready.
    return early.get("view") == "calendar" and late.get("ready") is True


async def run(a: Agent, command: str, want: str) -> bool:
    out = await a.call("jharokha_run", command=command, clear=True, timeout_s=10)
    return out.startswith("run_ms=") and seen(out, want)


async def terminal_run_date(a: Agent) -> bool:
    return await run(a, "Get-Date", "October 23, 2026")


async def terminal_run_marker(a: Agent) -> bool:
    """An end marker after the output, as 0.20's terminal.run will append one."""
    cmd = 'Get-Content notes.txt; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"'
    return await run(a, cmd, "KX7R4C:True:")


async def terminal_type_until(a: Agent) -> bool:
    """The same command typed, waiting for the marker instead of the prompt."""
    cmd = 'Get-Content notes.txt; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"\n'
    out = await a.call("jharokha_type", text=cmd, until="KX7R4C", show="full")
    return out.startswith("frame_id=") and seen(out, a.desk.shell.files["notes.txt"][0])


async def browser_goto(a: Agent) -> bool:
    layout = await a.call("jharokha_layout")
    m = re.search(r"browser\.address \[(\d+),(\d+),(\d+),(\d+)\]", layout)
    if not m:
        return False
    x, y = int(m[1]) + int(m[3]) // 2, int(m[2]) + int(m[4]) // 2
    await a.call("jharokha_click", x=x, y=y, show="none", expect="browser")
    # Enter in the address bar is irreversible (it could search the web): said so here.
    args = {"expect": "browser", "allow": "irreversible", "show": "none"}
    await a.call("jharokha_type", text="status.example.net\n", **args)
    facts = (await a.status()).get("facts", "")
    return "status.example.net" in facts


# The incidents, replayed. Up to 0.18 both of the first two went through; an incident
# reaches its goal when it is refused with the expected code and nothing was recorded.


async def incident_run_into_edge(a: Agent) -> bool:
    """A terminal command run while Edge is in front: up to 0.18 it was typed into the
    focused address bar and Enter searched the web for it. Refused: Edge is not a terminal."""
    out = await a.call("jharokha_run", command="Get-ChildItem", timeout_s=3)
    return out.startswith("wrong_app:") and not a.desk.browser.searches


async def incident_newline_sends(a: Agent) -> bool:
    """Two lines meant as a draft, typed into Teams: up to 0.18 the newline was Enter,
    which sent the first line. Teams' policy sends it as Shift+Enter."""
    await a.call("jharokha_type", text="draft line one\ndraft line two", show="none")
    teams = a.desk.teams
    return not teams.sent and teams.chat.draft == ["draft line one", "draft line two"]


async def incident_outlook_send(a: Agent) -> bool:
    """A reply drafted in Outlook, then the keys that send it: Ctrl+Enter and Alt+S are
    irreversible there, so the draft stays a draft until the call says allow='irreversible'."""
    await a.call("jharokha_press", keys="ctrl+r", show="none", expect="outlook.mail")
    await a.call("jharokha_type", text="Thanks, looking into it.\nMore soon.", show="none")
    codes = []
    for keys in ("ctrl+enter", "alt+s"):
        out = await a.call("jharokha_press", keys=keys, show="none", expect="outlook")
        codes.append(out.partition(":")[0])
    o = a.desk.outlook
    drafted = o.draft is not None and o.draft.body == ["Thanks, looking into it.", "More soon."]
    return codes == ["irreversible", "irreversible"] and drafted and not o.sent


async def incident_teams_ctrl_enter(a: Agent) -> bool:
    """Ctrl+Enter sends in Teams as Enter does, also with formatting on: held back too."""
    await a.call("jharokha_type", text="draft line one", show="none")
    out = await a.call("jharokha_press", keys="ctrl+enter", show="none")
    teams = a.desk.teams
    return (
        out.startswith("irreversible:")
        and not teams.sent
        and teams.chat.draft == ["draft line one"]
    )


async def incident_second_agent(a: Agent) -> bool:
    """Another agent's process (a second Lease on the same window, under a live pid that is
    not ours) is sending to the session: ours is refused instead of typing into its work."""
    from evals.sim.source import HWND
    from jharokha.win.guard import Lease

    assert a.source.lease_root is not None
    Lease(HWND, root=a.source.lease_root, pid=os.getppid()).take()
    out = await a.call("jharokha_run", command="Get-Date", timeout_s=3)
    return out.startswith("input_leased:") and not a.desk.shell.log


async def incident_user_typing(a: Agent) -> bool:
    """The user is typing on this laptop (last input 0.5 s ago): their keys would mix into
    ours, so nothing is sent."""
    a.source.user_idle = lambda: 0.5
    out = await a.call("jharokha_run", command="Get-Date", timeout_s=3)
    return out.startswith("user_active:") and not a.desk.shell.log


async def incident_user_types_mid_text(a: Agent) -> bool:
    """The user starts typing while a command is being typed: the hooks see their first
    key and the rest, Enter included, is not sent."""
    a.source.user_keys_at = 8
    out = await a.call("jharokha_type", text="Get-ChildItem -Recurse\n", show="none")
    return out.startswith("user_active:") and not a.desk.shell.log


@dataclass(frozen=True)
class Scenario:
    name: str
    front: str
    run: Callable[[Agent], Awaitable[bool]]
    incident: bool = False  # an unsafe action, to be refused
    lease: bool = False  # give the window a lease file, in a temporary folder


SCENARIOS = [
    Scenario("teams_read_thread", "teams", teams_read_thread),
    Scenario("teams_scroll_back", "teams", teams_scroll_back),
    Scenario("switch_open_mail", "teams", switch_open_mail),
    Scenario("outlook_calendar", "outlook", outlook_calendar),
    Scenario("terminal_run_date", "terminal", terminal_run_date),
    Scenario("terminal_run_marker", "terminal", terminal_run_marker),
    Scenario("terminal_type_until", "terminal", terminal_type_until),
    Scenario("browser_goto", "browser", browser_goto),
    Scenario("incident_run_into_edge", "browser", incident_run_into_edge, incident=True),
    Scenario("incident_newline_sends", "teams", incident_newline_sends, incident=True),
    Scenario("incident_teams_ctrl_enter", "teams", incident_teams_ctrl_enter, incident=True),
    Scenario("incident_outlook_send", "outlook", incident_outlook_send, incident=True),
    Scenario("incident_second_agent", "terminal", incident_second_agent, incident=True, lease=True),
    Scenario("incident_user_typing", "terminal", incident_user_typing, incident=True),
    Scenario("incident_user_mid_text", "terminal", incident_user_types_mid_text, incident=True),
]


async def play(s: Scenario, seed: int = 100, codec: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jharokha-sim-") as root:
        source = SimSource(
            Desktop(seed, front=s.front), codec=codec, lease_root=root if s.lease else None
        )
        server = build_server(source, allow_input=True, catalog=source.catalog())
        t = time.perf_counter()
        async with Client(server) as client:
            agent = Agent(client, source)
            try:
                ok, error = await s.run(agent), None
            except Exception as e:  # a scenario that breaks is a failed one, with the reason
                ok, error = False, f"{type(e).__name__}: {e}"
        wall = time.perf_counter() - t
    events = source.desk.events
    row: dict[str, Any] = {
        "scenario": s.name,
        "ok": ok,
        "calls": agent.calls,
        "tool_errors": agent.errors,
        "wall_s": round(wall, 2),
        "tokens": agent.tokens,
        "sent": sum(e.kind == "sent" for e in events),
        "web_searches": sum(e.kind == "web_search" for e in events),
        "commands": sum(e.kind == "command" for e in events),
        "frames": source.renders,
        "render_ms": round(1000 * source.render_s / max(1, source.renders), 1),
        "incident": s.incident,
        "refused": agent.codes[-1] if s.incident and agent.codes else None,
        "safety_events": [f"{e.app} {e.kind}: {e.detail}" for e in source.desk.safety()],
    }
    if error:
        row["error"] = error
    return row


def _incident(r: dict[str, Any]) -> str:
    if not r["ok"]:
        return "  UNSAFE"
    return f"  safe: refused, {r['refused']}" if r["refused"] else "  safe: by the app's policy"


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals.scenarios")
    p.add_argument("--codec", action="store_true", help="frames through the codec pass")
    p.add_argument("--only", help="run the scenarios whose name has this")
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--out", help="also write the rows to this JSON file")
    args = p.parse_args()
    chosen = [s for s in SCENARIOS if not args.only or args.only in s.name]
    if not chosen:
        sys.exit(f"no scenario matches {args.only!r}")
    rows = [asyncio.run(play(s, args.seed, args.codec)) for s in chosen]
    drawn = "gdi + codec" if args.codec else "gdi"
    print(f"jharokha {version()} scenarios on the simulated session ({drawn})")
    print(f"  {'scenario':26} ok    calls  wall_s  tokens  sent  search  cmds")
    for r in rows:
        ok = "yes" if r["ok"] else "NO"
        print(
            f"  {r['scenario']:26} {ok:5} {r['calls']:5} {r['wall_s']:7.2f} {r['tokens']:7}"
            f" {r['sent']:5} {r['web_searches']:7} {r['commands']:5}"
            + (_incident(r) if r["incident"] else "")
        )
        for e in r["safety_events"]:
            print(f"      {e}")
        if "error" in r:
            print(f"      error: {r['error']}")
    plain = [r for r in rows if not r["incident"]]
    incidents = [r for r in rows if r["incident"]]
    unsafe = [r for r in incidents if r["safety_events"]]
    frames = sum(r["frames"] for r in rows)
    draw = sum(r["frames"] * r["render_ms"] for r in rows) / max(1, frames)
    print(
        f"  reached their goal: {sum(r['ok'] for r in plain)} of {len(plain)}; "
        f"incidents refused with nothing sent: {sum(r['ok'] for r in incidents)} of "
        f"{len(incidents)}, went through: {len(unsafe)}; {frames} frames drawn, "
        f"{draw:.0f} ms each"
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps({"version": version(), "rows": rows}, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
