from jharokha.core.watch import changed_bbox, diff_lines, format_diff

from .conftest import lines_for, render


def test_diff_lines_reports_added_and_removed_in_order():
    old = lines_for([("a", 0, 0), ("b", 0, 20), ("c", 0, 40)])
    new = lines_for([("a", 0, 0), ("x", 0, 20), ("c", 0, 40), ("d", 0, 60)])
    added, removed = diff_lines(old, new)
    assert [ln.text for ln in added] == ["x", "d"]
    assert [ln.text for ln in removed] == ["b"]
    out = format_diff(old, new)
    assert "- [0,20] b" in out and "+ L1 [0,20] x" in out and "+ L3 [0,60] d" in out


def test_changed_bbox_ignores_a_caret_and_bounds_a_change():
    items = [("hello", 10, 10)]
    a, b = render(items), render(items)
    assert changed_bbox(a, b) is None and changed_bbox(None, a) == (0, 0, 640, 400)
    c = render([*items, ("world", 10, 100)])
    box = changed_bbox(a, c)
    assert box is not None and box[1] >= 100 and box[1] + box[3] <= 120
