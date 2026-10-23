import ast
import pathlib

import pytest

from jharokha.core import layout, text
from jharokha.core.cache import FrameCache
from jharokha.core.models import Line, Word, union

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
    lines = lines_for([("right", 60, 10), ("left", 10, 12), ("below", 10, 40)])
    assert [ln.text for ln in text.reading_order(lines)] == ["left", "right", "below"]


def test_reading_order_reads_side_by_side_panels_one_after_the_other():
    # A header across the top, then a chat list beside a thread; words are 7 px, lines 12 px.
    lines = lines_for(
        [
            ("Search everything here", 10, 5),
            ("Alice", 10, 50),
            ("Alice 10:02", 300, 50),
            ("hello there", 10, 70),
            ("can you check order AB12", 300, 70),
            ("Bob", 10, 90),
            ("on it", 300, 90),
        ]
    )
    got = [ln.text for ln in text.reading_order(lines)]
    assert got == [
        "Search everything here",
        "Alice",
        "hello there",
        "Bob",
        "Alice 10:02",
        "can you check order AB12",
        "on it",
    ]


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


def test_find_exact_fuzzy_regex_and_all():
    lines = lines_for(
        [("Build succeeded in 12s", 10, 10), ("Order AB12 shipped", 10, 40), ("Order AB13", 10, 70)]
    )
    m = text.find(lines, "SUCCEEDED")
    assert m and m[0].ref == "L0" and m[0].box == (52, 10, 63, 12) and m[0].center == (83, 16)
    assert text.find(lines, "0rder ABl2")[0].text == "Order AB12"  # OCR lookalikes folded
    assert text.find(lines, "succeded")[0].text == "succeeded"  # fuzzy fallback
    assert [m.ref for m in text.find(lines, "order", all=True)] == ["L1", "L2"]
    rx = text.find(lines, r"AB1\d", regex=True, all=True)
    assert rx[1].ref == "L2" and rx[0].text == "AB12" and rx[0].box == (52, 40, 28, 12)
    assert text.find(lines, "missing") == []


def test_find_tolerates_what_ocr_does_to_punctuation():
    """The misses that timed out waits in a live terminal."""
    lines = lines_for(
        [
            ("Name —— ——", 0, 0),
            ("saved links .txt", 0, 20),
            ("see https : //aka . ms/x", 0, 40),
            (r"PS C:\repo>I", 0, 60),
            ("a - b", 0, 80),
        ]
    )
    assert text.find(lines, "----")[0].ref == "L0"
    assert text.find(lines, "links.txt")[0].ref == "L1"
    assert text.find(lines, "https://aka.ms/x")[0].ref == "L2"
    assert text.find(lines, r"^PS C:.*>\s*$", regex=True)[0].ref == "L3"
    assert text.find(lines, r"links\.txt|Error", regex=True)[0].ref == "L1"
    assert text.find(lines, "--") == text.find(lines, "----")  # one hyphen is not a rule


def test_grid_rebuilds_terminal_rows():
    """Pieces of a row OCR split and spaced apart, a wrapped row, a table and a dotted zero."""

    def word(t: str, col: int, row: int) -> Word:
        return Word(t, (10 + 7 * col + 1, 5 + 20 * row + (row % 2), 7 * len(t) - 2, 12))

    def line(*ws: Word) -> Line:
        return Line(" ".join(w.text for w in ws), union(w.box for w in ws), ws)

    lines = [
        line(word("PS", 0, 0), word("C:\\repo>", 3, 0)),
        line(word("dir", 12, 0)),  # same row, its own OCR line, a pixel lower
        line(word("dist/index", 0, 1), word(".", 10, 1), word("html", 11, 1)),
        line(word("e.46", 18, 1), word("kB", 23, 1)),
        line(word("x" * 88, 0, 2)),  # reaches the right edge: wraps
        line(word("yz", 0, 3)),
        line(word("Found", 0, 5), word("e", 6, 5), word("errors", 8, 5)),
        Line("menu", (700, 5, 30, 12), (Word("menu", (700, 5, 30, 12)),)),
    ]
    out = text.grid(lines, (0, 0, 640, 200))
    assert [ln.text for ln in out] == [
        "PS C:\\repo> dir",
        "dist/index.html   0.46 kB",
        "x" * 88 + "yz",
        "Found e errors",
        "menu",
    ]
    assert text.grid(lines[:1], (0, 0, 640, 200)) == lines[:1]


def test_command_output_between_its_line_and_the_next_prompt():
    ps = r"^PS [^>]*>"
    before = [("PS C:\\a> dir", 0, 0), ("old output", 0, 20), ("PS C:\\a>", 0, 40)]
    running = lines_for([*before, ("PS C:\\a> Get-Date", 0, 60), ("Friday", 0, 80)])
    assert text.command_output(running, ps, "Get-Date") is None
    done = lines_for(
        [*before, ("PS C:\\a> Get-Date", 0, 60), ("Friday", 0, 80), ("PS C:\\a>I", 0, 100)]
    )
    out, whole = text.command_output(done, ps, "Get-Date")  # type: ignore[misc]
    assert [ln.text for ln in out] == ["Friday"] and whole
    # The same command again: its newest line counts, not the earlier run.
    again = lines_for(
        [
            ("PS C:\\a> dir", 0, 0),
            ("one", 0, 20),
            ("PS C:\\a> dir", 0, 40),
            ("two", 0, 60),
            ("PS C:\\a>", 0, 80),
        ]
    )
    assert [ln.text for ln in text.command_output(again, ps, "dir")[0]] == ["two"]  # type: ignore[index]
    # Longer than the screen: the command's line scrolled away.
    scrolled = lines_for([("row 7", 0, 0), ("row 8", 0, 20), ("PS C:\\a>", 0, 40)])
    assert text.command_output(scrolled, ps, "1..8") == (scrolled[:2], False)


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


def test_suspect_lines_and_applying_second_readings():
    lines = lines_for([("Ticket INCOOI 2345 resolved", 0, 0), ("see you soon", 0, 20)])
    assert text.suspect(lines[0]) and not text.suspect(lines[1])
    assert text.agree("AB 12", "AB12") and not text.agree("AB12", "ABI2")
    fixed = text.apply_verified(lines, {0: "Ticket INC0012345 resolved", 1: "see you soon"})
    assert fixed[0].text == "Ticket INC0012345 resolved" and fixed[0].words == (
        Word("Ticket INC0012345 resolved", lines[0].box),
    )
    assert fixed[1] is lines[1]
    out = text.format_concise(lines, alt={0: "Ticket INC0012345 resolved", 1: "see you soon"})
    assert "L0 [0,0] Ticket INCOOI 2345 resolved | verify: Ticket INC0012345 resolved" in out
    assert "L1 [0,20] see you soon\n" in out


def test_panels_cover_their_whole_cell():
    lines = lines_for(
        [
            ("Search messages files and people in this org", 10, 5),
            ("Alice", 10, 50),
            ("Alice 10:02", 300, 50),
            ("Bob", 10, 90),
        ]
    )
    cells = text.panels(lines, (0, 0, 640, 400))
    assert [(box, [ln.text for ln in ps]) for box, ps in cells] == [
        ((0, 0, 640, 33), ["Search messages files and people in this org"]),
        ((0, 33, 172, 367), ["Alice", "Bob"]),
        ((172, 33, 468, 367), ["Alice 10:02"]),
    ]


def test_layouts_parse_validate_and_scale():
    example = pathlib.Path(__file__).resolve().parents[1] / "layouts.example.toml"
    layouts = layout.load(example)
    assert layouts["teams"].anchor == "Chat" and "thread" in layouts["teams"].regions
    assert layout.to_box(layouts["teams"].regions["thread"], 1000, 500) == (240, 0, 760, 500)
    for bad in ("[a]\nr = [0, 0, 2, 1]", "[a]\nr = [0.5, 0, 0.6, 1]", "[a]\nr = [0, 0]", "a = 1"):
        with pytest.raises(layout.LayoutError):
            layout.parse(bad)


def test_stacked_blocks_of_one_width_are_one_panel():
    long = "a line that runs across the whole width of the window here"
    lines = lines_for(
        [(long, 10, 5), (long, 10, 60), (long, 10, 120), ("ok", 10, 200), ("ok", 10, 230)]
    )
    lines += lines_for([("cancel", 400, 200), ("cancel", 400, 230)])
    cells = text.panels(lines, (0, 0, 640, 400))
    assert [len(ps) for _, ps in cells] == [3, 2, 2]
    assert cells[0][0] == (0, 0, 640, 166)


def test_variants_are_chosen_by_where_the_anchor_is():
    layouts = layout.parse(
        '["app@small"]\nanchor = "Find"\nanchor_at = [0.3, 0.02]\nr = [0, 0, 0.5, 1]\n'
        '["app@big"]\nanchor = "Find"\nanchor_at = [0.25, 0.02]\nr = [0, 0, 0.6, 1]\n'
        "[bar]\nr = [0, 0.9, 1, 0.1]"
    )
    group = [la for la in layouts.values() if la.app == "app"]
    assert layout.choose(group, {"Find": (260, 20, 50, 12)}, 1000, 1000) is layouts["app@big"]
    assert layout.choose(group, {"Find": (295, 20, 50, 12)}, 1000, 1000) is layouts["app@small"]
    assert layout.choose(group, {"Find": None}, 1000, 1000) is None
    assert layout.choose([layouts["bar"]], {}, 1000, 1000) is layouts["bar"]


def test_right_aligned_own_messages_stay_in_the_thread():
    lines = lines_for(
        [
            ("Alice 10:02", 10, 10),
            ("can you check order AB12", 10, 30),
            ("on it", 400, 70),
            ("Alice 10:07", 10, 110),
            ("thanks", 10, 130),
            ("sent the tracking id", 400, 170),
        ]
    )
    assert len(text.panels(lines, (0, 0, 640, 400))) == 1


def test_measure_finds_columns_and_bands_and_writes_toml():
    rail = [("A", 5, 20 + 28 * i) for i in range(13)]
    side = [(f"chat {i}", 60, 20 + 28 * i) for i in range(13)]
    wide = [("Header title", 200, 10), ("Tabs here", 200, 30)]
    wide += [(f"message number {i}", 200, 120 + 20 * i) for i in range(8)]
    wide += [("Type a message", 200, 340), ("12:00", 600, 375)]
    lines = lines_for(rail + side + wide)
    regions = layout.measure(lines, 640, 400)
    assert list(regions) == ["col1", "col2", "top", "body", "bottom1", "bottom2"]
    assert regions["col1"][0] == 0 and regions["col2"][0] < regions["top"][0]
    assert regions["top"][1] == 0 and regions["body"][1] < 120 <= regions["body"][1] + 40
    fr = {k: layout.to_fraction(b, 640, 400) for k, b in regions.items()}
    t = layout.table("app@-1", "Header title", fr, (0.3, 0.02), {"focus": "body", "grid": "body"})
    text_ = layout.replace_app('[other]\nr = [0, 0, 1, 1]\n["app@0"]\nanchor = "x"\n', "app", t)
    parsed = layout.parse(text_)
    assert set(parsed) == {"other", "app@-1"} and parsed["app@-1"].anchor_at == (0.3, 0.02)
    assert parsed["app@-1"].focus == parsed["app@-1"].grid == "body"
    assert layout.to_box(parsed["app@-1"].regions["body"], 640, 400) == regions["body"]
