"""`jharokha` runs the MCP server on stdio; `jharokha status|read|find|bench` for a terminal."""

from __future__ import annotations

import argparse
import json
import sys
import time

from jharokha.core.screen import Screen, ScreenError
from jharokha.core.text import find, format_concise


def _source():  # noqa: ANN202
    if sys.platform != "win32":
        sys.exit("not_windows: jharokha reads Windows remote-desktop windows and needs Windows.")
    from jharokha.win.source import WindowsSource

    return WindowsSource()


def main() -> None:
    p = argparse.ArgumentParser(prog="jharokha", description=__doc__)
    p.add_argument("--window-title", help="substring of the window title to pick")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("read")
    sub.add_parser("find").add_argument("query")
    sub.add_parser("bench")
    args = p.parse_args()
    source = _source()
    if args.cmd is None:
        from jharokha.server import build_server

        build_server(source).run("stdio")
        return
    screen = Screen(source)
    try:
        if args.cmd == "status":
            print(json.dumps(screen.window(args.window_title).__dict__))
        elif args.cmd == "read":
            _, lines = screen.read(args.window_title, None)
            print(format_concise(lines))
        elif args.cmd == "find":
            _, lines = screen.read(args.window_title, None)
            print(json.dumps([m.__dict__ for m in find(lines, args.query, all=True)]))
        elif args.cmd == "bench":
            bench(screen, args.window_title)
    except ScreenError as e:
        sys.exit(str(e))


def bench(screen: Screen, window_title: str | None) -> None:
    w = screen.window(window_title)
    t = time.perf_counter()
    frame = screen.source.frame(w)
    t_cap = time.perf_counter() - t
    screen.source.ocr(frame, None)  # warm the engine
    t = time.perf_counter()
    lines = screen.source.ocr(frame, None)
    t_ocr = time.perf_counter() - t
    t = time.perf_counter()
    out = format_concise(lines)
    t_fmt = time.perf_counter() - t
    t = time.perf_counter()
    screen.read(window_title, None)
    screen.read(window_title, None)
    t_cached = time.perf_counter() - t
    rss = _peak_rss_mb()
    print(f"window        {w.client_size[0]}x{w.client_size[1]} @ {w.dpi_scale:g}x dpi")
    print(f"capture       {t_cap * 1000:6.0f} ms")
    print(f"ocr           {t_ocr * 1000:6.0f} ms   {len(lines)} lines")
    print(f"format        {t_fmt * 1000:6.0f} ms   ~{len(out) // 4} tokens concise")
    print(f"cached read   {t_cached * 1000 / 2:6.0f} ms")
    print(f"peak rss      {rss:6.0f} MB")


def _peak_rss_mb() -> float:
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("rest", ctypes.c_size_t * 6),
        ]

    pmc = PMC(cb=ctypes.sizeof(PMC))
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.PeakWorkingSetSize / 2**20


if __name__ == "__main__":
    main()
