import ast
import pathlib

from PIL import Image, ImageDraw

from jharokha.core import geometry, panes, text
from jharokha.core.apps import Variant
from jharokha.core.cache import FrameCache
from jharokha.core.models import Frame, Line, Word, union

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


def test_locked_detection():
    assert text.looks_locked(lines_for([("Press Ctrl+Alt+Del to unlock", 0, 0)]))
    assert not text.looks_locked(lines_for([("hello", 0, 0)]))
    # The Windows 11 sign-in screen a live session showed as "normal".
    signin = [("Ana Alder", 0, 0), ("password", 0, 20), ("Sign-in options", 0, 40)]
    assert text.looks_locked(lines_for(signin))
    assert text.looks_locked(lines_for([("Ana Alder", 0, 0), ("Locked", 0, 20), ("PIN", 0, 40)]))
    assert text.looks_locked(lines_for([("13:42", 0, 0), ("Friday, October 23", 0, 30)]))
    # A busy screen that mentions a password or shows a time is not a lock screen.
    busy = [(f"line {i}", 0, 20 * i) for i in range(8)] + [("Password", 0, 200)]
    assert not text.looks_locked(lines_for(busy))
    assert not text.looks_locked(lines_for([("13:42", 0, 0), ("build done", 0, 30)]))


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


def _app_frame() -> Frame:
    """A title bar over a rail, a list and a thread: a divider line under the bar and one
    right of the rail, and the list told from the thread only by its background."""
    img = Image.new("RGBA", (640, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 639, 39), fill=(235, 235, 235))
    d.rectangle((0, 40, 639, 40), fill=(224, 224, 224))
    d.rectangle((0, 41, 59, 399), fill=(235, 235, 235))
    d.rectangle((60, 41, 60, 399), fill=(224, 224, 224))
    d.rectangle((61, 41, 299, 399), fill=(245, 245, 245))
    for s, x, y in [("Search", 250, 12), ("Chat", 8, 60), ("Ana Brook", 70, 60)]:
        d.text((x, y), s, fill="black")
    for i in range(8):
        d.text((70, 90 + 30 * i), f"chat number {i}", fill="black")
        d.text((310, 90 + 30 * i), f"message number {i} in the thread", fill="black")
    return Frame(640, 400, img.tobytes("raw", "BGRA"), 1, 0.0)


def test_panes_from_divider_lines_and_background_colours():
    found = panes.panes(_app_frame())
    assert found == [(0, 0, 640, 40), (0, 40, 60, 360), (60, 40, 240, 360), (300, 40, 340, 360)]
    assert panes.panes(_app_frame(), (0, 40, 640, 360)) == found[1:]
    assert panes.within(found, (280, 100, 100, 50)) == [(280, 100, 20, 50), (300, 100, 80, 50)]
    # Text alone draws no panes: such a screen is read by its text gaps.
    plain = render([(f"line {i} of a plain screen", 10, 10 + 20 * i) for i in range(15)])
    assert panes.panes(plain) == [(0, 0, 640, 400)] and panes.reading_panes(plain) is None


def test_reading_order_reads_pane_by_pane_and_rows_inside():
    found = panes.reading_panes(_app_frame())
    lines = lines_for(
        [("Search", 250, 12), ("Chat", 8, 60)]
        + [(f"chat number {i}", 70, 90 + 30 * i) for i in range(3)]
        + [(f"message {i}", 310, 90 + 30 * i) for i in range(3)]
    )
    got = [ln.text for ln in text.reading_order(lines, found)]
    assert got == ["Search", "Chat"] + [f"chat number {i}" for i in range(3)] + [
        f"message {i}" for i in range(3)
    ]
    assert [box for box, _ in text.cells(lines, None, found)] == found


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
    small = Variant("small", "Find", (0.3, 0.02), {})
    big = Variant("big", "Find", (0.25, 0.02), {})
    bar = Variant("bar", None, None, {})
    assert geometry.choose([small, big], {"Find": (260, 20, 50, 12)}, 1000, 1000) is big
    assert geometry.choose([small, big], {"Find": (295, 20, 50, 12)}, 1000, 1000) is small
    assert geometry.choose([small, big], {"Find": None}, 1000, 1000) is None
    assert geometry.choose([bar], {}, 1000, 1000) is bar


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


def test_grid_takes_calibrated_pitch_width_and_origin():
    # Two rows of a 10 px grid whose words OCR placed a few pixels off.
    lines = [
        Line("ab", (13, 6, 20, 12), (Word("ab", (13, 6, 20, 12)),)),
        Line("cd", (43, 27, 20, 12), (Word("cd", (43, 27, 20, 12)),)),
    ]
    out = text.grid(lines, (0, 0, 200, 100), pitch=20, char_width=10, origin=(10, 2))
    assert [ln.text for ln in out] == ["ab", "   cd"]
