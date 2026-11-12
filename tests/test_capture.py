import json
from pathlib import Path
from typing import Any

from mcp import Client

from jharokha.server import build_server

from .conftest import FakeSource
from .test_server import Marked


def pairs(d: Path) -> list[dict[str, Any]]:
    pngs = sorted(d.glob("*.png"))
    assert sorted(p.stem for p in d.glob("*.json")) == [p.stem for p in pngs]
    return [json.loads(p.with_suffix(".json").read_text("utf-8")) for p in pngs]


async def test_a_read_saves_each_distinct_frame_once(source: FakeSource, tmp_path: Path):
    out = tmp_path / "frames"
    async with Client(build_server(source, capture=out)) as c:
        await c.call_tool("jharokha_read", {"region": "screen"})
        await c.call_tool("jharokha_read", {"region": "screen"})  # the same pixels
        recs = pairs(out)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["call"]["tool"] == "jharokha_read" and rec["call"]["args"]["region"] == "screen"
        assert rec["frame"]["width"] == 640 and rec["frame"]["height"] == 400
        assert rec["app"] == "generic" and rec["partial"] is False
        texts = [ln["text"] for ln in rec["lines"]]
        assert texts == [t for t, _, _ in source.items]
        assert rec["lines"][0]["box"] and rec["lines"][0]["words"][0]["box"]
        assert isinstance(rec["regions"], dict)
        source.items = [*source.items, ("one more line", 10, 300)]
        await c.call_tool("jharokha_find", {"query": "more"})
    recs = pairs(out)
    assert len(recs) == 2 and recs[1]["call"]["tool"] == "jharokha_find"
    assert "one more line" in [ln["text"] for ln in recs[1]["lines"]]


async def test_an_actions_steps_save_their_frames(tmp_path: Path):
    shell = Marked([("PS C:\\a> dir", 10, 10), ("old.txt", 10, 30), ("PS C:\\a>", 10, 50)])
    out = tmp_path / "frames"
    args = {"action": "terminal.run", "args": {"command": "Get-Date"}, "allow": "irreversible"}
    async with Client(build_server(shell, allow_input=True, capture=out)) as c:
        await c.call_tool("jharokha_do", {"action": "terminal.setup"})
        r = await c.call_tool("jharokha_do", args)
    assert not r.is_error
    recs = [rec for rec in pairs(out) if rec["call"]["args"].get("action") == "terminal.run"]
    assert len(recs) >= 2  # the prompt before, the output after
    assert all(rec["call"]["tool"] == "jharokha_do" for rec in recs)
    assert all(rec["call"]["args"].items() >= args.items() for rec in recs)
    assert all(rec["app"] == "terminal" for rec in recs)
    assert any("Friday 18 October" in ln["text"] for rec in recs for ln in rec["lines"])
    assert any(k.startswith("terminal.") for rec in recs for k in rec["regions"])


async def test_nothing_is_written_without_the_flag(
    source: FakeSource, tmp_path: Path, monkeypatch: Any
):
    monkeypatch.chdir(tmp_path)
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read", {"region": "screen"})
        await c.call_tool("jharokha_find", {"query": "x"})
    assert list(tmp_path.iterdir()) == []


def calls(d: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (d / "calls.jsonl").read_text("utf-8").splitlines()]


async def test_each_call_logs_its_result_and_frames(source: FakeSource, tmp_path: Path):
    out = tmp_path / "frames"
    async with Client(build_server(source, capture=out)) as c:
        await c.call_tool("jharokha_read", {"region": "screen"})
        await c.call_tool("jharokha_read", {"region": "screen"})  # the same pixels
    first, second = calls(out)
    assert first["tool"] == second["tool"] == "jharokha_read"
    assert first["args"]["region"] == "screen" and first["ok"] is True
    assert first["ms"] >= 0 and first["ts"] > 0
    assert source.items[0][0] in first["result"]
    stem = sorted(out.glob("*.png"))[0].stem
    assert first["frames"] == [stem] and second["frames"] == [stem]


async def test_an_error_is_logged_with_its_code(source: FakeSource, tmp_path: Path):
    out = tmp_path / "frames"
    async with Client(build_server(source, capture=out)) as c:
        r = await c.call_tool("jharokha_read", {"region": [1, 2]})
    assert r.is_error
    (rec,) = calls(out)
    assert rec["ok"] is False and rec["error"]["code"] == "region_out_of_bounds"
    assert rec["error"]["type"] == "ScreenError" and "region must be" in rec["error"]["message"]


def test_a_grid_regions_rows_are_added_to_the_frames_sidecar(tmp_path: Path):
    from jharokha.core.capture import Capture
    from jharokha.core.identify import Identity
    from jharokha.core.models import Frame, Line

    from .conftest import render

    cap = Capture(tmp_path)
    frame = render([("JK1 2030-10-23 10:00:00 True 0 C:\a", 10, 10)], bg=(12, 12, 12), ink="white")
    ident = Identity("terminal", "shell", 0.9)
    png = cap.save(frame, ident, [], {"terminal.body": (0, 0, 640, 400)})
    assert png is not None
    rows = [
        Line("JK1 2030-10-23 10:00:00 True 0 C:\a", (10, 10, 300, 20)),
        Line("> ", (10, 30, 14, 20)),
    ]
    cap.rows(frame, "terminal.body", rows)
    data = json.loads(png.with_suffix(".json").read_text("utf-8"))
    assert [r["text"] for r in data["rows"]["terminal.body"]] == [rows[0].text, "> "]
    assert data["rows"]["terminal.body"][1]["box"] == [10, 30, 14, 20]
    o = render([("something else", 10, 10)])
    other = Frame(o.width, o.height, o.bgra, 2, o.ts)
    cap.rows(other, "terminal.body", [])  # not the frame saved last: nothing written
    assert json.loads(png.with_suffix(".json").read_text("utf-8"))["rows"] == data["rows"]


def test_calls_in_parallel_each_log_their_own_name_and_result(tmp_path: Path):
    """A status and a read called at once must not share one slot: shared, the status was
    logged as the read and the read crashed on the way out ("Error executing tool")."""
    import threading

    from jharokha.core.capture import Capture

    cap = Capture(tmp_path)
    inside = threading.Barrier(2)
    leave_b = threading.Event()

    def a() -> None:
        with cap.during("jharokha_status", {}) as done:
            inside.wait()
            done.update({"ok": True, "result": "status"})
        leave_b.set()

    def b() -> None:
        with cap.during("jharokha_read", {"region": None}) as done:
            inside.wait()
            leave_b.wait()  # ends after a
            assert cap.call == {"tool": "jharokha_read", "args": {"region": None}}
            done.update({"ok": True, "result": "read"})

    ts = [threading.Thread(target=f) for f in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    recs = [json.loads(ln) for ln in (tmp_path / "calls.jsonl").read_text("utf-8").splitlines()]
    assert {(r["tool"], r["result"]) for r in recs} == {
        ("jharokha_status", "status"),
        ("jharokha_read", "read"),
    }


async def test_an_actions_own_failure_is_logged_without_its_screen_text(
    source: FakeSource, tmp_path: Path
):
    """calls.jsonl logs an action's own failure: a do that fails returns ok=false inside a
    tool result that succeeded, so the tool's ok alone undercounts failures."""
    out = tmp_path / "frames"
    async with Client(build_server(source, allow_input=True, capture=out)) as c:
        r = await c.call_tool("jharokha_do", {"action": "terminal.run", "args": {"command": "x"}})
        await c.call_tool("jharokha_wait", {"text": "Order"})
    assert not r.is_error
    do, wait = calls(out)
    assert do["ok"] is True  # the tool's
    action = do["action"]
    assert action["id"] == "terminal.run" and action["ok"] is False and action["ms"] >= 0
    assert action["failed"]["step"] == "1:focus"
    assert "taskbar.terminal" in action["failed"]["reason"]
    assert "candidates" not in action["failed"]
    assert wait["action"] == {"ok": True, "ended": "appeared"}
