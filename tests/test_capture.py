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
        r = await c.call_tool("jharokha_do", args)
    assert not r.is_error
    recs = pairs(out)
    assert len(recs) >= 2  # the prompt before, the output after
    assert all(rec["call"]["tool"] == "jharokha_do" for rec in recs)
    assert all(rec["call"]["args"].items() >= args.items() for rec in recs)
    assert all(rec["app"] == "terminal" for rec in recs)
    assert any("Friday 23 October" in ln["text"] for rec in recs for ln in rec["lines"])
    assert any(k.startswith("terminal.") for rec in recs for k in rec["regions"])


async def test_nothing_is_written_without_the_flag(
    source: FakeSource, tmp_path: Path, monkeypatch: Any
):
    monkeypatch.chdir(tmp_path)
    async with Client(build_server(source)) as c:
        await c.call_tool("jharokha_read", {"region": "screen"})
        await c.call_tool("jharokha_find", {"query": "x"})
    assert list(tmp_path.iterdir()) == []
