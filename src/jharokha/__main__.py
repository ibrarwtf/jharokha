"""`jharokha` runs the MCP server on stdio; in a shell, `jharokha status|read|find|screenshot|
bench|measure`, and with --allow-input `click|scroll|type|press`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from jharokha import version
from jharokha.core import actions, apps, guard
from jharokha.core.apps import Catalog
from jharokha.core.guard import Policy
from jharokha.core.identify import identify
from jharokha.core.layout import LayoutError, load
from jharokha.core.models import Point
from jharokha.core.panes import background, panes
from jharokha.core.screen import Screen, ScreenError, check_region, png
from jharokha.core.text import find, format_concise, looks_locked
from jharokha.core.trace import Trace

# In a shell the user's own Enter started the command: input waits this long for the
# keyboard and mouse to go idle (3 s) before it is refused as user_active.
WAIT_IDLE_S = 10.0


def _source():  # noqa: ANN202
    if sys.platform != "win32":
        sys.exit("not_windows: jharokha reads Windows remote-desktop windows and needs Windows.")
    from jharokha.win.source import WindowsSource

    return WindowsSource()


def main() -> None:
    p = argparse.ArgumentParser(prog="jharokha", description=__doc__)
    p.add_argument("--version", action="version", version=f"jharokha {version()}")
    p.add_argument("--window-title", help="substring of the window title to pick")
    p.add_argument(
        "--allow-input",
        action="store_true",
        help="also serve (or, in a shell, allow) scroll, click, type and press",
    )
    p.add_argument(
        "--apps",
        help="app definitions and calibration (default: $JHAROKHA_APPS, else ./apps.toml)",
    )
    p.add_argument(
        "--layouts",
        help="old layouts file, read as calibration (default: $JHAROKHA_LAYOUTS, "
        "else ./layouts.toml)",
    )
    p.add_argument(
        "--trace",
        metavar="PATH",
        help="append a JSON line per input action (sent or refused) to PATH; off by default",
    )
    p.add_argument(
        "--trace-text",
        action="store_true",
        help="with --trace, record typed text itself (default: its length and SHA-256)",
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
    after.add_argument("--until-regex", action="store_true", help="--until is a regex")
    after.add_argument("--timeout", type=float, default=10, help="seconds for --until")
    after.add_argument("--quiet", action="store_true", help="do not read the screen afterwards")
    after.add_argument(
        "--expect",
        help="send nothing unless this app (or app.view) is in front, e.g. terminal",
    )
    c = sub.add_parser("click", parents=[after], help="click at a client point")
    c.add_argument("point", nargs="+", help="X Y, or X,Y as --focus-at takes it")
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
    focus.add_argument(
        "--allow",
        choices=["irreversible"],
        help="send a key the app's policy calls irreversible (Enter in Teams or a browser)",
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
        help="write an app view's calibration to apps.toml from what is on screen",
    )
    m.add_argument("app", help="app id, e.g. teams; a new id starts an app of your own")
    m.add_argument("--view", help="the app's view (default: its first, e.g. teams chat)")
    m.add_argument("--anchor", required=True, help="text always on screen in that app")
    m.add_argument(
        "--zoom",
        help="also measure zoom steps LOW..HIGH, e.g. --zoom=-5..2: presses Ctrl+0, Ctrl+= "
        "and Ctrl+- in the session, then restores the zoom",
    )
    m.add_argument("--names", help="comma-separated names for the regions found, in order")
    m.add_argument(
        "--focus", help="the region input tools report changes in, e.g. thread (default: kept)"
    )
    m.add_argument(
        "--grid", help="a region that is a terminal's character grid, e.g. body (default: kept)"
    )
    args = p.parse_args()
    source = _source()
    trace = Trace(args.trace, include_text=args.trace_text)
    if args.cmd is None:
        from jharokha.server import build_server

        catalog = _catalog(args.apps, args.layouts)
        acts = _actions(args.apps)
        build_server(source, args.allow_input, catalog, trace, acts).run("stdio")
        return
    if hasattr(source, "wait_idle_s"):
        source.wait_idle_s = WAIT_IDLE_S
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
            act(screen, args, _catalog(args.apps, args.layouts), trace)
        elif args.cmd == "find":
            _, lines = screen.read(args.window_title, None)
            print(json.dumps([m.__dict__ for m in find(lines, args.query, all=True)]))
        elif args.cmd == "bench":
            bench(screen, args.window_title)
        elif args.cmd == "measure":
            from jharokha import measure

            measure.run(
                screen,
                args.window_title,
                args.app,
                args.anchor,
                _apps_path(args.apps)[0],
                args.zoom,
                args.names,
                args.focus,
                args.grid,
                args.view,
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


def act(screen: Screen, args: argparse.Namespace, catalog: Catalog, trace: Trace) -> None:
    """One input action, guarded as the server guards it (expect, the app's policy, and the
    source's lease and user checks), then (unless --quiet) the screen text after it."""
    w, frame = screen.frame(args.window_title)
    point: Point | None = getattr(args, "focus_at", None)
    if point:
        check_region((*point, 1, 1), frame)
    fields: dict[str, Any] = {"point": point}
    x = y = 0
    text = ""
    if args.cmd == "click":
        try:
            x, y = _ints(2)(",".join(args.point))
        except (argparse.ArgumentTypeError, ValueError):
            sys.exit(f"click: give X Y or X,Y, got {' '.join(args.point)!r}")
        check_region((x, y, 1, 1), frame)
        fields = {"point": (x, y), "keys": args.button + (" double" if args.double else "")}
    elif args.cmd == "scroll":
        x, y = args.at or (frame.width // 2, frame.height // 2)
        check_region((x, y, 1, 1), frame)
        fields = {"point": (x, y), "keys": f"wheel {args.ticks}"}
    elif args.cmd == "type":
        # a heredoc ends in a newline of its own; Enter is --enter's job
        given = sys.stdin.read().removesuffix("\n") if args.stdin else args.text
        if given is None:
            sys.exit("type: give the text or --stdin")
        fields["text"] = text = given + ("\n" if args.enter else "")
    else:
        fields["keys"] = args.keys
    on: list[str | None] = [None]
    try:
        policy = _front(screen, args, catalog, on)
        if args.cmd == "click":
            screen.source.click(w, x, y, args.button, args.double)
        elif args.cmd == "scroll":
            screen.source.scroll(w, x, y, args.ticks)
        elif args.cmd == "type":
            steps = guard.text_steps(text, args.paste, policy.newline)
            _check_keys(policy, guard.chords_of(steps), args.allow, on[0])
            for kind, s in steps:
                if kind == "press":
                    screen.source.press(w, s, point)
                else:
                    screen.source.type_text(w, s, point, kind == "paste")
                point = None
        else:
            try:
                chord = guard.combo_key(args.keys)
            except ValueError as e:
                raise ScreenError("bad_key", str(e)) from None
            _check_keys(policy, [chord], args.allow, on[0])
            screen.source.press(w, args.keys, point)
    except ScreenError as e:
        trace.record(args.cmd, w.title, front_app=on[0], result=e.code, **fields)
        raise
    trace.record(args.cmd, w.title, front_app=on[0], result="sent", **fields)
    screen.settle(args.window_title)
    start = time.monotonic()
    while args.until:
        _, lines = screen.read(args.window_title, None)
        if find(lines, args.until, regex=args.until_regex):
            break
        if time.monotonic() - start >= args.timeout:
            sys.exit(f"timeout: the input was sent, but '{args.until}' did not appear")
        time.sleep(0.5)
    if not args.quiet:
        _, lines = screen.read(args.window_title, None)
        print(format_concise(lines))


def _front(
    screen: Screen, args: argparse.Namespace, catalog: Catalog, on: list[str | None]
) -> Policy:
    """The app in front checked against --expect, and its input policy; identified only
    when --expect is given or the action is type or press, which a policy can shape."""
    expect = args.expect
    if expect is None and args.cmd not in ("type", "press"):
        return Policy()
    if expect is not None and not catalog.knows(expect):
        raise ScreenError(
            "bad_argument",
            f"--expect: no app or view '{expect}'; known: {', '.join(catalog.views())}",
        )
    frame, lines = screen.read(args.window_title, None)
    if looks_locked(lines):
        raise ScreenError("locked", "The session shows a lock screen. Unlock it and retry.")
    ident = identify(
        catalog.apps.values(), lines, panes(frame), background(frame), frame.width, frame.height
    )
    on[0] = f"{ident.app}.{ident.view}" if ident.app else None
    if expect is not None:
        try:
            guard.check_front(expect, ident.app, ident.view, ident.confidence)
        except guard.WrongApp as e:
            raise ScreenError(*guard.refusal(e)) from None
    app = catalog.apps.get(ident.app or "")
    return app.input_policy if app else Policy()


def _check_keys(policy: Policy, chords: list[str], allow: str | None, app: str | None) -> None:
    try:
        guard.check_keys(policy, chords, allow, app or "this app")
    except guard.Irreversible as e:
        raise ScreenError(*guard.refusal(e)) from None


def _apps_path(flag: str | None) -> tuple[Path, bool]:
    given = flag or os.environ.get("JHAROKHA_APPS")
    return Path(given or "apps.toml"), bool(given)


def _catalog(apps_flag: str | None, layouts_flag: str | None) -> Catalog:
    """Built-in apps, the old layouts file if there is one, then apps.toml."""
    given = layouts_flag or os.environ.get("JHAROKHA_LAYOUTS")
    lpath = Path(given or "layouts.toml")
    apath, apps_given = _apps_path(apps_flag)
    try:
        layouts = load(lpath) if given or lpath.exists() else None
        return apps.load(apath if apps_given or apath.exists() else None, layouts)
    except (OSError, LayoutError, apps.AppError) as e:
        sys.exit(f"apps: {e}")


def _actions(apps_flag: str | None) -> dict[str, actions.Action]:
    """Built-in actions, then those in apps.toml, then those in $JHAROKHA_ACTIONS."""
    apath, apps_given = _apps_path(apps_flag)
    paths = [apath] if apps_given or apath.exists() else []
    if extra := os.environ.get("JHAROKHA_ACTIONS"):
        paths.append(Path(extra))
    out = dict(actions.builtin())
    try:
        for p in paths:
            out.update(actions.parse(p.read_text(encoding="utf-8"), str(p)))
        return out
    except (OSError, actions.ActionError) as e:
        sys.exit(f"actions: {e}")


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
