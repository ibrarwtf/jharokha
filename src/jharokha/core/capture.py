"""Opt-in capture of the frames read on a live session, to replay them offline later and fix
a reader without the session.

Off unless a directory is given: then each distinct frame identified for a tool call or an
action step is written there once, as <ts>-<frame_id>.png and a .json beside it with the
call, the app and view identified, the whole screen's OCR lines and the regions placed.
Those frames hold real names and messages: the directory is the user's, never the repo."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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


class Capture:
    def __init__(self, directory: str | Path | None) -> None:
        self.dir = Path(directory) if directory is not None else None
        self.call: dict[str, Any] | None = None
        # The frame saved last, its file stem and whether its lines were partial: the same
        # pixels come back under new frame ids while the screen holds still.
        self._last: tuple[Frame, str, bool] | None = None

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    @contextmanager
    def during(self, tool: str, args: Mapping[str, Any]) -> Iterator[None]:
        """Frames saved inside it name this call."""
        self.call = {"tool": tool, "args": dict(args)}
        try:
            yield
        finally:
            self.call = None

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
            return None
        stem = f"{int(frame.ts * 1000)}-{frame.frame_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        png = self.dir / f"{stem}.png"
        frame.image().convert("RGB").save(png)
        self._write_json(stem, record(frame, ident, lines, regions, self.call, partial))
        self._last = (frame, stem, partial)
        return png

    def _write_json(self, stem: str, data: dict[str, Any]) -> None:
        assert self.dir is not None
        text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
        (self.dir / f"{stem}.json").write_text(text, encoding="utf-8")
