"""Opt-in record of every input sent, for tracing an incident afterwards.

Off unless a path is given: then one JSON line per action is appended to that file, and
nothing else is ever written. Typed text goes in only with include_text; by default a
record holds its length and SHA-256, enough to match it against a known string."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from jharokha.core.models import Box, Point

# "do": an action refused before any of its input (its inputs are traced one by one);
# "hover": the pointer moved onto an icon to read its tooltip (jharokha_learn).
ACTIONS = frozenset({"click", "type", "press", "scroll", "hover", "do"})


def _now() -> datetime:
    return datetime.now().astimezone()  # local time with its offset: what the user remembers


class Trace:
    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        include_text: bool = False,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.path = path
        self.include_text = include_text
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def record(
        self,
        action: str,
        window_title: str,
        *,
        point: Point | None = None,
        region: Box | None = None,
        front_app: str | None = None,
        text: str | None = None,
        keys: str | None = None,
        result: str | None = None,
    ) -> dict[str, Any] | None:
        """Appends one record and returns it, or None when tracing is off. result is "sent"
        or the error code that stopped it (wrong_app, user_active, ...): refusals are
        traced too, as they are what an incident's trail is made of."""
        if self.path is None:
            return None
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        rec: dict[str, Any] = {
            "time": self.clock().isoformat(timespec="milliseconds"),
            "action": action,
            "window": window_title,
        }
        if point is not None:
            rec["point"] = list(point)
        if region is not None:
            rec["region"] = list(region)
        if front_app is not None:
            rec["front_app"] = front_app
        if keys is not None:
            rec["keys"] = keys
        if text is not None:
            if self.include_text:
                rec["text"] = text
            rec["text_len"] = len(text)
            rec["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if result is not None:
            rec["result"] = result
        # Opened per record: the file appears only once something is sent, and no handle
        # stays open to block the user from moving or reading it between actions.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
