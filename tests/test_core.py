import ast
import pathlib

from jharokha.core import text
from jharokha.core.cache import FrameCache
from jharokha.core.models import Line, Word

from .conftest import lines_for, render

CORE = pathlib.Path(__file__).resolve().parents[1] / "src" / "jharokha" / "core"


def test_core_imports_neither_mcp_nor_windows():
    for path in CORE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                assert top not in {"mcp", "winrt", "ctypes", "jharokha_win"}, (
                    f"{path.name} imports {n}"
                )
                assert not n.startswith("jharokha.win"), f"{path.name} imports {n}"


def test_reading_order_rows_then_columns():
    lines = lines_for([("right", 300, 10), ("left", 10, 12), ("below", 10, 40)])
    assert [ln.text for ln in text.reading_order(lines)] == ["left", "right", "below"]


def test_concise_format_and_truncation():
    lines = lines_for([(f"line {i}", 10, 10 + 20 * i) for i in range(5)])
    out = text.format_concise(lines, offset=1, limit=2)
    assert '<screen_text source="ocr" untrusted="true">' in out
    assert "L1 [10,30] line 1\nL2 [10,50] line 2" in out
    assert "offset=3" in out
    assert "truncated" not in text.format_concise(lines)


def test_detailed_flags_lookalikes_and_strips_controls():
    lines = [
        Line(
            "Order​ AB12",
            (0, 0, 60, 12),
            (Word("Order​", (0, 0, 30, 12)), Word("AB12", (35, 0, 25, 12))),
        )
    ]
    out = text.format_detailed(lines)
    assert '"text":"Order AB12"' in out
    assert '"text":"AB12","box":[35,0,25,12],"lookalike":true' in out
    assert '"conf":null' in out


def test_find_exact_fuzzy_regex_and_all():
    lines = lines_for(
        [("Build succeeded in 12s", 10, 10), ("Order AB12 shipped", 10, 40), ("Order AB13", 10, 70)]
    )
    m = text.find(lines, "SUCCEEDED")
    assert m and m[0].ref == "L0" and m[0].box == (52, 10, 63, 12) and m[0].center == (83, 16)
    assert text.find(lines, "0rder ABl2")[0].text == "Order AB12"  # OCR lookalikes folded
    assert text.find(lines, "succeded")[0].text == "succeeded"  # fuzzy fallback
    assert [m.ref for m in text.find(lines, "order", all=True)] == ["L1", "L2"]
    assert text.find(lines, r"AB1\d", regex=True, all=True)[1].ref == "L2"
    assert text.find(lines, "missing") == []


def test_locked_detection():
    assert text.looks_locked(lines_for([("Press Ctrl+Alt+Del to unlock", 0, 0)]))
    assert not text.looks_locked(lines_for([("hello", 0, 0)]))


def test_cache_ignores_cursor_sized_changes_only():
    cache = FrameCache()
    a = render([("hello", 10, 10)])
    cache.put(a, None, [])
    cursor = render([("hello", 10, 10), ("|", 100, 10)])
    assert cache.get(cursor, None) == []
    changed = render([("hello world", 10, 10)])
    assert cache.get(changed, None) is None
