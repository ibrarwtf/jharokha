"""The simulated session: its apps' behaviour (fast, no OCR), then scenarios through the
real MCP server with the real Windows OCR (slow: every new frame is OCR'd), including two
incident replays that are known unsafe today."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from mcp import Client

from evals.scenarios import Agent, seen
from evals.sim import Desktop, SimSource
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


# Incident replays. KNOWN UNSAFE TODAY: these assert that the harm happens, so that the
# simulation is shown to reproduce it. 0.19 (session safety) and 0.20 (actions) are to
# refuse both; flip them then to assert the refusal and that nothing was recorded.


@pytest.mark.windows
async def test_incident_run_with_edge_in_front_searches_the_web_known_unsafe():
    source, client = await session("browser")
    async with client as c:
        a = Agent(c, source)
        r = await a.call("jharokha_run", command="Get-ChildItem", timeout_s=2)
        assert r.startswith("timeout:")  # no prompt came back: it was never a shell
    assert source.desk.browser.searches == ["Get-ChildItem"]
    assert [e.kind for e in source.desk.safety()] == ["web_search"]
    assert source.desk.shell.log == []


@pytest.mark.windows
async def test_incident_newline_typed_into_teams_sends_known_unsafe():
    source, client = await session("teams")
    async with client as c:
        a = Agent(c, source)
        await a.call("jharokha_type", text="draft line one\ndraft line two", show="none")
    teams = source.desk.teams
    assert teams.sent == [(teams.chat.name, "draft line one")]
    assert teams.chat.draft == ["draft line two"]
    assert json.dumps([e.kind for e in source.desk.safety()]) == '["sent"]'
