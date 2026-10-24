"""`jharokha` runs the MCP server on stdio. In a shell the same verbs as the tools:
`jharokha status|apps|read|find|wait|screenshot|do|learn`, with --allow-input `act`. Each
verb is the server's tool, called in-process, so a shell command is guarded and answers
exactly as the tool does."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from jharokha import version
from jharokha.core import actions, apps
from jharokha.core.apps import Catalog
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
        help="also serve (or, in a shell, allow) act, and do that sends input",
    )
    p.add_argument(
        "--apps",
        help="app definitions and calibration (default: $JHAROKHA_APPS, else ./apps.toml)",
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
    sub.add_parser("apps")
    r = sub.add_parser("read")
    r.add_argument("--region", type=_region, help="screen, a named region, P<n>, or x,y,w,h")
    r.add_argument("--format", choices=["auto", "lines", "items", "detailed", "panes"])
    r.add_argument("--since", help="'last', or a time for a list's items")
    r.add_argument("--verify", choices=["none", "flagged", "all"])
    r.add_argument("--limit", type=int, default=200, help="most lines or items to print")
    f = sub.add_parser("find")
    f.add_argument("query", nargs="?")
    f.add_argument("--region", type=_region)
    f.add_argument("--regex", action="store_true")
    f.add_argument("--all", action="store_true")
    f.add_argument("--locator", type=json.loads, help='JSON, e.g. {"right_of": "To"}')
    w = sub.add_parser("wait", help="until a text, a regex, a text gone, ready, else settled")
    w.add_argument("--text")
    w.add_argument("--regex")
    w.add_argument("--gone")
    w.add_argument("--ready", action="store_true")
    w.add_argument("--region", type=_region)
    w.add_argument("--timeout", type=float, default=10)
    s = sub.add_parser("screenshot", help="write a PNG of the window or a region")
    s.add_argument("out", help="PNG file to write")
    s.add_argument("--region", type=_region)
    s.add_argument("--max-long-edge", type=int, default=1024)
    a = sub.add_parser("act", help="one input: click, type, paste, press, scroll, ...")
    a.add_argument(
        "op", choices=["click", "double_click", "right_click", "type", "paste", "press", "scroll"]
    )
    a.add_argument("target", nargs="?", help="X,Y, a text to find, or a JSON locator")
    a.add_argument("--text", help="for type and paste (or --stdin)")
    a.add_argument(
        "--stdin", action="store_true", help="read the text from stdin, less one final newline"
    )
    a.add_argument("--enter", action="store_true", help="end the text with a newline")
    a.add_argument("--keys", help="for press, e.g. ctrl+c")
    a.add_argument("--ticks", type=int, default=-3, help="for scroll; negative scrolls down")
    a.add_argument("--expect", help="send nothing unless this app (or app.view) is in front")
    a.add_argument("--until", help="afterwards, wait until this text is on screen")
    a.add_argument("--timeout", type=float, default=10, help="seconds for --until")
    a.add_argument(
        "--allow",
        choices=["irreversible"],
        help="send a key the app's policy calls irreversible (Enter in Teams or a browser)",
    )
    a.add_argument("--quiet", action="store_true", help="do not read the screen afterwards")
    d = sub.add_parser("do", help="run an action (see `jharokha apps`)")
    d.add_argument("action")
    d.add_argument("--args", type=json.loads, default={}, help='JSON, e.g. {"folder": "Inbox"}')
    d.add_argument("--allow", choices=["read", "reversible", "irreversible"], default="reversible")
    d.add_argument("--dry-run", action="store_true")
    le = sub.add_parser("learn", help="name the session's taskbar icons as spots that switch apps")
    le.add_argument("--names", type=json.loads, help='JSON, e.g. {"T8": "teams"}')
    le.add_argument("--name-icons", action="store_true", help="read the icons' tooltips")
    le.add_argument("--save", action="store_true", help="write the spots into apps.toml")
    args = p.parse_args()
    source = _source()
    trace = Trace(args.trace, include_text=args.trace_text)
    apps_path = _apps_path(args.apps)[0]
    if args.cmd is None:
        from jharokha.server import build_server

        catalog = _catalog(args.apps)
        acts = _actions(args.apps)
        build_server(
            source, args.allow_input, catalog, trace, acts, apps_path, args.window_title
        ).run("stdio")
        return
    if hasattr(source, "wait_idle_s"):
        source.wait_idle_s = WAIT_IDLE_S
    if args.cmd == "act" and not args.allow_input:
        sys.exit(f"input_disabled: '{args.cmd}' sends real input; add --allow-input.")
    catalog = _catalog(args.apps)
    calls = _calls(args, catalog)
    from jharokha.server import build_server

    server = build_server(
        source,
        args.allow_input,
        catalog,
        trace,
        _actions(args.apps),
        apps_path,
        args.window_title,
    )
    done, error = asyncio.run(_run(server, calls))
    for text, image in done:
        if args.cmd == "screenshot":
            Path(args.out).write_bytes(image)
            text = json.dumps({"out": args.out} | json.loads(text))
        print(text)
    if error:
        sys.exit(error)


def _region(v: str) -> str | list[int]:
    parts = v.split(",")
    if len(parts) == 4:
        try:
            return [int(x) for x in parts]
        except ValueError:
            pass
    if len(parts) > 1:
        raise argparse.ArgumentTypeError(f"a region is x,y,w,h or a name, got {v!r}")
    return v


def _target(v: str | None) -> Any:
    if v is None:
        return None
    if v.startswith("{"):
        return json.loads(v)
    parts = v.split(",")
    if len(parts) == 2 and all(x.strip().lstrip("-").isdigit() for x in parts):
        return [int(x) for x in parts]
    return v


def _calls(args: argparse.Namespace, catalog: Catalog) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls a shell command stands for."""

    def given(**kw: Any) -> dict[str, Any]:
        return {k: v for k, v in kw.items() if v not in (None, False)}

    cmd = args.cmd
    if cmd in ("status", "apps"):
        return [(f"jharokha_{cmd}", given())]
    if cmd == "read":
        return [
            (
                "jharokha_read",
                given(
                    region=args.region,
                    format=args.format,
                    since=args.since,
                    verify=args.verify,
                    limit=args.limit,
                ),
            )
        ]
    if cmd == "find":
        if not args.query and not args.locator:
            sys.exit("find: give a query or --locator")
        f = given(query=args.query, region=args.region, regex=args.regex, locator=args.locator)
        return [("jharokha_find", f | {"all": True} if not args.locator else f)]
    if cmd == "wait":
        w = given(text=args.text, regex=args.regex, gone=args.gone, ready=args.ready)
        return [("jharokha_wait", w | given(region=args.region, timeout_s=args.timeout))]
    if cmd == "screenshot":
        return [
            ("jharokha_screenshot", given(region=args.region, max_long_edge=args.max_long_edge))
        ]
    if cmd == "do":
        d = given(action=args.action, args=args.args, allow=args.allow, dry_run=args.dry_run)
        return [("jharokha_do", d)]
    if cmd == "learn":
        return _learn_calls(args, given)
    # act
    text = args.text
    if args.stdin:
        text = sys.stdin.read().removesuffix("\n")  # a heredoc ends in a newline of its own
    if text is not None and args.enter:
        text += "\n"
    until = {"text": args.until, "timeout_s": args.timeout} if args.until else None
    a = given(
        op=args.op,
        target=_target(args.target),
        text=text,
        keys=args.keys,
        expect=args.expect,
        until=until,
        allow=args.allow,
    )
    if args.op == "scroll":
        a["ticks"] = args.ticks
    if args.quiet:
        a["show"] = "none"
    return [("jharokha_act", a)]


def _learn_calls(args: argparse.Namespace, given: Any) -> list[tuple[str, dict[str, Any]]]:
    """The taskbar scanned, its tooltips read with --name-icons, and with --names the spots
    shown as a change to apps.toml, written with --save: typed by the user. Icons are
    numbered left to right each scan, so names given with it hold for it."""
    if args.name_icons and not args.allow_input:
        sys.exit("input_disabled: --name-icons rests the pointer on each icon; add --allow-input.")
    tool = "jharokha_learn"
    calls = [(tool, given(step="taskbar"))]
    if args.name_icons:
        calls.append((tool, given(step="name_icons")))
    if not args.names:
        return calls
    return calls + [
        (tool, given(step="taskbar", names=args.names)),
        (tool, given(step="save", confirm=args.save)),
    ]


async def _run(
    server: Any, calls: list[tuple[str, dict[str, Any]]]
) -> tuple[list[tuple[str, bytes]], str | None]:
    """Each call's text and its image, if it returned one, up to the first error's text."""
    from mcp import Client
    from mcp.types import ImageContent, TextContent

    out = []
    async with Client(server) as c:
        for name, args in calls:
            r = await c.call_tool(name, args)
            texts = [x.text for x in r.content if isinstance(x, TextContent)]
            if r.is_error:
                return out, "\n".join(texts)
            image = b"".join(
                base64.b64decode(x.data) for x in r.content if isinstance(x, ImageContent)
            )
            out.append(("\n".join(texts), image))
    return out, None


def _apps_path(flag: str | None) -> tuple[Path, bool]:
    given = flag or os.environ.get("JHAROKHA_APPS")
    return Path(given or "apps.toml"), bool(given)


def _catalog(apps_flag: str | None) -> Catalog:
    """Built-in apps, then apps.toml."""
    apath, _ = _apps_path(apps_flag)
    try:
        # A file not there yet is one jharokha_learn has still to write.
        return apps.load(apath if apath.exists() else None)
    except (OSError, apps.AppError) as e:
        sys.exit(f"apps: {e}")


def _actions(apps_flag: str | None) -> dict[str, actions.Action]:
    """Built-in actions, then those in apps.toml, then those in $JHAROKHA_ACTIONS."""
    apath, _ = _apps_path(apps_flag)
    paths = [apath] if apath.exists() else []
    if extra := os.environ.get("JHAROKHA_ACTIONS"):
        paths.append(Path(extra))
    out = dict(actions.builtin())
    try:
        for p in paths:
            out.update(actions.parse(p.read_text(encoding="utf-8"), str(p)))
        return out
    except (OSError, actions.ActionError) as e:
        sys.exit(f"actions: {e}")


if __name__ == "__main__":
    main()
