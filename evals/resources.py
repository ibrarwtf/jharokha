"""What jharokha costs the machine it runs on: memory, and time and CPU per tool call.

    uv run python -m evals.resources [--repeats 20] [--idle-s 30] [--threads]
                                     [--out FILE] [--check evals/budget.json] [--profile ci]

No window: an in-process server over EvalSource (generated Teams-like screens, real Windows
OCR and second engine), called through an MCP client as an agent would. Every call but
read_cached and the verify step gets a changed screen first, so it pays for OCR as a fresh
capture would: a new screen, except for read_region, where only the region read changed
(new messages in the thread; read_region_new_screen is the same read on a new screen).
Capture itself (PrintWindow, ~40 ms live) is not in these numbers.

- rss_*: the process's working set, MB. idle is after imports and connecting, before any
  OCR; first_read after the first read (Windows OCR engine created); second_engine after
  the first verify (model loaded); peak is the most it held at any point.
- <op>.wall_*: wall time of the call, median and p95 over --repeats.
- <op>.cpu_mean_ms: CPU time of the whole process over the call (user + kernel, every
  thread: Windows OCR's, onnxruntime's, and the in-process MCP client's), so above wall time
  when threads run in parallel. Windows counts it in 15.6 ms ticks, so one call's figure is
  coarse and only the mean over the repeats is reported.
- verify.*_per_line: a 20-line verify=all on an already read screen, per line;
  verify.cpu_ms_in_1s_after: CPU in the second after it returns, onnxruntime's threads
  spin-waiting for more work.
- idle.cpu_pct: CPU time over wall time for --idle-s between calls, as a percentage of
  one core (0.20 measured its background watcher here instead).

--threads also times the second engine with onnxruntime's intra-op thread count at its
default (one per physical core), 1 and 2, with and without spin-waiting between runs.

--check exits 1 when a metric is over its budget in the file's --profile section."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import platform
import re
import sys
import time
from collections.abc import Awaitable, Callable
from ctypes import wintypes
from pathlib import Path
from statistics import mean, median
from typing import Any

from mcp import Client
from mcp.types import TextContent

from evals.screens import teams
from evals.source import EvalSource
from jharokha import version
from jharokha.core.models import Box
from jharokha.server import build_server

SEEDS = list(range(100, 106))
REGION = [456, 150, 700, 440]  # the chat thread: under a quarter of the frame, so zoomed
REGION_BOX: Box = (456, 150, 700, 440)
VERIFY_LINES = 20


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("rest", ctypes.c_size_t * 6),
    ]


def _k32() -> Any:
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return k


def rss_mb() -> tuple[float, float]:
    """Working set now and at its peak, MB."""
    k, pmc = _k32(), _PMC(cb=ctypes.sizeof(_PMC))
    k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 2**20, pmc.PeakWorkingSetSize / 2**20


def cpu_s() -> float:
    """CPU seconds of every thread of this process so far. time.process_time counts the same
    on Windows, but this is the call the numbers are documented against."""
    k = _k32()
    t = [wintypes.FILETIME() for _ in range(4)]
    k.GetProcessTimes(k.GetCurrentProcess(), *(ctypes.byref(f) for f in t))

    def s(f: wintypes.FILETIME) -> float:
        return ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7

    return s(t[2]) + s(t[3])


def percentile(xs: list[float], p: float) -> float:
    """Nearest rank: with 20 samples p95 is the 19th, not an interpolation past the data."""
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, math.ceil(len(s) * p / 100) - 1))] if s else 0.0


def check(metrics: dict[str, float], budget: dict[str, float]) -> list[str]:
    """Each budget key whose metric is over it or missing."""
    over = []
    for key, limit in budget.items():
        if key not in metrics:
            over.append(f"{key}: missing (budget {limit})")
        elif metrics[key] > limit:
            over.append(f"{key}: {metrics[key]:g} over budget {limit:g}")
    return over


def _text(r: Any) -> str:
    c = r.content[0]
    assert isinstance(c, TextContent)
    if r.is_error:
        raise RuntimeError(c.text)
    return c.text


class Meter:
    """Time and CPU of one awaited call, and the samples kept per operation."""

    def __init__(self) -> None:
        self.samples: dict[str, tuple[list[float], list[float]]] = {}

    async def __call__(self, op: str | None, call: Awaitable[Any]) -> tuple[Any, float, float]:
        c0, t0 = cpu_s(), time.perf_counter()
        out = await call
        wall, cpu = (time.perf_counter() - t0) * 1000, (cpu_s() - c0) * 1000
        if op:
            w, c = self.samples.setdefault(op, ([], []))
            w.append(wall)
            c.append(cpu)
        return out, wall, cpu

    def summary(self) -> dict[str, float]:
        out = {}
        for op, (w, c) in self.samples.items():
            out[f"{op}.wall_p50_ms"] = median(w)
            out[f"{op}.wall_p95_ms"] = percentile(w, 95)
            out[f"{op}.cpu_mean_ms"] = mean(c)
        return out


async def measure(repeats: int, idle_s: float) -> tuple[dict[str, float], EvalSource]:
    m: dict[str, float] = {}
    source = EvalSource(teams, SEEDS)
    meter = Meter()
    async with Client(build_server(source)) as c:

        def call(name: str, **args: Any) -> Awaitable[Any]:
            return c.call_tool(name, args)

        m["rss_idle_mb"] = rss_mb()[0]
        r, wall, _ = await meter(None, call("jharokha_read", region="screen"))
        _text(r)
        m["first_read_ms"] = wall
        m["rss_first_read_mb"] = rss_mb()[0]
        r, wall, _ = await meter(
            None, call("jharokha_read", region="screen", verify="all", limit=20)
        )
        _text(r)
        m["first_verify_ms"] = wall
        m["rss_second_engine_mb"] = rss_mb()[0]

        # Each op after the screen changed: all of it (a new screen), or only the region
        # read (new messages in the thread, the rest of the app as it was).
        fresh: list[tuple[str, Box | None, Callable[[], Awaitable[Any]]]] = [
            ("read_full", None, lambda: call("jharokha_read", region="screen")),
            ("read_focus", None, lambda: call("jharokha_read")),
            ("read_region", REGION_BOX, lambda: call("jharokha_read", region=REGION)),
            ("read_region_new_screen", None, lambda: call("jharokha_read", region=REGION)),
            ("find", None, lambda: call("jharokha_find", query=source.screen.finds[0].query)),
            ("panes", None, lambda: call("jharokha_read", format="panes")),
            ("wait_hit", None, lambda: call("jharokha_wait", text=source.screen.finds[-1].query)),
        ]
        per_line: tuple[list[float], list[float]] = ([], [])
        tail: list[float] = []
        for _ in range(repeats):
            for op, changed, fn in fresh:
                source.advance(changed)
                _text((await meter(op, fn()))[0])
            _text((await meter("read_cached", call("jharokha_read", region="screen")))[0])
            r, wall, cpu = await meter(
                "verify_20",
                call("jharokha_read", region="screen", verify="all", limit=VERIFY_LINES),
            )
            n = int(re.search(r"verified=(\d+)", _text(r)).group(1))  # type: ignore[union-attr]
            per_line[0].append(wall / n)
            per_line[1].append(cpu / n)
            # onnxruntime's threads spin on after the call returns; that CPU is verify's,
            # and would otherwise land on the next call.
            c0 = cpu_s()
            await asyncio.sleep(1)
            tail.append((cpu_s() - c0) * 1000)
        m.update(meter.summary())
        m["verify.wall_ms_per_line"] = median(per_line[0])
        m["verify.cpu_ms_per_line"] = mean(per_line[1])
        m["verify.cpu_ms_in_1s_after"] = mean(tail)

        # Between calls the server does nothing: no thread polls the screen (0.20's watcher
        # did, at about 1% of a core).
        c0, t0 = cpu_s(), time.perf_counter()
        await asyncio.sleep(idle_s)
        m["idle.cpu_pct"] = (cpu_s() - c0) / (time.perf_counter() - t0) * 100
    m["rss_end_mb"], m["rss_peak_mb"] = rss_mb()
    return {k: round(v, 2) for k, v in m.items()}, source


THREADS = [
    ("default", 0, True),
    ("default, no spin", 0, False),
    ("1", 1, True),
    ("2", 2, True),
    ("2, no spin", 2, False),
]


def thread_counts(source: EvalSource, repeats: int) -> list[dict[str, Any]]:
    """The second engine on the same 20 line boxes, per intra-op thread setting."""
    import onnxruntime as ort

    from jharokha.win.recognize import MODEL

    frame = source.frame(source.window(None))  # type: ignore[arg-type]
    boxes = [ln.box for ln in source.ocr(frame, None)][:VERIFY_LINES]
    rec = source._recognizer
    assert rec is not None, "measure() loads the model first"
    rows = []
    for name, n, spin in THREADS:
        opts = ort.SessionOptions()
        opts.log_severity_level = 4
        opts.enable_cpu_mem_arena = False
        opts.intra_op_num_threads = n
        if not spin:
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        rec._session = ort.InferenceSession(
            str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        rec.read(frame, boxes)  # warm
        walls, cpus = [], []
        for _ in range(repeats):
            c0, t0 = cpu_s(), time.perf_counter()
            rec.read(frame, boxes)
            walls.append((time.perf_counter() - t0) * 1000 / len(boxes))
            cpus.append((cpu_s() - c0) * 1000 / len(boxes))
        # Spinning threads keep a core busy after the last run returns.
        c0 = cpu_s()
        time.sleep(1)
        after = (cpu_s() - c0) * 1000
        rows.append(
            {
                "threads": name,
                "wall_ms_per_line": round(median(walls), 1),
                "wall_p95_ms_per_line": round(percentile(walls, 95), 1),
                "cpu_ms_per_line": round(mean(cpus), 1),
                "cpu_ms_in_1s_after": round(after),
            }
        )
    return rows


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals.resources")
    p.add_argument("--repeats", type=int, default=20)
    p.add_argument("--idle-s", type=float, default=30)
    p.add_argument("--threads", action="store_true", help="also time onnxruntime thread counts")
    p.add_argument("--out", help="write the numbers to this JSON file")
    p.add_argument("--check", help="budget file: exit 1 when a number is over its budget")
    p.add_argument("--profile", default="laptop", help="the budget file's section to check")
    args = p.parse_args()

    t0 = time.perf_counter()
    metrics, source = asyncio.run(measure(args.repeats, args.idle_s))
    threads = thread_counts(source, args.repeats) if args.threads else []
    machine = {"cpu": platform.processor(), "logical_cpus": os.cpu_count()}
    print(
        f"jharokha {version()} resources: {args.repeats} repeats, "
        f"{time.perf_counter() - t0:.0f} s, {machine['cpu']}"
    )
    for key, v in metrics.items():
        print(f"  {key:30} {v:10.2f}")
    if threads:
        print("  second engine per line, by onnxruntime intra-op threads:")
        for row in threads:
            print(
                f"    {row['threads']:17} wall {row['wall_ms_per_line']:6.1f} ms "
                f"(p95 {row['wall_p95_ms_per_line']:5.1f})  cpu {row['cpu_ms_per_line']:6.1f} ms"
                f"  cpu in the 1 s after {row['cpu_ms_in_1s_after']:4} ms"
            )
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "version": version(),
                    "machine": machine,
                    "repeats": args.repeats,
                    "idle_s": args.idle_s,
                    "metrics": metrics,
                    "threads": threads,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    if args.check:
        budgets = json.loads(Path(args.check).read_text(encoding="utf-8"))
        if args.profile not in budgets:
            sys.exit(f"no profile {args.profile!r} in {args.check}")
        over = check(metrics, budgets[args.profile]["max"])
        for o in over:
            print(f"OVER {o}")
        if over:
            sys.exit(1)
        print(f"within the {args.profile} budget")


if __name__ == "__main__":
    main()
