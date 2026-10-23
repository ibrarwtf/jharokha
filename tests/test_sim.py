"""The simulated session: its apps' behaviour (fast, no OCR), then scenarios through the
real MCP server with the real Windows OCR (slow: every new frame is OCR'd), including the
incident replays, each refused since 0.19, and the guards against a second agent and
someone at this computer."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest
from mcp import Client

from evals.scenarios import Agent, seen
from evals.sim import Desktop, SimSource
from evals.sim.source import HWND
from jharokha.core.trace import Trace
from jharokha.server import build_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="GDI and Windows OCR")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def desk(front: str = "teams") -> tuple[Desktop, Clock]:
    clock = Clock()
    return Desktop(100, front=front, clock=clock), clock


def printed(d: Desktop) -> list[str]:
    return [t for t, _ in d.shell.out]


# The apps, without OCR


def test_shell_runs_end_markers_and_reports_errors_as_powershell_does():
    d, _ = desk("terminal")
    line = 'Get-Content notes.txt; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"'
    d.type_text(line + "\n")
    out = printed(d)
    assert out[0] == f"{d.shell.prompt} {line}"
    assert out[1:4] == d.shell.files["notes.txt"] and out[-1] == "KX7R4C:True:"
    d.type_text('Get-Content nope.txt; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"\n')
    out = printed(d)
    assert out[-1] == "KX7R4C:False:"
    assert any(t.startswith("Get-Content : Cannot find path") for t in out)
    d.type_text("cmd /c exit 3; $LASTEXITCODE; $?\n")
    assert printed(d)[-2:] == ["3", "True"]
    d.type_text("Remove-Item run.log\n")
    assert "The term 'Remove-Item' is not recognized" in printed(d)[-6]
    d.type_text("Get-ChildItem | select -first 2\n")
    assert printed(d)[-2:] == ["", f"    Directory: {d.shell.cwd}"]
    d.type_text("Get-Content run.log -Tail 2\n")
    assert printed(d)[-2:] == d.shell.files["run.log"][-2:]
    assert len(d.shell.log) == 6 and [e.kind for e in d.safety()] == ["command"] * 6


def test_shell_keys_sleep_and_scrollback():
    d, clock = desk("terminal")
    d.type_text("Get-Date")
    d.press("ctrl+l")
    assert d.shell.out == [] and d.shell.input == "Get-Date"
    d.press("ctrl+c")
    assert d.shell.input == "" and d.shell.log == []
    d.type_text('Start-Sleep 2; "done"\n')
    assert d.shell.running and "done" not in printed(d)
    clock.t += 2.1
    d.tick()
    assert not d.shell.running and printed(d)[-1] == "done"
    for _ in range(30):
        d.type_text("Get-Location\n")
    assert d.screen().texts[-3].text == d.shell.prompt  # the last row, above the clock
    d.scroll(600, 500, 5)
    assert d.shell.back == 15 and d.screen().texts[-3].text != d.shell.prompt
    d.press("up")
    assert d.shell.back == 0 and d.shell.input == "Get-Location"


def test_teams_enter_sends_shift_enter_and_format_mode_add_lines():
    d, _ = desk()
    t = d.teams
    d.type_text("hello")
    d.press("shift+enter")
    d.type_text("there")
    assert t.chat.draft == ["hello", "there"] and t.sent == []
    d.press("enter")
    assert t.sent == [(t.chat.name, "hello\nthere")] and t.chat.msgs[-1].lines == ["hello", "there"]
    d.press("ctrl+shift+x")
    d.type_text("a\nb")
    assert t.chat.draft == ["a", "b"] and len(t.sent) == 1
    d.press("ctrl+shift+x")
    d.paste("c\nd")  # a paste keeps its newlines
    assert t.chat.draft == ["a", "bc", "d"] and len(t.sent) == 1
    d.click(200, 160 + 62 * 3)  # the fourth chat
    assert t.open == 3 and t.chat.draft == [""] and d.events[-1].kind == "sent"
    d.click(200, 160)
    assert t.chat.draft == ["a", "bc", "d"]  # drafts are kept per chat


def test_teams_scroll_back_animates_a_message_at_a_time():
    d, clock = desk()
    t = d.teams
    newest = t.shown()[-1][1]
    d.scroll(800, 600, 4)
    assert (t.offset, t.target) == (1, 4)
    for want in (2, 3, 4, 4):
        clock.t += 0.07
        d.tick()
        assert t.offset == want
    assert newest not in [m for _, m in t.shown()]
    d.scroll(200, 600, 4)  # over the chat list: the thread stays
    assert t.target == 4


def test_outlook_calendar_arrives_in_stages_and_mail_opens_on_click():
    d, clock = desk("outlook")
    o = d.outlook
    d.click(300, 170 + 74 * 2 + 10)
    assert o.selected == 2 and not o.mails[2].unread and d.events[-1].kind == "open"
    d.scroll(300, 500, -3)
    assert o.top == 3
    d.press("ctrl+2")
    texts = lambda: [x.text for x in d.screen().texts]  # noqa: E731
    assert o.view == "calendar" and "New event" in texts() and "My calendars" not in texts()
    clock.t += 0.5
    assert "My calendars" in texts() and "Today" not in texts()
    clock.t += 0.6
    assert "Today" in texts()
    d.click(30, 180)
    assert o.view == "mail" and "Inbox" in texts()


def test_browser_navigates_a_url_and_searches_the_web_for_anything_else():
    d, _ = desk("browser")
    b = d.browser
    d.type_text("status.example.net\n")  # the address bar has focus from the start
    assert b.url == "https://status.example.net/" and b.focus == "page" and not b.searches
    d.type_text("ignored on the page")
    assert b.edit is None
    d.click(500, 70)
    d.type_text("Get-ChildItem\n")
    assert b.searches == ["Get-ChildItem"] and b.url.endswith("?q=Get-ChildItem")
    assert [e.kind for e in d.safety()] == ["web_search"]
    d.click(120, 140)  # the Requests favourite
    assert b.url == "https://requests.example.net/queue"


def test_desktop_routes_taskbar_clicks_and_ignores_input_when_locked():
    d, _ = desk("terminal")
    spots = d.spots()
    d.click(*spots["outlook"])
    assert d.front == "outlook" and d.events[-1].kind == "switch"
    d.click(10, 1150)  # taskbar, no icon
    assert d.front == "outlook"
    d.lock(3)
    d.click(*spots["teams"])
    d.type_text("x\n")
    assert d.front == "outlook" and d.events[-1].kind == "ignored"
    assert "Press Ctrl+Alt+Del to unlock." in [t.text for t in d.screen().texts]
    d.unlock()
    with pytest.raises(ValueError, match="unknown key 'nope'"):
        d.press("ctrl+nope")


def test_sim_source_draws_a_frame_only_when_something_changed():
    s = SimSource(Desktop(100, front="teams"))
    w = s.window(None)
    assert w is not None and w.client_size == (1166, 1181) and s.window("other") is None
    f1, f2 = s.frame(w), s.frame(w)
    assert f1 is f2 and s.renders == 1
    s.type_text(w, "x")
    assert s.frame(w).frame_id == 2
    assert "session.taskbar.teams" in s.catalog().spots


# Through the MCP server, with the real OCR


async def session(front: str) -> tuple[SimSource, Any]:
    source = SimSource(Desktop(100, front=front))
    return source, Client(build_server(source, allow_input=True, catalog=source.catalog()))


@pytest.mark.windows
async def test_switch_by_taskbar_read_and_scroll_back_a_teams_thread():
    source, client = await session("terminal")
    async with client as c:
        a = Agent(c, source)
        assert (await a.status())["app"] == "terminal"
        await a.switch("teams")
        s = await a.status()
        assert (s["app"], s["view"], s["ready"]) == ("teams", "chat", True)
        msgs = source.desk.teams.chat.msgs
        thread = await a.call("jharokha_read_text", region="teams.thread")
        assert seen(thread, msgs[-1].lines[0]) and not seen(thread, msgs[-14].lines[0])
        r = await a.call("jharokha_scroll", ticks=6, x=800, y=600, region="teams.thread")
        assert seen(r, msgs[-14].lines[0])


@pytest.mark.windows
async def test_terminal_typed_command_and_its_output():
    source, client = await session("terminal")
    async with client as c:
        a = Agent(c, source)
        cmd = 'Get-Content notes.txt; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"\n'
        r = await a.call("jharokha_type", text=cmd, until="KX7R4C", show="full")
        assert seen(r, source.desk.shell.files["notes.txt"][0]) and seen(r, "KX7R4C:True:")
        r = await a.call("jharokha_run", command="Get-Date", clear=True, timeout_s=10)
        assert r.startswith("run_ms=") and seen(r, "October 23, 2026")
        assert source.desk.shell.log == [cmd.strip(), "Get-Date"]


@pytest.mark.windows
async def test_outlook_view_switch_settles_before_the_view_is_ready():
    source, client = await session("outlook")
    async with client as c:
        a = Agent(c, source)
        await a.call("jharokha_press", keys="ctrl+2", show="none")
        s = await a.status()
        # The screen held still between the ribbon and the grid: settled, not ready.
        assert (s["view"], s["ready"]) == ("calendar", False)
        await a.call("jharokha_wait_for_text", query="Today", timeout_s=5)
        s = await a.status()
        assert (s["view"], s["ready"]) == ("calendar", True)


# Incident replays. Up to 0.18 these asserted that the harm happened; since 0.19 each is
# refused, with nothing sent and nothing recorded by the simulation.


@pytest.mark.windows
async def test_incident_run_with_edge_in_front_is_refused_and_traced(tmp_path):
    source = SimSource(Desktop(100, front="browser"))
    trace = Trace(tmp_path / "trace.jsonl")
    server = build_server(source, allow_input=True, catalog=source.catalog(), trace=trace)
    async with Client(server) as c:
        a = Agent(c, source)
        r = await a.call("jharokha_run", command="Get-ChildItem", timeout_s=2)
        assert r.startswith("wrong_app: expected terminal in front, but the screen shows browser")
        assert "nothing was sent" in r
        # Told any app will do, Enter is still irreversible in a browser.
        r = await a.call("jharokha_run", command="Get-ChildItem", timeout_s=2, expect="any")
        assert r.startswith("irreversible: enter is irreversible in browser.page")
    b = source.desk.browser
    assert b.searches == [] and b.edit is None and source.desk.safety() == []
    recs = [json.loads(ln) for ln in (tmp_path / "trace.jsonl").read_text("utf-8").splitlines()]
    assert [(r["action"], r["front_app"], r["result"]) for r in recs] == [
        ("run", "browser.page", "wrong_app"),
        ("run", "browser.page", "irreversible"),
    ]
    assert recs[0]["text_len"] == len("Get-ChildItem") and "text" not in recs[0]


@pytest.mark.windows
async def test_incident_newline_typed_into_teams_stays_in_the_draft():
    source, client = await session("teams")
    teams = source.desk.teams
    async with client as c:
        a = Agent(c, source)
        await a.call("jharokha_type", text="draft line one\ndraft line two", show="none")
        assert teams.sent == [] and teams.chat.draft == ["draft line one", "draft line two"]
        r = await a.call("jharokha_press", keys="enter", show="none")
        assert r.startswith("irreversible: enter is irreversible in teams.chat") and not teams.sent
        args = {"keys": "Return", "expect": "teams.chat", "allow": "irreversible"}
        await a.call("jharokha_press", show="none", **args)
    assert teams.sent == [(teams.chat.name, "draft line one\ndraft line two")]
    assert [e.kind for e in source.desk.safety()] == ["sent"]


@pytest.mark.windows
async def test_a_second_agent_holding_the_session_is_refused(tmp_path):
    from jharokha.win.guard import Lease

    source = SimSource(Desktop(100, front="terminal"), lease_root=tmp_path)
    other = os.getppid()  # a live process that is not this one: the other agent
    Lease(HWND, root=tmp_path, pid=other).take()
    async with Client(build_server(source, allow_input=True, catalog=source.catalog())) as c:
        a = Agent(c, source)
        r = await a.call("jharokha_run", command="Get-Date", timeout_s=2)
        assert r.startswith(f"input_leased: process {other} is sending input to this window")
        assert (await a.status())["input"]["lease"]["pid"] == other
        Lease(HWND, root=tmp_path, pid=other).release()  # the other agent is done
        assert (await a.status())["input"]["lease"] is None
        r = await a.call("jharokha_run", command="Get-Date", clear=True, timeout_s=10)
        assert r.startswith("run_ms=")
    assert source.desk.shell.log == ["Get-Date"]


@pytest.mark.windows
async def test_someone_at_this_computer_stops_input_even_mid_text():
    idle: list[float | None] = [0.4]
    source = SimSource(Desktop(100, front="terminal"), user_idle=lambda: idle[0])
    source.covered = [{"kind": "taskbar", "rows": [1121, 1181]}]
    shell = source.desk.shell
    async with Client(build_server(source, allow_input=True, catalog=source.catalog())) as c:
        a = Agent(c, source)
        s = await a.status()
        assert s["input"]["user_idle_s"] == 0.4
        assert s["warnings"][0]["rows"] == [1121, 1181] and "taskbar" in s["warnings"][0]["detail"]
        r = await a.call("jharokha_type", text="Get-Date\n", show="none", expect="terminal")
        assert r.startswith("user_active: someone used this computer 0.4 s ago")
        assert shell.input == "" and shell.log == []
        # Idle when typing starts; the hooks see the user's key as the 9th character is due.
        source.user_idle = lambda: None
        source.user_keys_at = 8
        r = await a.call("jharokha_type", text="Get-ChildItem -Recurse\n", show="none")
        assert r.startswith(
            "user_active: someone used this computer while jharokha was typing: stopped "
            "after 8 of 23 characters, 'Get-Chil' being the last sent"
        )
        assert shell.input == "Get-Chil" and shell.log == []
        # A paste stops before its Enter.
        await a.call("jharokha_press", keys="ctrl+c", show="none")
        source.user_keys_at = 0
        r = await a.call("jharokha_type", text="Get-Date\n", paste=True, show="none")
        assert r.startswith("user_active: someone used this computer while jharokha was typing")
    assert shell.input == "Get-Date" and shell.log == []


@pytest.mark.windows
async def test_outlook_send_keys_are_held_back_until_allowed():
    source, client = await session("outlook")
    outlook = source.desk.outlook
    async with client as c:
        a = Agent(c, source)
        await a.call("jharokha_press", keys="ctrl+r", show="none", expect="outlook.mail")
        await a.call("jharokha_type", text="On it.\nWill reply today.", show="none")
        assert outlook.draft is not None and outlook.draft.body == ["On it.", "Will reply today."]
        for keys in ("ctrl+enter", "Alt+S"):
            r = await a.call("jharokha_press", keys=keys, show="none")
            assert r.startswith(f"irreversible: {keys.lower()} is irreversible in outlook.mail")
        assert outlook.sent == [] and source.desk.safety() == []
        await a.call("jharokha_press", keys="alt+s", allow="irreversible", show="none")
    assert outlook.sent == [(outlook.mails[0].sender, "On it.\nWill reply today.")]
    assert [e.kind for e in source.desk.safety()] == ["sent"]


@pytest.mark.windows
async def test_teams_ctrl_enter_sends_and_is_held_back():
    source, client = await session("teams")
    teams = source.desk.teams
    async with client as c:
        a = Agent(c, source)
        await a.call("jharokha_press", keys="ctrl+shift+x", show="none")  # formatting on
        await a.call("jharokha_type", text="one\ntwo", show="none")
        r = await a.call("jharokha_press", keys="ctrl+enter", show="none")
        assert r.startswith("irreversible: ctrl+enter is irreversible in teams.chat")
        assert teams.sent == [] and teams.chat.draft == ["one", "two"]
        await a.call("jharokha_press", keys="ctrl+enter", allow="irreversible", show="none")
    assert teams.sent == [(teams.chat.name, "one\ntwo")]
