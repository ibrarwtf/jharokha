"""evals/resources.py: its arithmetic, its budget check, and one short run end to end."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR and process APIs")
BUDGET = Path(__file__).resolve().parents[1] / "evals" / "budget.json"


def test_percentile_is_nearest_rank_and_check_flags_over_and_missing():
    from evals.resources import check, percentile

    xs = [float(x) for x in range(1, 21)]
    assert percentile(xs, 95) == 19 and percentile(xs, 50) == 10 and percentile([], 95) == 0
    assert percentile([5.0], 95) == 5
    assert check({"a": 1, "b": 2}, {"a": 1, "b": 3}) == []
    assert check({"a": 1.5}, {"a": 1, "b": 3}) == ["a: 1.5 over budget 1", "b: missing (budget 3)"]


@pytest.mark.windows
async def test_short_run_reports_every_budgeted_metric():
    from evals.resources import check, measure

    metrics, source = await measure(repeats=1, idle_s=0.5)
    assert source.screen.app == "teams"
    budgets = json.loads(BUDGET.read_text(encoding="utf-8"))
    for profile in ("laptop", "ci"):
        assert check(metrics, {k: float("inf") for k in budgets[profile]["max"]}) == []
    assert 0 < metrics["rss_idle_mb"] <= metrics["rss_first_read_mb"] <= metrics["rss_peak_mb"]
    assert metrics["read_full.wall_p50_ms"] > metrics["read_cached.wall_p50_ms"]
    assert metrics["verify.wall_ms_per_line"] > 0 and metrics["idle.cpu_pct"] >= 0
