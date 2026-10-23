"""Run the screen evals, print a summary, and compare it with the baseline.

    uv run python -m evals [--quick | --full] [--check] [--update] [--out FILE] [--show NAME]

Each generated screen is drawn (GDI with ClearType, then through an RDP-like codec; the
full run adds clean GDI and PIL), read through the real Windows OCR and jharokha's reading
order (and its grid where the screen has one), and scored:

- recognition: CER, WER, bag-of-words F1, lines exact, lookalike misses, tokens per read
- find: the first match's centre falls inside the box of the text it should land on
- lock: read as locked exactly when it is a lock or sign-in screen
- app: the layouts file's anchor picks the right app, or none
- panes: for each pane the screen was drawn with, the best IoU of a pane found from the
  pixels (mean, and the share at 0.8 or more)

--check fails (exit 1) when a summary number is worse than evals/baseline-<mode>.json by
more than its tolerance; --update rewrites that file. Timings are printed, never gated."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from evals import metrics
from evals.render import FontMissing, Screen, render
from evals.screens import LAYOUTS, suite
from jharokha import version
from jharokha.core.layout import parse
from jharokha.core.models import Line
from jharokha.core.panes import panes, reading_panes
from jharokha.core.text import find, format_concise, grid, looks_locked, reading_order
from jharokha.win.ocr import Ocr

HERE = Path(__file__).parent
LOWER_IS_BETTER = ("cer", "wer", "lookalike", "tokens")
TOLERANCE = {"tokens": 0.10}  # relative; everything else is a rate, absolute
RATE_TOLERANCE = 0.02


def detect_app(lines: list[Line]) -> str | None:
    for name, lay in parse(LAYOUTS).items():
        if lay.anchor and find(lines, lay.anchor):
            return name
    return None


def score(screen: Screen, config: str, ocr: Ocr) -> dict[str, Any]:
    engine, _, codec = config.partition("+")
    frame, boxes = render(screen, engine, codec == "codec")
    t = time.perf_counter()
    raw = ocr.recognize(frame)
    ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    found = panes(frame)
    panes_ms = (time.perf_counter() - t) * 1000
    lines = reading_order(raw, reading_panes(frame))
    if screen.grid:
        lines = grid(lines, screen.grid)
    got = [ln.text.rstrip() for ln in lines]
    truth = screen.truth
    row: dict[str, Any] = {
        "screen": screen.name,
        "app": screen.app,
        "config": config,
        "ms": round(ms),
        "panes_ms": round(panes_ms, 1),
        "cer": metrics.cer(truth, got),
        "wer": metrics.wer(truth, got),
        "bag_f1": metrics.bag_f1(truth, got),
        "lines_exact": metrics.lines_exact(truth, got),
        "lookalike": metrics.lookalike_misses(truth, got),
        "tokens": len(format_concise(lines)) // 4,
        "lock_ok": looks_locked(lines) == screen.locked,
        "app_ok": detect_app(lines)
        == (screen.app if screen.app in ("teams", "outlook", "terminal") else None),
    }
    hits = []
    for f, (x, y, w, h) in zip(screen.finds, boxes, strict=True):
        m = find(lines, f.query, regex=f.regex)
        c = m[0].center if m else None
        # A few pixels of slack: OCR boxes hug the ink, measured boxes the character cells.
        ok = c is not None and x - 3 <= c[0] <= x + w + 3 and y - 3 <= c[1] <= y + h + 3
        hits.append(ok)
        if not ok:
            row.setdefault("find_misses", []).append(f.query)
    if hits:
        row["find_hit"] = mean(hits)
    if screen.panes:
        best = [max(metrics.iou(p, f) for f in found) for p in screen.panes]
        row["panes_iou"] = mean(best)
        row["panes_hit"] = mean(b >= 0.8 for b in best)
    row["got"] = got
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[f"{r['app']}.{r['config']}"].append(r)
    out: dict[str, float] = {}
    for key, rs in sorted(groups.items()):
        for m in (
            "cer",
            "wer",
            "bag_f1",
            "lines_exact",
            "lookalike",
            "tokens",
            "find_hit",
            "panes_iou",
            "panes_hit",
        ):
            vals = [r[m] for r in rs if m in r]
            if vals:
                out[f"{key}.{m}"] = round(mean(vals), 4)
    out["all.lock_ok"] = round(mean(r["lock_ok"] for r in rows), 4)
    out["all.app_ok"] = round(mean(r["app_ok"] for r in rows), 4)
    finds = [r["find_hit"] for r in rows if "find_hit" in r]
    out["all.find_hit"] = round(mean(finds), 4) if finds else 0.0
    for m in ("panes_iou", "panes_hit"):
        vals = [r[m] for r in rows if m in r]
        out[f"all.{m}"] = round(mean(vals), 4) if vals else 0.0
    return out


def compare(now: dict[str, float], base: dict[str, float]) -> list[str]:
    worse = []
    for key, b in base.items():
        if key not in now:
            worse.append(f"{key}: missing (baseline {b})")
            continue
        n, lower = now[key], key.rsplit(".", 1)[1] in LOWER_IS_BETTER
        metric = key.rsplit(".", 1)[1]
        slack = TOLERANCE[metric] * max(abs(b), 1) if metric in TOLERANCE else RATE_TOLERANCE
        if metric == "lookalike":
            slack = 1.0
        if (n - b if lower else b - n) > slack:
            worse.append(f"{key}: {n} vs baseline {b}")
    return worse


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="the every-push set (default)")
    mode.add_argument("--full", action="store_true", help="the v0.x.0 set")
    p.add_argument("--check", action="store_true", help="exit 1 on a drop against the baseline")
    p.add_argument("--update", action="store_true", help="write the summary as the baseline")
    p.add_argument("--out", help="also write every case, with what was read, to this JSON file")
    p.add_argument("--show", help="print truth and reading for screens whose name has this")
    args = p.parse_args()
    name = "full" if args.full else "quick"
    configs = ["gdi+codec"] if name == "quick" else ["gdi+codec", "gdi", "pil"]

    ocr = Ocr()
    rows = []
    t0 = time.perf_counter()
    for screen in suite(name == "quick"):
        for config in configs:
            try:
                row = score(screen, config, ocr)
            except FontMissing as e:
                print(f"skip {screen.name} {config}: {e}", file=sys.stderr)
                continue
            rows.append(row)
            if args.show and args.show in screen.name:
                print(f"--- {screen.name} {config}")
                for t, g in zip(screen.truth, row["got"] + [""] * len(screen.truth), strict=False):
                    print(f"  {'==' if t == g else '!='} {t!r}\n     {g!r}")
    summary = summarize(rows)
    took = time.perf_counter() - t0

    print(f"jharokha {version()} evals, {name}: {len(rows)} reads in {took:.0f} s")
    print(
        f"  median ms: OCR {median(r['ms'] for r in rows):.0f}, "
        f"panes {median(r['panes_ms'] for r in rows):.1f} (timings are not gated)"
    )
    for key, v in summary.items():
        print(f"  {key:44} {v}")
    misses = [(r["screen"], r["config"], r["find_misses"]) for r in rows if "find_misses" in r]
    for m in misses:
        print(f"  find missed on {m[0]} {m[1]}: {m[2]}")
    for r in rows:
        if not r["lock_ok"]:
            print(f"  lock wrong on {r['screen']} {r['config']}")
        if not r["app_ok"]:
            print(f"  app wrong on {r['screen']} {r['config']}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"version": version(), "mode": name, "summary": summary, "cases": rows}, indent=1
            ),
            encoding="utf-8",
        )
    base_path = HERE / f"baseline-{name}.json"
    if args.update:
        base_path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {base_path}")
    if args.check:
        if not base_path.exists():
            sys.exit(f"no baseline at {base_path}; run with --update first")
        worse = compare(summary, json.loads(base_path.read_text(encoding="utf-8")))
        for w in worse:
            print(f"WORSE {w}")
        if worse:
            sys.exit(1)
        print("no drop against the baseline")


if __name__ == "__main__":
    main()
