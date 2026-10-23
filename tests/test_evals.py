"""The eval's own scores: if these are wrong, every number the eval prints is."""

from __future__ import annotations

import sys

import pytest

from evals import metrics
from evals.__main__ import compare


def test_distance_cer_wer():
    assert metrics.distance("kitten", "sitting") == 3
    assert metrics.cer(["abcd"], ["abcd"]) == 0
    assert metrics.cer(["abcd"], ["abxd"]) == 0.25
    assert metrics.cer(["ab", "cd"], ["cd", "ab"]) > 0  # order counts
    assert metrics.wer(["one two three four"], ["one too three"]) == 0.5


def test_bag_f1_ignores_order_and_lines_exact_counts_duplicates():
    assert metrics.bag_f1(["a b", "c d"], ["c d", "a b"]) == 1
    assert metrics.bag_f1(["a b"], ["x y"]) == 0
    assert metrics.lines_exact(["x", "x", "y"], ["x", "y", "z"]) == 2 / 3
    assert metrics.lookalike_misses(["INC0012345"], ["INCOO12345"]) == 2


def test_compare_flags_only_real_drops():
    base = {"t.cer": 0.10, "t.lines_exact": 0.80, "t.tokens": 300, "t.lookalike": 4}
    assert (
        compare({"t.cer": 0.11, "t.lines_exact": 0.79, "t.tokens": 320, "t.lookalike": 5}, base)
        == []
    )
    worse = compare({"t.cer": 0.15, "t.lines_exact": 0.70, "t.tokens": 400, "t.lookalike": 7}, base)
    assert len(worse) == 4
    assert compare({}, {"t.cer": 0.1}) == ["t.cer: missing (baseline 0.1)"]


@pytest.mark.skipif(sys.platform != "win32", reason="GDI")
def test_gdi_renders_and_locates_finds():
    from evals.render import render
    from evals.screens import outlook

    s = outlook(1)
    frame, boxes = render(s, "gdi")
    assert (frame.width, frame.height) == (s.width, s.height) and any(frame.bgra[:4000])
    assert len(boxes) == len(s.finds) and all(w > 0 and h > 0 for _, _, w, h in boxes)
