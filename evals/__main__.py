"""Run the screen evals, print a summary, and compare it with the baseline.

    uv run python -m evals [--quick | --full] [--check] [--update] [--out FILE] [--show NAME]

Each generated screen is drawn (GDI with ClearType, then through an RDP-like codec; the
full run adds clean GDI and PIL), read through the real Windows OCR and jharokha's reading
order, and scored. A screen with a terminal grid is read as the server reads a grid region:
row by row (core.rows: the grid calibrated from the pixels, each inked row read by the
second engine). The older path, Windows OCR's words snapped to the grid (text.grid), is
scored too under `<config>+ocrgrid`, outside the all.* totals.

- recognition: CER, WER, bag-of-words F1, lines exact, lookalike misses, tokens per read
- find: the first match's centre falls inside the box of the text it should land on
- lock: read as locked exactly when it is a lock or sign-in screen
- identify: the built-in app definitions name the right app (app_ok) and view
  (identify_ok): none on a lock screen, generic where no app is drawn; and the mean
  confidence they give (identify_conf); a confusion table of the misses
- panes: for each pane the screen was drawn with, the best IoU of a pane found from the
  pixels (mean, and the share at 0.8 or more)
- readers: each named region whose items the screen knows (the Teams chat list and thread,
  the Outlook mail list, the calendar week, the browser's request table) is placed by the
  built-in definitions on the identified view, OCR'd as a region read is, and read by its
  reader. Lists: item precision and recall, and the share of fields exact (folding case
  and spaces) on the items matched. Tables: cells right (row, column and text) over the
  truth's and over what was read. Generated screens (`gen`) and the simulated session's
  apps (`sim`: Teams, Outlook mail and calendar, the browser's queue and status pages).

--check fails (exit 1) when a summary number is worse than evals/baseline-<mode>.json by
more than its tolerance; --update rewrites that file. Timings are printed, never gated."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from evals import items as truth_items
from evals import metrics
from evals.render import FontMissing, Screen, render
from evals.screens import suite
from jharokha import version
from jharokha.core import apps, readers, rows
from jharokha.core.apps import GENERIC, Catalog
from jharokha.core.identify import LOCKED, Identity, identify
from jharokha.core.models import Box, Frame, Line
from jharokha.core.panes import background, panes, reading_panes
from jharokha.core.text import find, format_concise, grid, looks_locked, reading_order
from jharokha.win.ocr import Ocr
from jharokha.win.recognize import Recognizer

HERE = Path(__file__).parent
LOWER_IS_BETTER = ("cer", "wer", "lookalike", "tokens")
TOLERANCE = {"tokens": 0.10}  # relative; everything else is a rate, absolute
RATE_TOLERANCE = 0.02
CALIBRATE_MS: list[float] = []
READER_METRICS = (
    "items_precision",
    "items_recall",
    "fields_exact",
    "cells_recall",
    "cells_precision",
)


def expected(screen: Screen) -> tuple[str | None, str | None]:
    """The app and view a screen should be identified as: none on a lock screen, generic
    where no built-in app is drawn."""
    if screen.locked:
        return None, None
    if screen.view is None:
        return GENERIC, "screen"
    return screen.app, screen.view


@dataclasses.dataclass
class Read:
    """One screen drawn and read: what every axis scores from."""

    frame: Frame
    boxes: list[Box]
    ordered: list[Line]
    found: list[Box]
    who: Identity
    ms: float
    panes_ms: float
    identify_ms: float


def score(
    screen: Screen, config: str, ocr: Ocr, catalog: Catalog, recognizer: Recognizer
) -> list[dict[str, Any]]:
    """The screen's rows: its scores (two for a screen with a grid, see the module
    docstring) and one per region on the readers axis. A simulated session's screen is
    scored on the readers axis only."""
    engine, _, codec = config.partition("+")
    frame, boxes = render(screen, engine, codec == "codec")
    t = time.perf_counter()
    raw = ocr.recognize(frame)
    ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    found = panes(frame)
    panes_ms = (time.perf_counter() - t) * 1000
    ordered = reading_order(raw, reading_panes(frame))
    t = time.perf_counter()
    who = (
        LOCKED
        if looks_locked(ordered)
        else identify(
            catalog.apps.values(), ordered, found, background(frame), frame.width, frame.height
        )
    )
    identify_ms = (time.perf_counter() - t) * 1000
    r = Read(frame, boxes, ordered, found, who, ms, panes_ms, identify_ms)
    out = []
    if not screen.name.startswith("sim-"):
        if screen.grid:
            t = time.perf_counter()
            lines, n = by_rows(frame, ordered, screen.grid, recognizer)
            rows_ms = (time.perf_counter() - t) * 1000
            row = _scores(screen, config, lines, r)
            row["rows_ms"] = round(rows_ms)
            row["rows_read"] = n
            out.append(row)
            old = _scores(screen, f"{config}+ocrgrid", grid(ordered, screen.grid), r)
            old["path"] = "ocrgrid"
            out.append(old)
        else:
            out.append(_scores(screen, config, ordered, r))
    out += score_readers(screen, config, r, ocr, catalog)
    return out


def by_rows(
    frame: Frame, ordered: list[Line], box: Box, recognizer: Recognizer
) -> tuple[list[Line], int]:
    """The screen with its grid read row by row: calibrated from this frame, every inked row
    read by the second engine, wrapped rows joined and blank ones left out (the truth has
    none), in place of the Windows OCR lines inside the grid. And how many rows were read."""
    t = time.perf_counter()
    g = rows.calibrate(frame, ordered, box)
    CALIBRATE_MS.append((time.perf_counter() - t) * 1000)
    if g is None:
        return grid(ordered, box), 0
    n = 0

    def reader(bs: list[Box]) -> list[str]:
        nonlocal n
        n += len(bs)
        return recognizer.read(frame, bs, margin=0)

    read = rows.read_rows(frame, box, reader, g, ordered)
    assert read is not None
    got = [ln for ln in rows.unwrap(read.lines, g) if ln.text]
    x0, y0, bw, bh = box
    inside = [
        x0 <= ln.box[0] + ln.box[2] / 2 < x0 + bw and y0 <= ln.box[1] + ln.box[3] / 2 < y0 + bh
        for ln in ordered
    ]
    first = inside.index(True) if True in inside else len(ordered)
    rest = [ln for ln, i in zip(ordered, inside, strict=True) if not i]
    before = sum(1 for i in inside[:first] if not i)
    return rest[:before] + got + rest[before:], n


def _scores(screen: Screen, config: str, lines: list[Line], r: Read) -> dict[str, Any]:
    got = [ln.text.rstrip() for ln in lines]
    truth = screen.truth
    want = expected(screen)
    who = r.who
    row: dict[str, Any] = {
        "screen": screen.name,
        "kind": screen.name.split("-")[0],
        "config": config,
        "ms": round(r.ms),
        "panes_ms": round(r.panes_ms, 1),
        "identify_ms": round(r.identify_ms, 1),
        "cer": metrics.cer(truth, got),
        "wer": metrics.wer(truth, got),
        "bag_f1": metrics.bag_f1(truth, got),
        "lines_exact": metrics.lines_exact(truth, got),
        "lookalike": metrics.lookalike_misses(truth, got),
        "tokens": len(format_concise(lines)) // 4,
        "lock_ok": looks_locked(lines) == screen.locked,
        "app_ok": who.app == want[0],
        "identify_ok": (who.app, who.view) == want,
        "identified": f"{who.app}.{who.view}",
    }
    if not screen.locked:
        row["identify_conf"] = who.confidence
    hits = []
    for f, (x, y, w, h) in zip(screen.finds, r.boxes, strict=True):
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
        best = [max(metrics.iou(p, f) for f in r.found) for p in screen.panes]
        row["panes_iou"] = mean(best)
        row["panes_hit"] = mean(b >= 0.8 for b in best)
    row["got"] = got
    return row


def score_readers(
    screen: Screen, config: str, r: Read, ocr: Ocr, catalog: Catalog
) -> list[dict[str, Any]]:
    """One row per region the screen knows the items of: placed on the identified view,
    OCR'd as jharokha_read_text reads a region, read by its reader."""
    out = []
    f, who = r.frame, r.who
    for name, truth in screen.items.items():
        app, _, key = name.partition(".")
        row: dict[str, Any] = {
            "axis": "readers",
            "source": "sim" if screen.name.startswith("sim-") else "gen",
            "screen": screen.name,
            "region": name,
            "config": config,
        }
        out.append(row)
        placed, view = None, who.view or ""
        if who.app == app and who.view and key in catalog.apps[app].views[who.view].regions:
            placed = catalog.region_box(app, view, key, r.found, who.found, f.width, f.height)
        if placed is None:
            row.update(dict.fromkeys(_metrics(truth["kind"]), 0.0), error="region not placed")
            continue
        box = placed[0]
        reg = catalog.apps[app].views[view].regions[key]
        inner = catalog.nested(app, view, key, r.found, who.found, f.width, f.height)
        small = box[2] * box[3] * 4 <= f.width * f.height
        raw = ocr.recognize(f, box, upscale=2.0 if small else 1.0)
        lines = reading_order(raw, reading_panes(f, box))
        got = readers.as_dicts(readers.items(reg.reader, lines, box, reg.kind, inner))
        if truth["kind"] == "table":
            t = next((it["table"] for it in got if "table" in it), None)
            row.update(truth_items.score_table(truth, t))
        else:
            row.update(truth_items.score_list(truth["kind"], truth["items"], got))
        row["got"] = got
    return out


def _metrics(kind: str) -> tuple[str, ...]:
    return READER_METRICS[3:] if kind == "table" else READER_METRICS[:3]


def sim_screens(seeds: list[int]) -> list[Screen]:
    """The simulated session's apps whose regions know their items: Teams chat, Outlook mail
    and calendar (loaded), the browser's request queue and status page."""
    from evals.sim.desktop import Desktop

    out = []
    for seed in seeds:
        now = [1000.0]
        d = Desktop(seed, front="teams", clock=lambda now=now: now[0])

        def shot(label: str, d: Desktop = d, seed: int = seed) -> None:
            out.append(dataclasses.replace(d.screen(), name=f"sim-{label}-{seed}"))

        shot("teams")
        d.switch("outlook")
        shot("outlook")
        d.outlook.go("calendar")
        now[0] += 2  # the grid arrives a second after the switch
        shot("calendar")
        d.switch("browser")
        shot("browser")
        d.browser.navigate("https://status.example.net/")
        shot("status")
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    reader_rows = [r for r in rows if r.get("axis") == "readers"]
    rows = [r for r in rows if r.get("axis") != "readers"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[f"{r['kind']}.{r['config']}"].append(r)
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
            "identify_ok",
            "identify_conf",
        ):
            vals = [r[m] for r in rs if m in r]
            if vals:
                out[f"{key}.{m}"] = round(mean(vals), 4)
    # The older grid path is a second look at screens already counted; all.* stays the
    # path the server takes.
    rows = [r for r in rows if r.get("path") != "ocrgrid"]
    out["all.lock_ok"] = round(mean(r["lock_ok"] for r in rows), 4)
    out["all.app_ok"] = round(mean(r["app_ok"] for r in rows), 4)
    out["all.identify_ok"] = round(mean(r["identify_ok"] for r in rows), 4)
    conf = [r["identify_conf"] for r in rows if "identify_conf" in r]
    out["all.identify_conf"] = round(mean(conf), 4) if conf else 0.0
    finds = [r["find_hit"] for r in rows if "find_hit" in r]
    out["all.find_hit"] = round(mean(finds), 4) if finds else 0.0
    for m in ("panes_iou", "panes_hit"):
        vals = [r[m] for r in rows if m in r]
        out[f"all.{m}"] = round(mean(vals), 4) if vals else 0.0
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reader_rows:
        by[f"readers.{r['source']}.{r['region']}.{r['config']}"].append(r)
    for key, rs in sorted(by.items()):
        for m in READER_METRICS:
            vals = [r[m] for r in rs if m in r]
            if vals:
                out[f"{key}.{m}"] = round(mean(vals), 4)
    for m in READER_METRICS:
        vals = [r[m] for r in reader_rows if m in r]
        if vals:
            out[f"readers.all.{m}"] = round(mean(vals), 4)
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
    recognizer = Recognizer()
    catalog = apps.catalog()  # the built-in definitions only, as on a fresh install
    cases = []
    t0 = time.perf_counter()
    seeds = [100] if name == "quick" else [100, 101, 102]
    for screen in suite(name == "quick") + sim_screens(seeds):
        for config in configs:
            try:
                scored = score(screen, config, ocr, catalog, recognizer)
            except FontMissing as e:
                print(f"skip {screen.name} {config}: {e}", file=sys.stderr)
                continue
            cases.extend(scored)
            if args.show and args.show in screen.name:
                for row in scored:
                    if row.get("axis") == "readers":
                        print(f"--- {screen.name} {row['config']} {row['region']}")
                        for it in row.get("got", []):
                            print(f"     {json.dumps(it)[:160]}")
                        continue
                    print(f"--- {screen.name} {row['config']}")
                    got = row["got"] + [""] * len(screen.truth)
                    for t, g in zip(screen.truth, got, strict=False):
                        print(f"  {'==' if t == g else '!='} {t!r}\n     {g!r}")
    summary = summarize(cases)
    took = time.perf_counter() - t0
    main_rows = [r for r in cases if r.get("axis") != "readers"]

    print(f"jharokha {version()} evals, {name}: {len(main_rows)} reads in {took:.0f} s")
    print(
        f"  median ms: OCR {median(r['ms'] for r in main_rows):.0f}, "
        f"panes {median(r['panes_ms'] for r in main_rows):.1f}, "
        f"identify {median(r['identify_ms'] for r in main_rows):.1f} (timings are not gated)"
    )
    by_row = [r for r in main_rows if "rows_ms" in r]
    if by_row:
        each = sum(r["rows_ms"] for r in by_row) / max(1, sum(r["rows_read"] for r in by_row))
        print(
            f"  row reads: median {median(r['rows_ms'] for r in by_row):.0f} ms a screen "
            f"(calibrate and every inked row), max {max(r['rows_ms'] for r in by_row):.0f}, "
            f"{each:.0f} ms a row read; calibrating {median(CALIBRATE_MS):.0f} ms of it"
        )
    for key, v in summary.items():
        print(f"  {key:52} {v}")
    misses = [(r["screen"], r["config"], r["find_misses"]) for r in main_rows if "find_misses" in r]
    for m in misses:
        print(f"  find missed on {m[0]} {m[1]}: {m[2]}")
    for r in cases:
        if r.get("axis") != "readers":
            continue
        for k in ("error", "item_misses", "cell_misses"):
            if k in r:
                print(f"  {r['region']} on {r['screen']} {r['config']} {k}: {r[k]}")
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for r in main_rows:
        if not r["lock_ok"]:
            print(f"  lock wrong on {r['screen']} {r['config']}")
        if not r["identify_ok"]:
            print(f"  identified {r['screen']} {r['config']} as {r['identified']}")
            confusion[(r["kind"], r["identified"])] += 1
    if confusion:
        print("  confusion (screen kind -> identified as: count):")
        for (kind, got), n in sorted(confusion.items()):
            print(f"    {kind} -> {got}: {n}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"version": version(), "mode": name, "summary": summary, "cases": cases},
                indent=1,
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
