"""`jharokha` runs the MCP server on stdio; in a shell, `jharokha status|read|find|screenshot|
bench|measure`, and with --allow-input `click|scroll|type|press`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from jharokha.core.layout import Layout, LayoutError, load
from jharokha.core.models import Point
from jharokha.core.screen import Screen, ScreenError, check_region, png
from jharokha.core.text import find, format_concise


def _source():  # noqa: ANN202
    if sys.platform != "win32":
        sys.exit("not_windows: jharokha reads Windows remote-desktop windows and needs Windows.")
    from jharokha.win.source import WindowsSource

    return WindowsSource()


def main() -> None:
    p = argparse.ArgumentParser(prog="jharokha", description=__doc__)
    p.add_argument("--window-title", help="substring of the window title to pick")
    p.add_argument(
        "--allow-input",
        action="store_true",
        help="also serve (or, in a shell, allow) scroll, click, type and press",
    )
    p.add_argument(
        "--layouts",
        help="TOML file of named regions (default: $JHAROKHA_LAYOUTS, else ./layouts.toml)",
    )
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    r = sub.add_parser("read")
    r.add_argument("--region", type=_ints(4), help="x,y,w,h in client pixels")
    r.add_argument("--limit", type=int, default=200, help="most lines to print")
    sub.add_parser("find").add_argument("query")
    s = sub.add_parser("screenshot", help="write a PNG of the window or a region")
    s.add_argument("out", help="PNG file to write")
    s.add_argument("--region", type=_ints(4), help="x,y,w,h in client pixels")
    s.add_argument("--max-long-edge", type=int, default=1568)
    after = argparse.ArgumentParser(add_help=False)
    after.add_argument(
        "--until", help="afterwards, wait until this text is on screen (up to --timeout)"
    )
    after.add_argument("--timeout", type=float, default=10, help="seconds for --until")
    after.add_argument("--quiet", action="store_true", help="do not read the screen afterwards")
    c = sub.add_parser("click", parents=[after], help="click at a client point")
    c.add_argument("x", type=int)
    c.add_argument("y", type=int)
    c.add_argument("--button", choices=["left", "right", "middle"], default="left")
    c.add_argument("--double", action="store_true")
    sc = sub.add_parser("scroll", parents=[after], help="turn the wheel; ticks < 0 scrolls down")
    sc.add_argument("ticks", type=int, help="ticks; negative scrolls down")
    sc.add_argument("--at", type=_ints(2), help="x,y to scroll at (default: the centre)")
    focus = argparse.ArgumentParser(add_help=False)
    focus.add_argument(
        "--focus-at",
        type=_ints(2),
        help="x,y of a blank point to click first if the session will not come to the front",
    )
    t = sub.add_parser("type", parents=[after, focus], help="type text; newlines are Enter")
    t.add_argument("text", nargs="?", help="text to type (or --stdin)")
    t.add_argument(
        "--stdin", action="store_true", help="read the text from stdin, less one final newline"
    )
    t.add_argument("--enter", action="store_true", help="press Enter after the text")
    t.add_argument(
        "--paste", action="store_true", help="send through the clipboard and Ctrl+V, not keys"
    )
    k = sub.add_parser("press", parents=[after, focus], help="a key or chord, e.g. ctrl+c")
    k.add_argument("keys")
    sub.add_parser("bench")
    m = sub.add_parser(
        "measure",
        help="write an app's named regions to the layouts file from what is on screen",
    )
    m.add_argument("app", help="layout name, e.g. teams")
    m.add_argument("--anchor", required=True, help="text always on screen in that app")
    m.add_argument(
        "--zoom",
        help="also measure zoom steps LOW..HIGH, e.g. --zoom=-5..2: presses Ctrl+0, Ctrl+= "
        "and Ctrl+- in the session, then restores the zoom",
    )
    m.add_argument("--names", help="comma-separated names for the regions found, in order")
    args = p.parse_args()
    source = _source()
    if args.cmd is None:
        from jharokha.server import build_server

        layouts = _layouts(args.layouts)
        build_server(source, allow_input=args.allow_input, layouts=layouts).run("stdio")
        return
    screen = Screen(source)
    try:
        if args.cmd == "status":
            print(json.dumps(screen.window(args.window_title).__dict__))
        elif args.cmd == "read":
            _, lines = screen.read(args.window_title, args.region)
            print(format_concise(lines, limit=args.limit))
        elif args.cmd == "screenshot":
            _, frame = screen.frame(args.window_title)
            data, box, scale = png(frame, args.region, args.max_long_edge)
            Path(args.out).write_bytes(data)
            print(json.dumps({"out": args.out, "region": list(box), "scale": scale}))
        elif args.cmd in ("click", "scroll", "type", "press"):
            if not args.allow_input:
                sys.exit(f"input_disabled: '{args.cmd}' sends real input; add --allow-input.")
            act(screen, args)
        elif args.cmd == "find":
            _, lines = screen.read(args.window_title, None)
            print(json.dumps([m.__dict__ for m in find(lines, args.query, all=True)]))
        elif args.cmd == "bench":
            bench(screen, args.window_title)
        elif args.cmd == "measure":
            from jharokha import measure

            path = Path(args.layouts or os.environ.get("JHAROKHA_LAYOUTS") or "layouts.toml")
            measure.run(
                screen, args.window_title, args.app, args.anchor, path, args.zoom, args.names
            )
    except ScreenError as e:
        sys.exit(str(e))


def _ints(n: int):  # noqa: ANN202
    def parse(v: str) -> tuple[int, ...]:
        parts = v.split(",")
        if len(parts) != n:
            raise argparse.ArgumentTypeError(f"expected {n} comma-separated integers, got {v!r}")
        return tuple(int(x) for x in parts)

    return parse


def act(screen: Screen, args: argparse.Namespace) -> None:
    """One input action, then (unless --quiet) the screen text after it, as the server does."""
    w, frame = screen.frame(args.window_title)
    point: Point | None = getattr(args, "focus_at", None)
    if point:
        check_region((*point, 1, 1), frame)
    if args.cmd == "click":
        check_region((args.x, args.y, 1, 1), frame)
        screen.source.click(w, args.x, args.y, args.button, args.double)
    elif args.cmd == "scroll":
        x, y = args.at or (frame.width // 2, frame.height // 2)
        check_region((x, y, 1, 1), frame)
        screen.source.scroll(w, x, y, args.ticks)
    elif args.cmd == "type":
        # a heredoc ends in a newline of its own; Enter is --enter's job
        text = sys.stdin.read().removesuffix("\n") if args.stdin else args.text
        if text is None:
            sys.exit("type: give the text or --stdin")
        screen.source.type_text(w, text + ("\n" if args.enter else ""), point, args.paste)
    else:
        screen.source.press(w, args.keys, point)
    screen.settle(args.window_title)
    start = time.monotonic()
    while args.until:
        _, lines = screen.read(args.window_title, None)
        if find(lines, args.until):
            break
        if time.monotonic() - start >= args.timeout:
            sys.exit(f"timeout: the input was sent, but '{args.until}' did not appear")
        time.sleep(0.5)
    if not args.quiet:
        _, lines = screen.read(args.window_title, None)
        print(format_concise(lines))


def _layouts(flag: str | None) -> dict[str, Layout]:
    given = flag or os.environ.get("JHAROKHA_LAYOUTS")
    path = Path(given or "layouts.toml")
    if not given and not path.exists():
        return {}
    try:
        return load(path)
    except (OSError, LayoutError) as e:
        sys.exit(f"layouts: {e}")


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
