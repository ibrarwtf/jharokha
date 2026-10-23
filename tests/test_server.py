import json
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from jharokha.core.layout import parse
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
        "jharokha_changes",
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
    assert '"verify":"second column"' in r and "differ=1" in r


@pytest.mark.windows
def test_real_verifier_rereads_a_line():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows")
    from jharokha.win.verify import Verifier

    from .conftest import lines_for, render

    items = [("Order AB12 shipped", 20, 20)]
    assert Verifier().read(render(items), [lines_for(items)[0].box]) == ["Order AB12 shipped"]


async def test_layout_panels_and_named_regions(source):
    layouts = parse(
        '[app]\nanchor = "Build"\nright = [0.4, 0, 0.6, 1]\n'
        '[other]\nanchor = "Zzz"\nall = [0, 0, 1, 1]'
    )
    async with Client(build_server(source, layouts=layouts)) as c:
        r = text(await c.call_tool("jharokha_layout", {}))
        assert "P0 [0,0,232,400] 3 lines: Build succeeded in 12s" in r
        assert "P1 [232,0,408,400] 1 lines: second column" in r
        assert "layout app (on screen): app.right=[256,0,384,400]" in r
        assert "layout other (not on screen: needs 'Zzz')" in r
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


async def test_app_on_screen_and_its_focus_region_after_input(source):
    layouts = parse(
        "[taskbar]\nbar = [0, 0.9, 1, 0.1]\n"
        '[app]\nanchor = "Build"\nfocus = "lower"\nlower = [0, 0.6, 1, 0.4]\n'
    )
    async with Client(build_server(source, allow_input=True, layouts=layouts)) as c:
        assert json.loads(text(await c.call_tool("jharokha_status", {})))["app"] == "app"
        assert " app=app\n" in text(await c.call_tool("jharokha_read_text", {}))
        # The fake types at y 230, above the focus (y 240 on), and presses at y 260, in it.
        r = text(await c.call_tool("jharokha_type", {"text": "hi"}))
        assert "focus=app.lower (1 lines changed elsewhere" in r and "(no text changed)" in r
        r = text(await c.call_tool("jharokha_press", {"keys": "enter"}))
        assert "elsewhere" not in r and "+ L0 [10,260] pressed enter" in r
        assert "Build succeeded" not in r
    with pytest.raises(ValueError, match="focus must name one of its regions"):
        parse('[app]\nanchor = "x"\nfocus = "nope"\nall = [0, 0, 1, 1]')
