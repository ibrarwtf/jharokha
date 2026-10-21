import json
import time

from mcp import Client

from jharokha.core.watch import Watcher, diff_lines
from jharokha.server import build_server

from .conftest import lines_for, render
from .test_server import text


def test_diff_lines_reports_added_and_removed_in_order():
    old = lines_for([("a", 0, 0), ("b", 0, 20), ("c", 0, 40)])
    new = lines_for([("a", 0, 0), ("x", 0, 20), ("c", 0, 40), ("d", 0, 60)])
    added, removed = diff_lines(old, new)
    assert [ln.text for ln in added] == ["x", "d"]
    assert [ln.text for ln in removed] == ["b"]


def test_watcher_skips_ocr_when_pixels_did_not_change():
    items = [("hello", 10, 10)]
    calls = []

    def ocr(frame):  # noqa: ANN001, ANN202
        calls.append(1)
        return lines_for(items)

    w = Watcher(capture=lambda: render(items), ocr=ocr)
    w.baseline(render(items), lines_for(items))
    assert w.observe(render(items)) is None and calls == []
    items.append(("world", 10, 40))
    change = w.observe(render(items))
    assert change and [ln.text for ln in change.added] == ["world"] and len(calls) == 1


async def test_watch_tool_logs_changes_in_the_background(source):
    async with Client(build_server(source)) as c:
        r = await c.call_tool("jharokha_watch", {"action": "start", "interval_s": 0.2})
        s = json.loads(text(r))
        assert s["watching"] and s["interval_s"] == 0.2
        source.items.append(("Deploy finished", 10, 100))
        time.sleep(0.6)
        source.items = [i for i in source.items if i[0] != "Error: file not found"]
        time.sleep(0.6)
        head, body = text(await c.call_tool("jharokha_changes", {})).split("\n", 1)
        assert json.loads(head)["returned"] == 2
        assert "+ [10,100] Deploy finished" in body
        assert "- [10,40] Error: file not found" in body
        assert "(no changes)" in text(await c.call_tool("jharokha_changes", {"since_seq": 2}))
        s = json.loads(text(await c.call_tool("jharokha_watch", {"action": "stop"})))
        assert not s["watching"]


async def test_changes_without_watcher_diffs_against_last_read(source):
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read_text", {})
        source.items.append(("New toast", 10, 120))
        r = text(await c.call_tool("jharokha_changes", {}))
        assert "+ [10,120] New toast" in r
        assert json.loads(r.split("\n", 1)[0])["watching"] is False
        assert "(no changes)" in text(await c.call_tool("jharokha_changes", {}))


def test_watcher_stops_itself_when_nobody_asks():
    items = [("hello", 10, 10)]
    w = Watcher(
        capture=lambda: render(items), ocr=lambda f: lines_for(items), interval_s=0.05, idle_s=0.2
    )
    w.start()
    assert w.running
    time.sleep(0.5)
    assert not w.running and w.frames >= 2
