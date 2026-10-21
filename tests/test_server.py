import json
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from jharokha.server import build_server

from .conftest import FakeSource


async def call(source: FakeSource, name: str, **args: Any):
    async with Client(build_server(source)) as c:
        return await c.call_tool(name, args)


def text(r: Any, i: int = 0) -> str:
    assert isinstance(r.content[i], TextContent)
    return r.content[i].text


async def test_lists_five_read_only_tools(source):
    async with Client(build_server(source)) as c:
        tools = (await c.list_tools()).tools
    assert sorted(t.name for t in tools) == [
        "jharokha_find_text",
        "jharokha_read_text",
        "jharokha_screenshot",
        "jharokha_status",
        "jharokha_wait_for_text",
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
    assert "L0 [10,10] Build succeeded in 12s\nL1 [300,10] second column\nL2 [10,40]" in body
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
    assert len(m) == 1 and m[0]["ref"] == "L2" and m[0]["text"] == "file not found"
    assert m[0]["center"][1] == 46


async def test_screenshot_region_and_scale(source):
    r = await call(source, "jharokha_screenshot", region=[0, 0, 320, 200], max_long_edge=160)
    meta = json.loads(text(r))
    assert meta["region"] == [0, 0, 320, 200] and meta["scale"] == 0.5
    assert r.content[1].type == "image" and r.content[1].mime_type == "image/png"


async def test_wait_for_text_and_timeout(source):
    r = await call(source, "jharokha_wait_for_text", query="succeeded", timeout_s=1)
    assert json.loads(text(r))["found"] is True
    r = await call(source, "jharokha_wait_for_text", query="succeeded", gone=True, timeout_s=0.6)
    assert r.is_error and text(r).startswith("timeout:") and "Build succeeded" in text(r)


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
