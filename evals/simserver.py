"""jharokha's MCP server on stdio over a simulated desktop, for agents to be evaluated on.

    uv run python -m evals.simserver --scenario NAME [--seed N] [--log FILE] [--codec]

The server is the real one, built with input allowed, over evals.sim's SimSource with the
desktop prepared for the scenario (evals/agents/setups.py). Nothing reaches a real window.
With --log, the desktop's side effects (messages sent, web searches, commands run, mails
opened, every click, key and scroll) and its final state (drafts, the open chat, the URL,
...) are written to FILE as JSON when the server starts, after every input and on exit, so
a grader can check them even when the client kills the server."""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path
from typing import Any

from evals.agents.setups import SCENARIOS, build
from evals.sim import Desktop, SimSource
from jharokha.core.models import Point, WindowInfo
from jharokha.server import build_server


class LoggedSource(SimSource):
    """SimSource that records each input as an `input` event and saves the log after it."""

    def __init__(
        self, desktop: Desktop, scenario: str, seed: int, log: Path | None, codec: bool = False
    ) -> None:
        super().__init__(desktop, codec=codec)
        self.scenario, self.seed, self.log = scenario, seed, log
        self._save_lock = threading.Lock()

    def _input(self, detail: str) -> None:
        self.desk.event(self.desk.front if self.desk.locked is None else "session", "input", detail)

    def scroll(self, w: WindowInfo, x: int, y: int, ticks: int) -> None:
        self._input(f"scroll {x},{y} ticks={ticks}")
        try:
            super().scroll(w, x, y, ticks)
        finally:
            self.save()

    def click(self, w: WindowInfo, x: int, y: int, button: str, double: bool) -> None:
        self._input(f"click {x},{y} {button}{' double' if double else ''}")
        try:
            super().click(w, x, y, button, double)
        finally:
            self.save()

    def type_text(
        self, w: WindowInfo, text: str, focus_at: Point | None = None, paste: bool = False
    ) -> None:
        self._input(f"{'paste' if paste else 'type'} {text!r}")
        try:
            super().type_text(w, text, focus_at, paste)
        finally:
            self.save()

    def press(self, w: WindowInfo, combo: str, focus_at: Point | None = None) -> None:
        self._input(f"press {combo}")
        try:
            super().press(w, combo, focus_at)
        finally:
            self.save()

    def record(self) -> dict[str, Any]:
        with self._lock:
            self.desk.tick()
            return {
                "scenario": self.scenario,
                "seed": self.seed,
                "events": [
                    {"t": e.t, "app": e.app, "kind": e.kind, "detail": e.detail}
                    for e in self.desk.events
                ],
                "state": state(self.desk),
            }

    def save(self) -> None:
        if self.log is None:
            return
        data = json.dumps(self.record(), indent=1)
        with self._save_lock:
            tmp = self.log.with_suffix(self.log.suffix + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.log)


def state(desk: Desktop) -> dict[str, Any]:
    """What a grader may check at the end: what is in front, what is drafted, opened, typed."""
    t, o, b, s = desk.teams, desk.outlook, desk.browser, desk.shell
    return {
        "front": desk.front,
        "locked": desk.locked is not None,
        "teams": {
            "open": t.chat.name,
            "drafts": {c.name: "\n".join(c.draft) for c in t.chats if any(c.draft)},
            "sent": [{"chat": c, "text": x} for c, x in t.sent],
            "search": t.search,
        },
        "outlook": {
            "view": o.view,
            "folder": o.folder,
            "selected": o.mails[o.selected].subject if o.mails else None,
            "unread": [m.subject for m in o.folders["Inbox"] if m.unread],
        },
        "browser": {"url": b.url, "searches": list(b.searches), "history": list(b.history)},
        "terminal": {"commands": list(s.log), "input": s.input},
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals.simserver")
    p.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--log", type=Path, help="write side effects and final state here (JSON)")
    p.add_argument("--codec", action="store_true", help="frames through the codec pass")
    args = p.parse_args()
    desk = build(args.scenario, args.seed)
    source = LoggedSource(desk, args.scenario, args.seed, args.log, args.codec)
    server = build_server(source, allow_input=True, catalog=source.catalog())
    source.save()
    try:
        server.run("stdio")
    finally:
        source.save()


if __name__ == "__main__":
    main()
