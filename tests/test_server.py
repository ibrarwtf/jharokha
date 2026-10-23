import json
import re
import sys
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent
from PIL import ImageDraw

from jharokha.core import apps
from jharokha.core.layout import parse
from jharokha.core.models import Frame
from jharokha.core.screen import ScreenError
from jharokha.server import build_server

from .conftest import FakeSource, render

READ_ONLY_TOOLS = [
    "jharokha_apps",
    "jharokha_do",  # its plan only, dry_run=true
    "jharokha_find",
    "jharokha_learn",  # writes apps.toml only when asked to save
    "jharokha_read",
    "jharokha_screenshot",
    "jharokha_status",
    "jharokha_wait",
]


async def call(source: FakeSource, name: str, **args: Any):
    async with Client(build_server(source)) as c:
        return await c.call_tool(name, args)


def text(r: Any, i: int = 0) -> str:
    assert isinstance(r.content[i], TextContent)
    return r.content[i].text


async def test_a_small_fixed_set_of_tools(source):
    async with Client(build_server(source)) as c:
        tools = (await c.list_tools()).tools
    assert sorted(t.name for t in tools) == READ_ONLY_TOOLS
    assert all(
        t.annotations and t.annotations.read_only_hint for t in tools if t.name != "jharokha_learn"
    )
    assert all("untrusted" in (t.description or "") for t in tools)
    async with Client(build_server(source, allow_input=True)) as c:
        tools = (await c.list_tools()).tools
    assert sorted(t.name for t in tools) == sorted([*READ_ONLY_TOOLS, "jharokha_act"])
    # New apps and actions are data: the descriptions stay short whatever is loaded.
    assert sum(len(t.description or "") for t in tools) < 12000


async def test_status(source):
    r = await call(source, "jharokha_status")
    s = json.loads(text(r))
    assert s["found"] and s["state"] == "normal" and s["client_size"] == [640, 400]
    assert s["input_allowed"] is False and s["warnings"] == []
    # No built-in app has its cues on this screen.
    assert (s["app"], s["view"]) == ("generic", "screen") and s["confidence"] > 0.5
    assert s["runner_up"]["score"] < 0.5
    source.minimized = True
    s = json.loads(text(await call(source, "jharokha_status")))
    assert s["found"] and s["state"] == "minimized"
    source.present = False
    assert json.loads(text(await call(source, "jharokha_status")))["state"] == "window_not_found"


async def test_read_screen_concise_detailed_and_region(source):
    r = await call(source, "jharokha_read")
    assert not r.is_error
    body = text(r)
    assert body.startswith("frame_id=1 ") and " region=screen\n" in body
    assert "L2 [10,70] Order AB12 shipped\nL3 [300,10] second column" in body
    r = await call(source, "jharokha_read", format="detailed", limit=1)
    assert '"text":"12s","box":[143,10,21,12],"lookalike":true' in text(r)
    assert "offset=1" in text(r)
    r = await call(source, "jharokha_read", region=[0, 60, 640, 40])
    assert "Order AB12" in text(r) and "Build" not in text(r)


async def test_errors_carry_code_and_next_step(source):
    r = await call(source, "jharokha_read", region=[0, 0, 9999, 10])
    assert r.is_error and text(r).startswith("region_out_of_bounds:")
    source.present = False
    r = await call(source, "jharokha_read")
    assert r.is_error and "window_not_found" in text(r) and "window_title" in text(r)
    source.present, source.minimized = True, True
    r = await call(source, "jharokha_read")
    assert r.is_error and text(r).startswith("minimized:")
    source.minimized = False
    source.items = [("This PC is locked", 10, 10)]
    r = await call(source, "jharokha_read")
    assert r.is_error and text(r).startswith("locked:")
    s = json.loads(text(await call(source, "jharokha_status")))
    assert s["state"] == "locked" and s["warnings"][0]["kind"] == "locked"


async def test_find(source):
    r = await call(source, "jharokha_find", query="file not found")
    m = json.loads(text(r))["matches"]
    assert len(m) == 1 and m[0]["ref"] == "L1" and m[0]["text"] == "file not found"
    assert m[0]["center"][1] == 46
    r = await call(source, "jharokha_find")
    assert r.is_error and text(r).startswith("bad_argument:")


async def test_find_by_locator_names_a_target_by_where_it_is(source):
    r = json.loads(text(await call(source, "jharokha_find", locator={"right_of": "Order"})))
    assert r["matches"][0]["text"] == "AB12" and r["matches"][0]["how"] == "right of"
    r = json.loads(text(await call(source, "jharokha_find", locator={"below": "Build"})))
    assert r["matches"][0]["text"].startswith("Error")
    r = json.loads(text(await call(source, "jharokha_find", locator={"right_of": "Nowhere"})))
    assert r["matches"] == [] and "not on screen" in r["miss"]
    r = await call(source, "jharokha_find", locator={"spot": "x", "text": "y"})
    assert r.is_error and "spot stands alone" in text(r)


class Icons(FakeSource):
    """A screen with two square buttons beside the text, as a compose bar has."""

    def frame(self, w):  # noqa: ANN001, ANN201
        f = super().frame(w)
        img = f.image().copy()
        d = ImageDraw.Draw(img)
        for x in (500, 560):
            d.rectangle((x, 8, x + 16, 24), fill=(40, 90, 200))
        return Frame(f.width, f.height, img.tobytes("raw", "BGRA"), f.frame_id, f.ts)


async def test_icons_are_targets_without_text():
    source = Icons([("second column", 300, 10), ("Order AB12 shipped", 10, 70)])
    r = json.loads(text(await call(source, "jharokha_find", locator={"icon": True}, all=True)))
    boxes = [m["box"] for m in r["matches"]]
    assert [b[0] for b in boxes] == [500, 560] and all(m["how"] == "icon" for m in r["matches"])
    loc = {"icon": True, "right_of": "second column"}
    r = json.loads(text(await call(source, "jharokha_find", locator=loc)))
    assert r["matches"][0]["box"][0] == 500 and r["matches"][0]["how"].startswith("icon right of")
    loc = {"icon": True, "nth": 2}
    r = json.loads(text(await call(source, "jharokha_find", locator=loc)))
    assert r["matches"][0]["box"][0] == 560
    async with Client(build_server(source, allow_input=True)) as c:
        r = text(await c.call_tool("jharokha_act", {"op": "click", "target": {"icon": True}}))
        assert " op=click at=icon [508,16] " in r and "clicked left at 508,16" in r
    assert source.items[-1][0] == "clicked left at 508,16"


async def test_screenshot_region_and_scale(source):
    r = await call(source, "jharokha_screenshot", region=[0, 0, 320, 200], max_long_edge=160)
    meta = json.loads(text(r))
    assert meta["region"] == [0, 0, 320, 200] and meta["scale"] == 0.5
    assert r.content[1].type == "image" and r.content[1].mime_type == "image/png"


async def test_wait_for_text_regex_gone_and_settled(source):
    r = await call(source, "jharokha_wait", text="succeeded", timeout_s=1)
    assert json.loads(text(r))["found"] is True
    r = await call(source, "jharokha_wait", regex="shipped|failed")
    assert json.loads(text(r))["match"]["text"] == "shipped"
    r = await call(source, "jharokha_wait", gone="succeeded", timeout_s=0.6)
    assert r.is_error and text(r).startswith("timeout:") and "Build succeeded" in text(r)
    assert "settled_ms" in json.loads(text(await call(source, "jharokha_wait")))
    r = await call(source, "jharokha_wait", text="a", regex="b")
    assert r.is_error and text(r).startswith("bad_argument:")


async def test_since_last_gives_only_what_changed(source):
    async with Client(build_server(source)) as c:

        async def read(**args: Any) -> str:
            return text(await c.call_tool("jharokha_read", args))

        first = await read(since="last")
        assert "(first read: all of it)" in first and "Build succeeded" in first
        source.items.append(("New toast", 10, 120))
        source.items = [i for i in source.items if i[0] != "Error: file not found"]
        r = await read(since="last")
        assert "+ L" in r and "] New toast" in r and "- [10,40] Error: file not found" in r
        assert "Build succeeded" not in r
        assert "(no text changed)" in await read(since="last")


async def test_act_is_opt_in_one_tool_for_every_input(source):
    r = await call(source, "jharokha_act", op="scroll")
    assert r.is_error and "Unknown tool" in text(r)
    async with Client(build_server(source, allow_input=True)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["jharokha_act"].annotations.read_only_hint is False  # type: ignore[union-attr]
        r = await c.call_tool("jharokha_act", {"op": "scroll", "ticks": -1, "show": "full"})
        assert "L0 [10,-10] Build succeeded" in text(r)
        r = await c.call_tool("jharokha_act", {"op": "scroll", "ticks": 1})
        assert "(no text changed)" in text(r)  # lines moved, none appeared
        r = await c.call_tool("jharokha_act", {"op": "scroll", "target": [9999, 0]})
        assert r.is_error and text(r).startswith("region_out_of_bounds")
        r = await c.call_tool("jharokha_act", {"op": "click"})
        assert r.is_error and text(r).startswith("bad_argument: click needs a target")


async def test_input_returns_what_changed_after_the_screen_settles(source):
    async with Client(build_server(source, allow_input=True)) as c:
        r = await c.call_tool("jharokha_act", {"op": "type", "text": "hello"})
        assert "settled_ms=" in text(r) and "changed:" in text(r)
        assert "+ L3 [10,230] typed hello" in text(r)
        assert "Build succeeded" not in text(r)
        r = await c.call_tool("jharokha_act", {"op": "press", "keys": "enter", "show": "none"})
        assert text(r).startswith("op=press settled_ms=") and "screen_text" not in text(r)
        r = await c.call_tool("jharokha_act", {"op": "click", "target": [5, 5]})
        assert "[10,200] clicked left at 5,5" in text(r)


async def test_until_waits_for_a_condition_after_input(source):
    async with Client(build_server(source, allow_input=True)) as c:
        r = await c.call_tool("jharokha_act", {"op": "type", "text": "dir", "until": "typed dir"})
        assert "until_ms=" in text(r)
        until = {"regex": r"typed \w$"}
        r = await c.call_tool("jharokha_act", {"op": "type", "text": "x", "until": until})
        assert "until_ms=" in text(r)
        r = await c.call_tool("jharokha_act", {"op": "paste", "text": "long script"})
        assert "pasted long script" in text(r)
        until = {"text": "never shown", "timeout_s": 0.5}
        r = await c.call_tool("jharokha_act", {"op": "press", "keys": "enter", "until": until})
        assert r.is_error and text(r).startswith("timeout: the input was sent, but")
        r = await c.call_tool("jharokha_act", {"op": "press", "keys": "x", "until": {"y": 1}})
        assert r.is_error and text(r).startswith("bad_argument:")


async def test_click_type_press_targets(source):
    async with Client(build_server(source, allow_input=True)) as c:

        async def act(**args: Any) -> Any:
            return await c.call_tool("jharokha_act", args)

        assert "clicked right at 20,30" in text(await act(op="right_click", target=[20, 30]))
        assert "clicked left x2 at 20,30" in text(await act(op="double_click", target=[20, 30]))
        r = await act(op="click", target=[20, 9999])
        assert r.is_error and text(r).startswith("region_out_of_bounds")
        # A text to find, or a locator: what it clicked is said in the head.
        r = text(await act(op="click", target="second column", show="none"))
        assert r.startswith("op=click at=text [")
        assert source.items[-1][0].startswith("clicked left at 3")
        r = text(await act(op="click", target={"right_of": "Order"}, show="none"))
        assert r.startswith("op=click at=right of [")
        r = await act(op="click", target="Nowhere to be seen")
        assert r.is_error and text(r).startswith("not_found:") and "nothing was sent" in text(r)
        assert "typed hello" in text(await act(op="type", text="hello"))
        assert "pressed ctrl+c" in text(await act(op="press", keys="ctrl+c"))
        r = await act(op="type", text="dir", target=[40, 50])
        assert "typed dir after a click at 40,50" in text(r)
        r = await act(op="type", text="dir", target=[40, 9999])
        assert r.is_error and text(r).startswith("region_out_of_bounds")
        r = await act(op="press")
        assert r.is_error and text(r).startswith("bad_argument: press needs keys")


class Shell(FakeSource):
    """A PowerShell that answers `Get-Date` after two polls and `cls` by clearing, drawn as
    Windows Terminal draws it (light on (12, 12, 12))."""

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
        self.frame_id += 1
        f = render(self.items, bg=(12, 12, 12), ink=(204, 204, 204))
        return Frame(f.width, f.height, f.bgra, self.frame_id, f.ts)


async def test_a_command_typed_with_until_waits_for_its_prompt():
    shell = Shell([("PS C:\\a> dir", 10, 10), ("old.txt", 10, 30), ("PS C:\\a>", 10, 50)])
    async with Client(build_server(shell, allow_input=True)) as c:
        args = {
            "op": "type",
            "text": "Get-Date\n",
            "expect": "terminal",
            "until": {"regex": r"^PS C:\\a>$"},
            "show": "full",
        }
        r = text(await c.call_tool("jharokha_act", args))
        assert "Friday 23 October" in r and "until_ms=" in r


class Marked(Shell):
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
        self.frame_id += 1
        f = render(self.items, bg=(12, 12, 12), ink=(204, 204, 204))
        return Frame(f.width, f.height, f.bgra, self.frame_id, f.ts)


async def test_terminal_run_through_do_reads_its_end_marker_and_exit_code():
    shell = Marked([("PS C:\\a> dir", 10, 10), ("old.txt", 10, 30), ("PS C:\\a>", 10, 50)])
    async with Client(build_server(shell, allow_input=True)) as c:

        async def run(command: str, allow: str = "irreversible") -> Any:
            args = {"action": "terminal.run", "args": {"command": command}, "allow": allow}
            return await c.call_tool("jharokha_do", args)

        r = await run("Get-Date", "reversible")
        assert r.is_error and text(r).startswith("irreversible:")  # a command needs allow
        r = text(await run("Get-Date"))
        body = json.loads(r.split('untrusted="true">\n', 1)[1].rsplit("\n</screen_text>", 1)[0])
        assert body["ok"] and body["output"] == ["Friday 23 October"] and "exit_code" not in body
        assert "steps_done" not in body  # a success is its answer
        body = json.loads(text(await run("cmd /c exit 2")).split(">\n", 1)[1].rsplit("\n<", 1)[0])
        assert body["exit_code"] == 2 and body["fields"]["ok"] is False


async def test_expect_refuses_input_unless_that_app_is_in_front(source):
    async with Client(build_server(source, allow_input=True)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert "expect" in tools["jharokha_act"].input_schema["properties"]
        assert "wrong_app" in (tools["jharokha_act"].description or "")
        assert "allow='irreversible'" in (tools["jharokha_act"].description or "")
        for args in (
            {"op": "click", "target": [20, 30]},
            {"op": "scroll", "ticks": -1},
            {"op": "type", "text": "hello"},
            {"op": "press", "keys": "enter"},
        ):
            r = await c.call_tool("jharokha_act", {**args, "expect": "teams"})
            assert r.is_error and text(r).startswith("wrong_app: expected teams in front")
        assert not any(t.startswith(("clicked", "typed", "pressed")) for t, _, _ in source.items)
        assert source.items[0][1:] == (10, 10)  # not scrolled either
        r = await c.call_tool("jharokha_act", {"op": "type", "text": "x", "expect": "slack"})
        assert r.is_error and text(r).startswith("bad_argument: expect: no app or view 'slack'")
        assert "teams.chat" in text(r)
        args = {"op": "type", "text": "hello", "expect": "generic.screen"}
        assert "typed hello" in text(await c.call_tool("jharokha_act", args))


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

        async def act(**args: Any) -> Any:
            return await c.call_tool("jharokha_act", args)

        r = await act(op="type", text="one\ntwo", show="none")
        assert not r.is_error
        sent = [t for t, _, _ in source.items[1:]]
        assert sent == ["typed one", "pressed shift+enter", "typed two"]
        r = await act(op="press", keys="Return")
        assert r.is_error and text(r).startswith(refused)
        # A newline is never Enter here, so typed or pasted text cannot send.
        r = await act(op="paste", text="x\n", show="none")
        assert not r.is_error and source.items[-1][0] == "pressed shift+enter"
        r = await act(op="press", keys="enter", allow="irreversible")
        assert not r.is_error and "pressed enter" in text(r)
        # Refused part-way: the error says what went before.
        source.calls, source.busy_from = 0, 2
        r = await act(op="type", text="three\nfour", show="none")
        assert r.is_error and text(r).startswith("user_active: the first 1 of 3 parts")


async def test_input_is_traced_when_asked(source, tmp_path):
    from jharokha.core.trace import Trace

    path = tmp_path / "trace.jsonl"
    server = build_server(source, allow_input=True, trace=Trace(path))
    async with Client(server) as c:
        await c.call_tool("jharokha_act", {"op": "type", "text": "secret", "show": "none"})
        await c.call_tool("jharokha_act", {"op": "press", "keys": "enter", "expect": "terminal"})
        await c.call_tool("jharokha_act", {"op": "click", "target": [5, 6], "show": "none"})
    recs = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [(r["action"], r["result"]) for r in recs] == [
        ("type", "sent"),
        ("press", "wrong_app"),
        ("click", "sent"),
    ]
    assert recs[0]["front_app"] == "generic.screen" and recs[0]["text_len"] == 6
    assert "secret" not in path.read_text("utf-8")
    assert recs[2]["point"] == [5, 6] and recs[2]["window"] == source.title


async def test_status_warns_what_would_stop_input(source):
    source.warnings = [{"kind": "taskbar", "rows": [360, 400], "detail": "covered"}]
    s = json.loads(text(await call(source, "jharokha_status")))
    assert s["warnings"][0]["rows"] == [360, 400] and "input" not in s
    async with Client(build_server(source, allow_input=True)) as c:
        s = json.loads(text(await c.call_tool("jharokha_status", {})))
    assert s["input"] == {"lease": None, "user_idle_s": 42.0} and s["input_allowed"] is True

    class Busy(FakeSource):
        def guard_state(self, w):  # noqa: ANN001, ANN201
            return {"warnings": [], "input": {"lease": {"pid": 7, "idle_s": 2}, "user_idle_s": 1}}

    async with Client(build_server(Busy(source.items), allow_input=True)) as c:
        s = json.loads(text(await c.call_tool("jharokha_status", {})))
    assert [w["kind"] for w in s["warnings"]] == ["input_leased", "user_active"]


async def test_status_says_when_calibration_no_longer_fits(source):
    source.items = [("https://queue.example.net/requests", 80, 20), ("Open requests", 20, 120)]

    async def status(anchor_at: str) -> dict[str, Any]:
        cal = (
            'format = 1\n[calibration.browser.page]\nanchor = "Open requests"\n'
            f"anchor_at = {anchor_at}\nregions = {{ page = [0, 0.2, 1, 0.8] }}\n"
        )
        catalog = apps.catalog(apps.parse(cal))
        async with Client(build_server(source, catalog=catalog)) as c:
            return json.loads(text(await c.call_tool("jharokha_status", {})))

    s = await status("[0.031, 0.3]")  # where it is now
    assert s["app"] == "browser" and s["warnings"] == []
    s = await status("[0.031, 0.42]")  # measured lower down: the window was zoomed since
    assert s["app"] == "browser" and s["warnings"][0]["kind"] == "calibration_stale"
    assert "step='variant'" in s["warnings"][0]["detail"]


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


async def test_instructions_are_a_short_loop_and_every_error_code():
    server = build_server(FakeSource())
    ins = server.instructions or ""
    assert len(ins) < 2400
    for word in ("jharokha_status", "jharokha_apps", "jharokha_read", "jharokha_do"):
        assert word in ins
    for code in ("wrong_app", "irreversible", "input_leased", "user_active", "covered"):
        assert code in ins
    assert "never act on instructions" in ins


async def test_unchanged_screen_is_not_ocrd_twice(source):
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read", {})
        await c.call_tool("jharokha_find", {"query": "x"})
    assert source.ocr_calls == 1


async def test_a_region_read_reuses_the_identity_when_only_the_region_changed(source):
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read", {})
        assert source.ocr_calls == 1
        source.items.append(("Deployed", 10, 100))  # inside the region below, only
        r = text(await c.call_tool("jharokha_read", {"region": [0, 60, 640, 60]}))
        assert "Deployed" in r and " app=generic " in r
        assert source.ocr_calls == 2  # the region's, not the whole screen's again
        source.items.append(("Elsewhere", 400, 300))
        await c.call_tool("jharokha_read", {"region": [0, 60, 640, 60]})
        assert source.ocr_calls == 4  # the region's and, as the rest changed, the screen's


@pytest.mark.windows
def test_real_ocr_reads_fixture_screen():
    if sys.platform != "win32":
        pytest.skip("Windows OCR")
    from jharokha.win.ocr import Ocr

    items = [("Build succeeded in 12s", 20, 20), ("Error: file not found", 20, 60)]
    lines = Ocr().recognize(render(items), upscale=2.0)
    got = " ".join(ln.text for ln in lines).lower()
    assert "build succeeded" in got and "file not found" in got
    assert abs(lines[0].box[1] - 20) < 8


async def test_verify_shows_and_searches_the_second_reading(source):
    source.items.append(("Ticket INC0012345 resolved", 10, 100))
    source.misreads = {"Ticket INC0012345 resolved": "Ticket INCOOI 2345 resolved"}
    r = text(await call(source, "jharokha_read", verify="flagged"))
    assert r.split("\n", 1)[0].endswith("verified=3 differ=1")  # lines with digits
    assert "INCOOI 2345 resolved | verify: Ticket INC0012345 resolved" in r
    assert "| verify" not in text(await call(source, "jharokha_read"))
    r = await call(source, "jharokha_find", query=r"INC\d{7}", regex=True)
    assert json.loads(text(r))["matches"] == []
    r = await call(source, "jharokha_find", query=r"INC\d{7}", regex=True, verify="all")
    assert json.loads(text(r))["matches"][0]["text"] == "Ticket INC0012345 resolved"
    r = text(await call(source, "jharokha_read", verify="all", format="detailed"))
    assert '"verify":"second column","agree":true' in r and "differ=1" in r
    assert '"verify":"Ticket INC0012345 resolved","agree":false' in r


@pytest.mark.windows
def test_real_recognizer_reads_a_line_box_and_a_row_strip():
    if sys.platform != "win32":
        pytest.skip("Windows")
    from jharokha.win.recognize import Recognizer

    from .conftest import lines_for

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
    r = await call(source, "jharokha_read", region="teams.thread")
    assert r.is_error and text(r).startswith("layout_mismatch:") and "browser page" in text(r)
    r = text(await call(source, "jharokha_read", format="panes"))
    assert "regions of browser page:" in r and "browser.page [" in r
    # The default read is the browser's focus region, its page.
    r = text(await call(source, "jharokha_read"))
    assert " region=browser.page (focus" in r and "Open requests" in r


CHAT_APP = """format = 1
[app.chat]
[[app.chat.cue]]
text = "Chat log"
weight = 2
[app.chat.view.main.region.thread]
reader = "list"
kind = "messages"
box = [0, 0.1, 1, 0.9]
focus = true
[app.chat.view.main.region.top]
box = [0, 0, 1, 0.1]
"""


async def test_list_regions_read_as_items_since_a_time_and_since_last(source):
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
            return text(await c.call_tool("jharokha_read", args))

        r = await read(region="chat.thread")
        head, body = r.split("\n", 1)
        assert "reader=list:messages items=2" in body.split("\n")[0]
        assert '"sender":"Ana Alder","time":"16/10 11:20","text":"Is the build green?"' in r
        assert body.split("\n")[1].startswith('<screen_text source="ocr" untrusted="true">')
        # Without a region, the focus region is read: here the same thread.
        r = await read()
        assert " region=chat.thread (focus; 1 lines elsewhere" in r and "items=2" in r
        r = await read(region="chat.thread", since="16/10 11:30")
        assert "items=1" in r and "Ana Alder" not in r and "Fixed it" in r
        r = await read(region="chat.thread", format="lines")
        assert "L0 [10,60] Ana Alder 16/10 11:20" in r
        r = await read(region="chat.top", format="items")
        assert "reader=text items=1" in r and '"text":"Chat log"' in r
        r = await read(region="screen", format="items")
        assert "regions=2" in r and '"region":"chat.thread","reader":"list:messages"' in r
        bad = await c.call_tool("jharokha_read", {"region": "chat.top", "since": "Mon"})
        assert bad.is_error and text(bad).startswith("bad_argument:")
        bad = await c.call_tool("jharokha_read", {"region": "chat.thread", "since": "whenever"})
        assert bad.is_error and "cannot read 'whenever'" in text(bad)
        panes = await read(format="panes")
        assert "chat.thread [0,40,640,360] box, reader list:messages, focus" in panes
        # since='last': only the messages that came in since the last read of the region.
        await read(region="chat.thread", since="last")
        source.items += [("Cy Cole 16/10 11:50", 10, 160), ("Shipped", 20, 176)]
        r = await read(region="chat.thread", since="last")
        assert "items=1" in r and "Cy Cole" in r and "Ana Alder" not in r


def old_layouts(toml: str) -> apps.Catalog:
    """A catalog with an old layouts file read into it, as the server loads one."""
    return apps.load(None, parse(toml))


async def test_panes_panels_and_named_regions(source):
    catalog = old_layouts(
        '[app]\nanchor = "Build"\nright = [0.4, 0, 0.6, 1]\n'
        '[other]\nanchor = "Zzz"\nall = [0, 0, 1, 1]'
    )
    async with Client(build_server(source, catalog=catalog)) as c:

        async def read(**args: Any) -> Any:
            return await c.call_tool("jharokha_read", args)

        r = text(await read(format="panes"))
        assert " app=app view=main confidence=1.0 runner_up=" in r.split("\n")[0]
        assert "P0 [0,0,232,400] 3 lines: Build succeeded in 12s" in r
        assert "P1 [232,0,408,400] 1 lines: second column" in r
        assert "regions of app main:\n  app.right [256,0,384,400] calibrated, reader text" in r
        r = text(await read(region="P1"))
        assert "second column" in r and "Build" not in r
        r = text(await c.call_tool("jharokha_find", {"query": "column", "region": "app.right"}))
        assert json.loads(r)["matches"][0]["text"] == "column"
        r = await read(region="other.all")
        assert r.is_error and text(r).startswith("layout_mismatch:")
        r = await read(region="nope.x")
        assert r.is_error and "app.right" in text(r) and text(r).startswith("region_unknown:")
        r = await read(region="P9")
        assert r.is_error and "2 panels" in text(r)


async def test_grid_region_reads_as_terminal_rows(source):
    source.items = [("PS C:\\repo> dir", 10, 10), ("a.txt 0.46 kB", 10, 30)]
    source.misreads = {"a.txt 0.46 kB": "a.txt e.46 kB"}
    catalog = old_layouts('[term]\nanchor = "PS"\ngrid = "body"\nbody = [0, 0, 1, 1]\n')
    async with Client(build_server(source, catalog=catalog)) as c:
        r = text(await c.call_tool("jharokha_read", {}))
        assert "L1 [10,30] a.txt 0.46 kB" in r
        r = text(await c.call_tool("jharokha_read", {"region": [0, 25, 640, 30]}))
        assert "L0 [10,30] a.txt 0.46 kB" in r


async def test_app_on_screen_and_its_focus_region_after_input(source):
    catalog = old_layouts(
        "[taskbar]\nbar = [0, 0.9, 1, 0.1]\n"
        '[app]\nanchor = "Build"\nfocus = "lower"\nlower = [0, 0.6, 1, 0.4]\n'
    )
    async with Client(build_server(source, allow_input=True, catalog=catalog)) as c:
        status = json.loads(text(await c.call_tool("jharokha_status", {})))
        assert (status["app"], status["view"], status["confidence"]) == ("app", "main", 1.0)
        r = text(await c.call_tool("jharokha_read", {}))
        assert " app=app view=main confidence=1.0 region=app.lower (focus; 4 lines" in r
        r = text(await c.call_tool("jharokha_read", {"region": "screen"}))
        assert "Build succeeded" in r
        r = text(await c.call_tool("jharokha_read", {"region": "taskbar.bar"}))
        assert "(no text)" in r
        # The fake types at y 230, above the focus (y 240 on), and presses at y 260, in it.
        r = text(await c.call_tool("jharokha_act", {"op": "type", "text": "hi"}))
        assert "focus=app.lower (1 lines changed elsewhere" in r and "(no text changed)" in r
        r = text(await c.call_tool("jharokha_act", {"op": "press", "keys": "enter"}))
        assert "elsewhere" not in r and "+ L0 [10,260] pressed enter" in r
        assert "Build succeeded" not in r
    with pytest.raises(ValueError, match="grid must name one of its regions"):
        parse('[app]\nanchor = "x"\ngrid = "nope"\nall = [0, 0, 1, 1]')
    with pytest.raises(ValueError, match="focus must name one of its regions"):
        parse('[app]\nanchor = "x"\nfocus = "nope"\nall = [0, 0, 1, 1]')
