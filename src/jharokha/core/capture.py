"""Opt-in capture of the frames read on a real session, to replay them offline later and fix
a reader without the session.

Off unless a directory is given: then each distinct frame identified for a tool call or an
action step is written there once, as <ts>-<frame_id>.png and a .json beside it with the
call, the app and view identified, the whole screen's OCR lines, the regions placed and the
rows each grid region was read as (a terminal's, cell by cell), and every tool call adds a
line to calls.jsonl: its arguments, timing, the frames it saw and what
it returned or the error that ended it. Those frames and results hold real names and
messages: the directory is the user's, never the repo."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from jharokha.core.identify import Identity
from jharokha.core.models import Box, Frame, Line
from jharokha.core.watch import changed_bbox


def record(
    frame: Frame,
    ident: Identity,
    lines: list[Line],
    regions: Mapping[str, Box],
    call: Mapping[str, Any] | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    """The sidecar of a captured frame, as JSON-ready data."""
    return {
        "call": dict(call) if call else None,
        "frame": {
            "id": frame.frame_id,
            "ts": frame.ts,
            "width": frame.width,
            "height": frame.height,
        },
        "app": ident.app,
        "view": ident.view,
        "confidence": ident.confidence,
        "ready": ident.ready,
        # Outside the region a read was for, the lines of an earlier read of those pixels.
        "partial": partial,
        "lines": [
            {
                "text": ln.text,
                "box": list(ln.box),
                "words": [{"text": w.text, "box": list(w.box)} for w in ln.words],
            }
            for ln in lines
        ],
        "regions": {k: list(b) for k, b in regions.items()},
    }


def outcome(result: Any) -> dict[str, Any]:
    """What a tool returned, as JSON-ready data: its text, with an image kept as its size."""
    is_error = bool(getattr(result, "is_error", False))
    parts = getattr(result, "content", result)
    if not isinstance(parts, list | tuple):
        parts = [parts]
    texts = []
    for part in parts:
        text = getattr(part, "text", None)
        data = getattr(part, "data", None)
        if isinstance(part, str):
            texts.append(part)
        elif text is not None:
            texts.append(text)
        elif data is not None:
            texts.append(f"<image {len(data)} bytes>")
        else:
            texts.append(str(part))
    return {"ok": not is_error, "result": "\n".join(texts)}


class Capture:
    def __init__(self, directory: str | Path | None) -> None:
        self.dir = Path(directory) if directory is not None else None
        # The frame saved last, its file stem and whether its lines were partial: the same
        # pixels come back under new frame ids while the screen holds still.
        self._last: tuple[Frame, str, bool] | None = None
        # The call running in this context and the file stems of the frames it saved or saw
        # again: per call, since an agent calls tools in parallel (a status and a
        # read at once, where one shared slot would log the status as the read and crash it).
        self._call: ContextVar[tuple[dict[str, Any], list[str]] | None] = ContextVar(
            f"capture_call_{id(self)}", default=None
        )

    @property
    def call(self) -> dict[str, Any] | None:
        """The tool call running in this context, as frames saved now name it."""
        cur = self._call.get()
        return cur[0] if cur else None

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    @contextmanager
    def during(self, tool: str, args: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        """Frames saved inside it name this call. On the way out the call goes to calls.jsonl
        with what the caller put in the dict it yields (outcome()), or the error that ended
        it, with its code."""
        call: dict[str, Any] = {"tool": tool, "args": dict(args)}
        seen: list[str] = []
        token = self._call.set((call, seen))
        started = time.time()
        done: dict[str, Any] = {}
        try:
            yield done
        except BaseException as e:
            done = {"ok": False, "error": {"type": type(e).__name__, "message": str(e)}}
            if getattr(e, "code", None) is not None:
                done["error"]["code"] = e.code  # type: ignore[attr-defined]
            raise
        finally:
            entry = {
                "ts": started,
                "ms": round((time.time() - started) * 1000),
                **call,
                "frames": seen,
                **done,
            }
            self._call.reset(token)
            self._append(entry)

    def _append(self, entry: dict[str, Any]) -> None:
        if self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with (self.dir / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def reused(self) -> None:
        """The current call read the pixels saved last again, without identifying them."""
        if self._last is not None:
            self._saw(self._last[1])

    def _saw(self, stem: str) -> None:
        cur = self._call.get()
        if cur is not None and stem not in cur[1]:
            cur[1].append(stem)

    def save(
        self,
        frame: Frame,
        ident: Identity,
        lines: list[Line],
        regions: Mapping[str, Box],
        partial: bool = False,
    ) -> Path | None:
        """Writes the frame and its sidecar unless these pixels were saved last; a whole
        screen's read of them replaces a partial one's sidecar. Returns the PNG written."""
        if self.dir is None:
            return None
        last = self._last
        if last is not None and (
            last[0].frame_id == frame.frame_id or changed_bbox(last[0], frame) is None
        ):
            if last[2] and not partial:
                self._write_json(last[1], record(frame, ident, lines, regions, self.call))
                self._last = (last[0], last[1], False)
            self._saw(last[1])
            return None
        stem = f"{int(frame.ts * 1000)}-{frame.frame_id}"
        self._saw(stem)
        self.dir.mkdir(parents=True, exist_ok=True)
        png = self.dir / f"{stem}.png"
        frame.image().convert("RGB").save(png)
        self._write_json(stem, record(frame, ident, lines, regions, self.call, partial))
        self._last = (frame, stem, partial)
        return png

    def rows(self, frame: Frame, region: str, lines: list[Line]) -> None:
        """The rows a grid region was read as, cell by cell (what terminal.run reads), added
        to the sidecar of the frame saved last when it shows these pixels: Windows OCR's
        lines identify a frame, these are what a replay of the shell needs."""
        if self.dir is None or self._last is None:
            return
        last, stem, _ = self._last
        if last.frame_id != frame.frame_id and changed_bbox(last, frame) is not None:
            return
        path = self.dir / f"{stem}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        data.setdefault("rows", {})[region] = [
            {"text": ln.text, "box": list(ln.box)} for ln in lines
        ]
        self._write_json(stem, data)

    def _write_json(self, stem: str, data: dict[str, Any]) -> None:
        assert self.dir is not None
        text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
        (self.dir / f"{stem}.json").write_text(text, encoding="utf-8")
