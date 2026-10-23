import json
import re
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from jharokha.core import apps
from jharokha.core.layout import parse
from jharokha.core.screen import ScreenError
from jharokha.server import build_server

from .conftest import FakeSource


async def call(source: FakeSource, name: str, **args: Any):
    async with Client(build_server(source)) as c:
        return await c.call_tool(name, args)


def text(r: Any, i: int = 0) -> str:
    assert isinstance(r.content[i], TextContent)
    return r.content[i].text


async def test_lists_read_only_tools(source):
    async with Client(build_server(source)) as c:
        tools = (await c.list_tools()).tools
    assert sorted(t.name for t in tools) == [
        "jharokha_apps",
        "jharokha_changes",
        "jharokha_do",  # its plan only, dry_run=true
        "jharokha_find_text",
        "jharokha_layout",
        "jharokha_read_text",
        "jharokha_screenshot",
        "jharokha_status",
        "jharokha_wait_for_text",
        "jharokha_watch",
    ]
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert all("untrusted" in (t.description or "") for t in tools)


async def test_status(source):
    r = await call(source, "jharokha_status")
    s = json.loads(text(r))
    assert s["found"] and s["state"] == "normal" and s["client_size"] == [640, 400]
    # No built-in app has its cues on this screen.
    assert (s["app"], s["view"]) == ("generic", "screen") and s["confidence"] > 0.5
    assert s["runner_up"]["score"] < 0.5
    source.minimized = True
    s = json.loads(text(await call(source, "jharokha_status")))
    assert s["found"] and s["state"] == "minimized"
    source.present = False
    assert json.loads(text(await call(source, "jharokha_status")))["state"] == "window_not_found"


async def test_read_text_concise_detailed_and_region(source):
    r = await call(source, "jharokha_read_text")
    assert not r.is_error
    body = text(r)
    assert body.startswith("frame_id=1 ")
    assert "L2 [10,70] Order AB12 shipped\nL3 [300,10] second column" in body
    r = await call(source, "jharokha_read_text", response_format="detailed", limit=1)
    assert '"text":"12s","box":[143,10,21,12],"lookalike":true' in text(r)
    assert "offset=1" in text(r)
    r = await call(source, "jharokha_read_text", region=[0, 60, 640, 40])
    assert "Order AB12" in text(r) and "Build" not in text(r)


async def test_errors_carry_code_and_next_step(source):
    r = await call(source, "jharokha_read_text", region=[0, 0, 9999, 10])
    assert r.is_error and text(r).startswith("region_out_of_bounds:")
    source.present = False
    r = await call(source, "jharokha_read_text")
    assert r.is_error and "window_not_found" in text(r) and "window_title" in text(r)
    source.present, source.minimized = True, True
    r = await call(source, "jharokha_read_text")
    assert r.is_error and text(r).startswith("minimized:")
    source.minimized = False
    source.items = [("This PC is locked", 10, 10)]
    r = await call(source, "jharokha_read_text")
    assert r.is_error and text(r).startswith("locked:")


async def test_find_text(source):
    r = await call(source, "jharokha_find_text", query="file not found")
    m = json.loads(text(r))["matches"]
    assert len(m) == 1 and m[0]["ref"] == "L1" and m[0]["text"] == "file not found"
    assert m[0]["center"][1] == 46


async def test_screenshot_region_and_scale(source):
    r = await call(source, "jharokha_screenshot", region=[0, 0, 320, 200], max_long_edge=160)
    meta = json.loads(text(r))
    assert meta["region"] == [0, 0, 320, 200] and meta["scale"] == 0.5
    assert r.content[1].type == "image" and r.content[1].mime_type == "image/png"


async def test_wait_for_text_and_timeout(source):
    r = await call(source, "jharokha_wait_for_text", query="succeeded", timeout_s=1)
    assert json.loads(text(r))["found"] is True
    r = await call(source, "jharokha_wait_for_text", query="shipped|failed", regex=True)
    assert json.loads(text(r))["match"]["text"] == "shipped"
    r = await call(source, "jharokha_wait_for_text", query="succeeded", gone=True, timeout_s=0.6)
    assert r.is_error and text(r).startswith("timeout:") and "Build succeeded" in text(r)


async def test_scroll_is_opt_in_and_returns_the_new_screen(source):
    r = await call(source, "jharokha_scroll", ticks=-1)
    assert r.is_error and "Unknown tool" in text(r)
    async with Client(build_server(source, allow_input=True)) as c:
        tools = (await c.list_tools()).tools
        scroll = next(t for t in tools if t.name == "jharokha_scroll")
        assert scroll.annotations and scroll.annotations.read_only_hint is False
        r = await c.call_tool("jharokha_scroll", {"ticks": -1, "show": "full"})
        assert "L0 [10,-10] Build succeeded" in text(r)
        r = await c.call_tool("jharokha_scroll", {"ticks": 1})
        assert "(no text changed)" in text(r)  # lines moved, none appeared
        r = await c.call_tool("jharokha_scroll", {"ticks": 1, "x": 9999, "y": 0})
        assert r.is_error and text(r).startswith("region_out_of_bounds")


async def test_input_returns_what_changed_after_the_screen_settles(source):
    async with Client(build_server(source, allow_input=True)) as c:
        r = await c.call_tool("jharokha_type", {"text": "hello"})
        assert "settled_ms=" in text(r) and "changed:" in text(r)
        assert "+ L3 [10,230] typed hello" in text(r)
        assert "Build succeeded" not in text(r)
        r = await c.call_tool("jharokha_press", {"keys": "enter", "show": "none"})
        assert text(r).startswith("settled_ms=") and "screen_text" not in text(r)
        r = await c.call_tool("jharokha_click", {"x": 5, "y": 5, "region": [0, 190, 640, 30]})
        assert "+ L0 [10,200] clicked left at 5,5" in text(r)


async def test_until_waits_for_text_after_input(source):
    async with Client(build_server(source, allow_input=True)) as c:
        r = await c.call_tool("jharokha_type", {"text": "dir", "until": "typed dir"})
        assert "until_ms=" in text(r)
        r = await c.call_tool(
            "jharokha_type", {"text": "x", "until": r"typed \w$", "until_regex": True}
        )
        assert "until_ms=" in text(r)
        r = await c.call_tool("jharokha_type", {"text": "long script", "paste": True})
        assert "pasted long script" in text(r)
        r = await c.call_tool(
            "jharokha_press", {"keys": "enter", "until": "never shown", "timeout_s": 0.5}
        )
        assert r.is_error and text(r).startswith("timeout: the input was sent, but")


async def test_click_type_press_are_opt_in_and_return_the_new_screen(source):
    for name in ("jharokha_click", "jharokha_type", "jharokha_press"):
        assert (await call(source, name, x=1, y=1, text="a", keys="a")).is_error
    async with Client(build_server(source, allow_input=True)) as c:
        r = await c.call_tool(
            "jharokha_click", {"x": 20, "y": 30, "button": "right", "double": True}
        )
        assert "clicked right x2 at 20,30" in text(r)
        r = await c.call_tool("jharokha_click", {"x": 20, "y": 9999})
        assert r.is_error and text(r).startswith("region_out_of_bounds")
        r = await c.call_tool("jharokha_type", {"text": "hello"})
        assert "typed hello" in text(r)
        r = await c.call_tool("jharokha_press", {"keys": "ctrl+c"})
        assert "pressed ctrl+c" in text(r)
        r = await c.call_tool("jharokha_type", {"text": "dir", "focus_at": [40, 50]})
        assert "typed dir after a click at 40,50" in text(r)
        r = await c.call_tool("jharokha_type", {"text": "dir", "focus_at": [40, 9999]})
        assert r.is_error and text(r).startswith("region_out_of_bounds")


class Shell(FakeSource):
    """A PowerShell that answers `Get-Date` after two polls and `cls` by clearing."""

    def type_text(self, w, text, focus_at=None, paste=False):  # noqa: ANN001, ANN201
        cmd = text.rstrip("\n")
        y = self.items[-1][2]
        self.items[-1] = (f"PS C:\\a> {cmd}", 10, y)
        self.pending = [("Friday 23 October", 10, y + 20), ("PS C:\\a>", 10, y + 40)]

    def press(self, w, combo, focus_at=None):  # noqa: ANN001, ANN201
        self.items = [("PS C:\\a>", 10, 10)]

    def frame(self, w):  # noqa: ANN001, ANN201
        if getattr(self, "pending", None):
            self.items.append(self.pending.pop(0))
        return super().frame(w)


async def test_run_returns_only_the_commands_output():
    shell = Shell([("PS C:\\a> dir", 10, 10), ("old.txt", 10, 30), ("PS C:\\a>", 10, 50)])
    async with Client(build_server(shell, allow_input=True)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["jharokha_run"].annotations.destructive_hint is not False  # type: ignore[union-attr]
        # A bare prompt on white is no terminal the definitions know: refused by default.
        r = await c.call_tool("jharokha_run", {"command": "Get-Date"})
        assert r.is_error and text(r).startswith("wrong_app: expected terminal in front")
        assert "nothing was sent" in text(r) and shell.items[-1][0] == "PS C:\\a>"
        # A shell that prints no end marker is waited on by its prompt.
        args = {"command": "Get-Date", "expect": "any", "shell": "other"}
        r = text(await c.call_tool("jharokha_run", args))
        assert r.startswith("run_ms=") and " lines=1" in r
        assert "Friday 23 October" in r and "old.txt" not in r and "PS C:" not in r
        args = {"command": "Get-Date", "clear": True, "expect": "any", "shell": "other"}
        r = text(await c.call_tool("jharokha_run", args))
        assert "lines=1" in r and "Friday" in r
        args = {"command": "x", "prompt": "(", "timeout_s": 1, "expect": "any"}
        r = await c.call_tool("jharokha_run", args)
        assert r.is_error and text(r).startswith("bad_prompt")


class Marked(FakeSource):
    """A PowerShell that answers a marked command with its output and the end marker."""

    def type_text(self, w, text, focus_at=None, paste=False):  # noqa: ANN001, ANN201
        self.items = [(f"PS C:\\a> {text}", 10, 10)]
        if text.startswith("cmd /c exit 2"):
            out, status = [], "False:2"
        else:
            out, status = [("Friday 23 October", 10, 30)], "True:"
        a, b = re.search(r'"(\w\w)"\+"(\w{4}):', text).groups()  # type: ignore[union-attr]
        self.pending = [*out, (f"{a}{b}:{status}", 10, 50), ("PS C:\\a>", 10, 70)]

    def press(self, w, combo, focus_at=None):  # noqa: ANN001, ANN201
        if combo == "ctrl+l":
            self.items = [("PS C:\\a>", 10, 10)]

    def frame(self, w):  # noqa: ANN001, ANN201
        if getattr(self, "pending", None):
            self.items = self.items + self.pending
            self.pending = []
        return super().frame(w)


async def test_run_reads_its_end_marker_and_exit_code():
    shell = Marked([("PS C:\\a> dir", 10, 10), ("old.txt", 10, 30), ("PS C:\\a>", 10, 50)])
    async with Client(build_server(shell, allow_input=True)) as c:
        args = {"command": "Get-Date", "expect": "any"}
        r = text(await c.call_tool("jharokha_run", args))
        head = r.split("\n")[0]
        assert head.startswith("run_ms=") and " exit_code= ok=true lines=1" in head
        assert "Friday 23 October" in r and "old.txt" not in r
        args = {"command": "cmd /c exit 2", "expect": "any", "clear": True}
        r = text(await c.call_tool("jharokha_run", args))
        assert " exit_code=2 ok=false lines=0" in r.split("\n")[0]


async def test_expect_refuses_input_unless_that_app_is_in_front(source):
    async with Client(build_server(source, allow_input=True)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        for name in ("jharokha_scroll", "jharokha_click", "jharokha_type", "jharokha_press"):
            assert "expect" in tools[name].input_schema["properties"]
            assert "wrong_app" in (tools[name].description or "")
        assert "allow='irreversible'" in (tools["jharokha_type"].description or "")
        for name, args in (
            ("jharokha_click", {"x": 20, "y": 30}),
            ("jharokha_scroll", {"ticks": -1}),
            ("jharokha_type", {"text": "hello"}),
            ("jharokha_press", {"keys": "enter"}),
        ):
            r = await c.call_tool(name, {**args, "expect": "teams"})
            assert r.is_error and text(r).startswith("wrong_app: expected teams in front")
        assert not any(t.startswith(("clicked", "typed", "pressed")) for t, _, _ in source.items)
        assert source.items[0][1:] == (10, 10)  # not scrolled either
        r = await c.call_tool("jharokha_type", {"text": "x", "expect": "slack"})
        assert r.is_error and text(r).startswith("bad_argument: expect: no app or view 'slack'")
        assert "teams.chat" in text(r)
        r = await c.call_tool("jharokha_type", {"text": "hello", "expect": "generic.screen"})
        assert "typed hello" in text(r)


POLICY_APP = """format = 1
[app.chat]
[[app.chat.cue]]
text = "Build succeeded"
weight = 2
[app.chat.policy]
newline = "shift+enter"
irreversible_keys = ["enter"]
[app.chat.view.main]
"""
BUSY = "someone used this computer 0.1 s ago; nothing was sent."


class Busy(FakeSource):
    """Refuses input as the user's activity would, from the nth call on."""

    calls = 0
    busy_from = 99

    def press(self, w, combo, focus_at=None):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls >= self.busy_from:
            raise ScreenError("user_active", BUSY)
        super().press(w, combo, focus_at)

    def type_text(self, w, text, focus_at=None, paste=False):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls >= self.busy_from:
            raise ScreenError("user_active", BUSY)
        super().type_text(w, text, focus_at, paste)


async def test_the_front_apps_policy_shapes_and_holds_back_keys():
    source = Busy([("Build succeeded", 10, 10)])
    catalog = apps.catalog(apps.parse(POLICY_APP))
    refused = "irreversible: enter is irreversible in chat.main"
    async with Client(build_server(source, allow_input=True, catalog=catalog)) as c:
        r = await c.call_tool("jharokha_type", {"text": "one\ntwo", "show": "none"})
        assert not r.is_error
        sent = [t for t, _, _ in source.items[1:]]
        assert sent == ["typed one", "pressed shift+enter", "typed two"]
        r = await c.call_tool("jharokha_press", {"keys": "Return"})
        assert r.is_error and text(r).startswith(refused)
        # A newline is never Enter here, so typed or pasted text cannot send.
        r = await c.call_tool("jharokha_type", {"text": "x\n", "paste": True, "show": "none"})
        assert not r.is_error and source.items[-1][0] == "pressed shift+enter"
        r = await c.call_tool("jharokha_press", {"keys": "enter", "allow": "irreversible"})
        assert not r.is_error and "pressed enter" in text(r)
        # Refused part-way: the error says what went before.
        source.calls, source.busy_from = 0, 2
        r = await c.call_tool("jharokha_type", {"text": "three\nfour", "show": "none"})
        assert r.is_error and text(r).startswith("user_active: the first 1 of 3 parts")


async def test_input_is_traced_when_asked(source, tmp_path):
    from jharokha.core.trace import Trace

    path = tmp_path / "trace.jsonl"
    server = build_server(source, allow_input=True, trace=Trace(path))
    async with Client(server) as c:
        await c.call_tool("jharokha_type", {"text": "secret", "show": "none"})
        await c.call_tool("jharokha_press", {"keys": "enter", "expect": "terminal"})
        await c.call_tool("jharokha_click", {"x": 5, "y": 6, "show": "none"})
    recs = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [(r["action"], r["result"]) for r in recs] == [
        ("type", "sent"),
        ("press", "wrong_app"),
        ("click", "sent"),
    ]
    assert recs[0]["front_app"] == "generic.screen" and recs[0]["text_len"] == 6
    assert "secret" not in path.read_text("utf-8")
    assert recs[2]["point"] == [5, 6] and recs[2]["window"] == source.title


async def test_status_warns_what_covers_the_window_and_reports_input(source):
    source.warnings = [{"kind": "taskbar", "rows": [360, 400], "detail": "covered"}]
    s = json.loads(text(await call(source, "jharokha_status")))
    assert s["warnings"][0]["rows"] == [360, 400] and "input" not in s
    async with Client(build_server(source, allow_input=True)) as c:
        s = json.loads(text(await c.call_tool("jharokha_status", {})))
    assert s["input"] == {"lease": None, "user_idle_s": 42.0}


async def test_version_of_the_running_code_in_status_and_instructions(source):
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    want = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    server = build_server(source)
    assert (server.instructions or "").startswith(f"jharokha {want}. ")
    async with Client(server) as c:
        assert json.loads(text(await c.call_tool("jharokha_status", {})))["version"] == want
    source.present = False
    async with Client(build_server(source)) as c:
        assert json.loads(text(await c.call_tool("jharokha_status", {})))["version"] == want


async def test_unchanged_screen_is_not_ocrd_twice(source):
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read_text", {})
        await c.call_tool("jharokha_find_text", {"query": "x"})
    assert source.ocr_calls == 1


@pytest.mark.windows
def test_real_ocr_reads_fixture_screen():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows OCR")
    from jharokha.win.ocr import Ocr

    from .conftest import render

    items = [("Build succeeded in 12s", 20, 20), ("Error: file not found", 20, 60)]
    lines = Ocr().recognize(render(items), upscale=2.0)
    got = " ".join(ln.text for ln in lines).lower()
    assert "build succeeded" in got and "file not found" in got
    assert abs(lines[0].box[1] - 20) < 8


async def test_verify_shows_and_searches_the_second_reading(source):
    source.items.append(("Ticket INC0012345 resolved", 10, 100))
    source.misreads = {"Ticket INC0012345 resolved": "Ticket INCOOI 2345 resolved"}
    r = text(await call(source, "jharokha_read_text", verify="flagged"))
    assert r.split("\n", 1)[0].endswith("verified=3 differ=1")  # lines with digits
    assert "INCOOI 2345 resolved | verify: Ticket INC0012345 resolved" in r
    assert "| verify" not in text(await call(source, "jharokha_read_text"))
    r = await call(source, "jharokha_find_text", query=r"INC\d{7}", regex=True)
    assert json.loads(text(r))["matches"] == []
    r = await call(source, "jharokha_find_text", query=r"INC\d{7}", regex=True, verify="all")
    assert json.loads(text(r))["matches"][0]["text"] == "Ticket INC0012345 resolved"
    r = text(await call(source, "jharokha_read_text", verify="all", response_format="detailed"))
    assert '"verify":"second column","agree":true' in r and "differ=1" in r
    assert '"verify":"Ticket INC0012345 resolved","agree":false' in r


@pytest.mark.windows
def test_real_recognizer_reads_a_line_box_and_a_row_strip():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows")
    from jharokha.win.recognize import Recognizer

    from .conftest import lines_for, render

    items = [("Order AB12 shipped", 20, 20), ("PS C:\\Users\\X> dir", 20, 40)]
    frame, rec = render(items), Recognizer()
    assert rec.read(frame, [lines_for(items)[0].box]) == ["Order AB12 shipped"]
    # Without a line box to go by, the model drops spaces between monospaced glyphs.
    strip = rec.read(frame, [(0, 34, 640, 20)], margin=0)
    assert [s.replace(" ", "") for s in strip] == ["PSC:\\Users\\X>dir"]


async def test_status_names_the_browser_and_reports_its_url_as_untrusted(source):
    source.items = [("https://queue.example.net/requests", 80, 20), ("Open requests", 20, 120)]
    s = json.loads(text(await call(source, "jharokha_status")))
    assert (s["app"], s["view"]) == ("browser", "page")
    assert s["facts"].startswith('<screen_text source="ocr" untrusted="true">')
    assert "url=https://queue.example.net/requests" in s["facts"]
    r = await call(source, "jharokha_read_text", region="teams.thread")
    assert r.is_error and text(r).startswith("layout_mismatch:") and "browser page" in text(r)
    r = text(await call(source, "jharokha_layout"))
    assert "regions of browser page:" in r and "browser.page [" in r


CHAT_APP = """format = 1
[app.chat]
[[app.chat.cue]]
text = "Chat log"
weight = 2
[app.chat.view.main.region.thread]
reader = "list"
kind = "messages"
box = [0, 0.1, 1, 0.9]
[app.chat.view.main.region.top]
box = [0, 0, 1, 0.1]
"""


async def test_list_regions_read_as_items_and_since(source):
    source.items = [
        ("Chat log", 10, 10),
        ("Ana Alder 16/10 11:20", 10, 60),
        ("Is the build green?", 20, 76),
        ("Ben Brook 16/10 11:45", 10, 110),
        ("Fixed it", 20, 126),
    ]
    catalog = apps.catalog(apps.parse(CHAT_APP))
    async with Client(build_server(source, catalog=catalog)) as c:

        async def read(**args: Any) -> str:
            return text(await c.call_tool("jharokha_read_text", args))

        r = await read(region="chat.thread")
        head, body = r.split("\n", 1)
        assert "reader=list:messages items=2" in body.split("\n")[0]
        assert '"sender":"Ana Alder","time":"16/10 11:20","text":"Is the build green?"' in r
        assert body.split("\n")[1].startswith('<screen_text source="ocr" untrusted="true">')
        r = await read(region="chat.thread", since="16/10 11:30")
        assert "items=1" in r and "Ana Alder" not in r and "Fixed it" in r
        r = await read(region="chat.thread", format="lines")
        assert "L0 [10,60] Ana Alder 16/10 11:20" in r
        r = await read(region="chat.top", format="items")
        assert "reader=text items=1" in r and '"text":"Chat log"' in r
        r = await read(format="items")
        assert "regions=2" in r and '"region":"chat.thread","reader":"list:messages"' in r
        bad = await c.call_tool("jharokha_read_text", {"region": "chat.top", "since": "Mon"})
        assert bad.is_error and text(bad).startswith("bad_argument:")
        bad = await c.call_tool(
            "jharokha_read_text", {"region": "chat.thread", "since": "whenever"}
        )
        assert bad.is_error and "cannot read 'whenever'" in text(bad)
        layout = text(await c.call_tool("jharokha_layout", {}))
        assert "chat.thread [0,40,640,360] box, reader list:messages" in layout


def old_layouts(toml: str) -> apps.Catalog:
    """A catalog with an old layouts file read into it, as the server loads one."""
    return apps.load(None, parse(toml))


async def test_layout_panels_and_named_regions(source):
    catalog = old_layouts(
        '[app]\nanchor = "Build"\nright = [0.4, 0, 0.6, 1]\n'
        '[other]\nanchor = "Zzz"\nall = [0, 0, 1, 1]'
    )
    async with Client(build_server(source, catalog=catalog)) as c:
        r = text(await c.call_tool("jharokha_layout", {}))
        assert " app=app view=main confidence=1.0 runner_up=" in r.split("\n")[0]
        assert "P0 [0,0,232,400] 3 lines: Build succeeded in 12s" in r
        assert "P1 [232,0,408,400] 1 lines: second column" in r
        assert "regions of app main:\n  app.right [256,0,384,400] calibrated, reader text" in r
        assert "other.main" in r.split("other apps and views: ")[1]
        r = text(await c.call_tool("jharokha_read_text", {"region": "P1"}))
        assert "second column" in r and "Build" not in r
        r = text(
            await c.call_tool("jharokha_find_text", {"query": "column", "region": "app.right"})
        )
        assert json.loads(r)["matches"][0]["text"] == "column"
        r = await c.call_tool("jharokha_read_text", {"region": "other.all"})
        assert r.is_error and text(r).startswith("layout_mismatch:")
        r = await c.call_tool("jharokha_read_text", {"region": "nope.x"})
        assert r.is_error and "app.right" in text(r) and text(r).startswith("region_unknown:")
        r = await c.call_tool("jharokha_read_text", {"region": "P9"})
        assert r.is_error and "2 panels" in text(r)


async def test_grid_region_reads_as_terminal_rows(source):
    source.items = [("PS C:\\repo> dir", 10, 10), ("a.txt 0.46 kB", 10, 30)]
    source.misreads = {"a.txt 0.46 kB": "a.txt e.46 kB"}
    catalog = old_layouts('[term]\nanchor = "PS"\ngrid = "body"\nbody = [0, 0, 1, 1]\n')
    async with Client(build_server(source, catalog=catalog)) as c:
        r = text(await c.call_tool("jharokha_read_text", {}))
        assert "L1 [10,30] a.txt 0.46 kB" in r
        r = text(await c.call_tool("jharokha_read_text", {"region": [0, 25, 640, 30]}))
        assert "L0 [10,30] a.txt 0.46 kB" in r


async def test_app_on_screen_and_its_focus_region_after_input(source):
    catalog = old_layouts(
        "[taskbar]\nbar = [0, 0.9, 1, 0.1]\n"
        '[app]\nanchor = "Build"\nfocus = "lower"\nlower = [0, 0.6, 1, 0.4]\n'
    )
    async with Client(build_server(source, allow_input=True, catalog=catalog)) as c:
        status = json.loads(text(await c.call_tool("jharokha_status", {})))
        assert (status["app"], status["view"], status["confidence"]) == ("app", "main", 1.0)
        assert " app=app view=main confidence=1.0\n" in text(
            await c.call_tool("jharokha_read_text", {})
        )
        r = text(await c.call_tool("jharokha_read_text", {"region": "taskbar.bar"}))
        assert "(no text)" in r
        # The fake types at y 230, above the focus (y 240 on), and presses at y 260, in it.
        r = text(await c.call_tool("jharokha_type", {"text": "hi"}))
        assert "focus=app.lower (1 lines changed elsewhere" in r and "(no text changed)" in r
        r = text(await c.call_tool("jharokha_press", {"keys": "enter"}))
        assert "elsewhere" not in r and "+ L0 [10,260] pressed enter" in r
        assert "Build succeeded" not in r
    with pytest.raises(ValueError, match="grid must name one of its regions"):
        parse('[app]\nanchor = "x"\ngrid = "nope"\nall = [0, 0, 1, 1]')
    with pytest.raises(ValueError, match="focus must name one of its regions"):
        parse('[app]\nanchor = "x"\nfocus = "nope"\nall = [0, 0, 1, 1]')
