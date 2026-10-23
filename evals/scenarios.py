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
import base64
import difflib
import hashlib
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


# The same tasks twice: with the raw tools, as an agent would string them together, and as
# one jharokha_do action. Each pair starts from the same screen.


def body(text: str) -> Any:
    """The JSON inside a result's untrusted wrapper."""
    inner = text.split('untrusted="true">\n', 1)[1].rsplit("\n</screen_text>", 1)[0]
    return json.loads(inner)


async def do(a: Agent, action: str, allow: str = "reversible", **args: Any) -> dict[str, Any]:
    out = await a.call("jharokha_do", action=action, args=args, allow=allow)
    return body(out) if 'untrusted="true">' in out else {"ok": False, "error": out}


def _minutes(t: str) -> int:
    h, m = t.replace(" ", "").split(":")[-2:]
    return int(h) * 60 + int(m[:2])


NEW_CHAT, NEW_BACK = 1, 17  # teams.chats[1], from its 17th newest message on


async def raw_new_messages(a: Agent) -> bool:
    """The messages of a chat since a time older than the screen shows: open the chat, then
    read and scroll back until a message at or before that time is on screen."""
    chat = a.desk.teams.chats[NEW_CHAT]
    since = _minutes(chat.msgs[-NEW_BACK].time)
    at = await a.center(chat.name, "teams.list")
    if at is None:
        return False
    await a.call("jharokha_click", x=at[0], y=at[1], show="none", expect="teams")
    got: dict[str, dict[str, Any]] = {}
    for _ in range(12):
        out = await a.call("jharokha_read_text", region="teams.thread", format="items")
        its = [m for m in body(out) if m.get("time")]
        for m in its:
            got.setdefault(m["time"].replace(" ", ""), m)
        if any(_minutes(m["time"]) <= since for m in its):
            break
        await a.call("jharokha_scroll", ticks=3, x=800, y=600, show="none", expect="teams")
    times = sorted((t for t in got if _minutes(t) >= since), key=_minutes)
    return times == [m.time for m in chat.msgs[-NEW_BACK:]]


async def do_new_messages(a: Agent) -> bool:
    chat = a.desk.teams.chats[NEW_CHAT]
    r = await do(a, "teams.new_messages", chat=chat.name, since=chat.msgs[-NEW_BACK].time)
    times = [m["time"].replace(" ", "") for m in r.get("items", {}).get("messages", [])]
    return r["ok"] and times == [m.time for m in chat.msgs[-NEW_BACK:]]


def _unread_ok(a: Agent, unread: str | None, subjects: list[str]) -> bool:
    o = a.desk.outlook
    want = [m.subject for m in o.mails[: o.visible()]]
    count = str(sum(m.unread for m in o.folders["Inbox"]))
    pairs = zip(subjects, want, strict=False)
    return unread == count and len(subjects) == len(want) and all(seen(s, w) for s, w in pairs)


async def raw_outlook_unread(a: Agent) -> bool:
    """Outlook's Inbox unread count and the mails its list shows, from another app."""
    await a.switch("outlook")
    await a.call("jharokha_press", keys="ctrl+1", show="none", expect="outlook")
    at = await a.center("Inbox", "outlook.folders")
    if at is None:
        return False
    await a.call("jharokha_click", x=at[0], y=at[1], show="none", expect="outlook")
    mails = body(await a.call("jharokha_read_text", region="outlook.list"))
    folders = await a.call("jharokha_read_text", region="outlook.folders")
    m = re.search(r"Inbox\s+(\d+)", folders)
    return _unread_ok(a, m[1] if m else None, [x.get("subtitle", "") for x in mails])


async def do_outlook_unread(a: Agent) -> bool:
    r = await do(a, "outlook.unread", folder="Inbox")
    subjects = [x.get("subject", "") for x in r.get("items", {}).get("messages", [])]
    return r["ok"] and _unread_ok(a, r.get("fields", {}).get("unread"), subjects)


PUT_DATA = bytes(range(256)) * 3 + "naïve résumé\r\n".encode() * 8


async def raw_put_file(a: Agent) -> bool:
    """A 900-byte file written in the shell: base64 typed in chunks with jharokha_run,
    then decoded and its SHA-256 printed and compared."""
    b64 = base64.b64encode(PUT_DATA).decode()
    chunks = [b64[i : i + 340] for i in range(0, len(b64), 340)]
    for i, c in enumerate(chunks):
        init = "$b=@{}; " if i == 0 else ""
        await a.call("jharokha_run", command=f"{init}$b[{i}]='{c}'", timeout_s=10)
    resolve = "$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath"
    write = (
        f"$p={resolve}('out.bin'); [IO.File]::WriteAllBytes($p,[Convert]::FromBase64String"
        f"((-join $b[0..{len(chunks) - 1}]))); (Get-FileHash -A SHA256 -LiteralPath $p).Hash"
    )
    out = await a.call("jharokha_run", command=write, timeout_s=10)
    digest = hashlib.sha256(PUT_DATA).hexdigest().upper()
    return seen(out, digest, 0.95) and _written(a) == PUT_DATA


def _written(a: Agent) -> bytes | None:
    return a.desk.shell.written.get(f"{a.desk.shell.cwd}\\out.bin")


async def do_put_file(a: Agent) -> bool:
    b64 = base64.b64encode(PUT_DATA).decode()
    r = await do(a, "terminal.put_file", "irreversible", path="out.bin", content_base64=b64)
    hashed = r.get("fields", {}).get("hash_check") in ("exact", "folded")
    return r["ok"] and hashed and _written(a) == PUT_DATA


async def raw_run_prompt(a: Agent) -> bool:
    """A command's output by waiting for the prompt, as jharokha_run did up to 0.19."""
    out = await a.call("jharokha_run", command="Get-Content notes.txt", shell="other")
    return out.startswith("run_ms=") and seen(out, a.desk.shell.files["notes.txt"][-1])


async def run_marker(a: Agent) -> bool:
    """The same command by its end marker, with its exit code."""
    out = await a.call("jharokha_run", command="Get-Content notes.txt")
    head = out.split("\n")[0]
    return " ok=true " in head and seen(out, a.desk.shell.files["notes.txt"][-1])


async def do_terminal_run(a: Agent) -> bool:
    r = await do(a, "terminal.run", "irreversible", command="Get-Content notes.txt")
    return r["ok"] and r.get("output") == a.desk.shell.files["notes.txt"]


DRAFT = "draft line one\ndraft line two"


async def raw_teams_draft(a: Agent) -> bool:
    """Two lines left in another chat's compose box, not sent."""
    chat = a.desk.teams.chats[2]
    at = await a.center(chat.name, "teams.list")
    if at is None:
        return False
    await a.call("jharokha_click", x=at[0], y=at[1], show="none", expect="teams")
    await a.call("jharokha_type", text=DRAFT, show="none", expect="teams.chat")
    return a.desk.teams.chat is chat and chat.draft == DRAFT.split("\n") and not a.desk.teams.sent


async def do_teams_draft(a: Agent) -> bool:
    chat = a.desk.teams.chats[2]
    r = await do(a, "teams.draft", chat=chat.name, text=DRAFT)
    return r["ok"] and chat.draft == DRAFT.split("\n") and not a.desk.teams.sent


async def do_browser_goto(a: Agent) -> bool:
    r = await do(a, "browser.goto", "irreversible", url="status.example.net")
    return r["ok"] and a.desk.browser.url == "https://status.example.net/"


async def do_incident_run_into_edge(a: Agent) -> bool:
    """terminal.run with Edge in front, even allowed to run a command: refused, wrong_app."""
    out = await a.call(
        "jharokha_do", action="terminal.run", args={"command": "Get-ChildItem"}, allow="irreversible"
    )
    return out.startswith("wrong_app:") and not a.desk.browser.searches and not a.desk.shell.log


async def do_incident_draft_sends(a: Agent) -> bool:
    """teams.draft with a newline: Shift+Enter by Teams' policy, so nothing is sent; and
    pressing Enter in Teams through an action is refused under the default allow."""
    chat = a.desk.teams.chat
    r = await do(a, "teams.draft", chat=chat.name, text=DRAFT)
    return r["ok"] and not a.desk.teams.sent and chat.draft == DRAFT.split("\n")


@dataclass(frozen=True)
class Scenario:
    name: str
    front: str
    run: Callable[[Agent], Awaitable[bool]]
    incident: bool = False  # an unsafe action, to be refused
    lease: bool = False  # give the window a lease file, in a temporary folder
    task: str | None = None  # a task done both with the raw tools and as an action


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
    Scenario("incident_second_agent", "terminal", incident_second_agent, incident=True, lease=True),
    Scenario("incident_user_typing", "terminal", incident_user_typing, incident=True),
    Scenario("raw_new_messages", "teams", raw_new_messages, task="new_messages"),
    Scenario("do_new_messages", "teams", do_new_messages, task="new_messages"),
    Scenario("raw_outlook_unread", "browser", raw_outlook_unread, task="outlook_unread"),
    Scenario("do_outlook_unread", "browser", do_outlook_unread, task="outlook_unread"),
    Scenario("raw_put_file", "terminal", raw_put_file, task="put_file"),
    Scenario("do_put_file", "terminal", do_put_file, task="put_file"),
    Scenario("raw_run_prompt", "terminal", raw_run_prompt, task="terminal_run"),
    Scenario("run_marker", "terminal", run_marker, task="terminal_run"),
    Scenario("do_terminal_run", "terminal", do_terminal_run, task="terminal_run"),
    Scenario("raw_teams_draft", "teams", raw_teams_draft, task="teams_draft"),
    Scenario("do_teams_draft", "teams", do_teams_draft, task="teams_draft"),
    Scenario("browser_goto ", "browser", browser_goto, task="browser_goto"),
    Scenario("do_browser_goto", "browser", do_browser_goto, task="browser_goto"),
    Scenario("do_incident_run_into_edge", "browser", do_incident_run_into_edge, incident=True),
    Scenario("do_incident_draft_sends", "teams", do_incident_draft_sends, incident=True),
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
