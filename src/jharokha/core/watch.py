"""What changed on the screen: a diff of text lines between two frames, and a background
watcher that keeps a log of such diffs so an agent can ask "what happened since seq N"
instead of re-reading the whole screen."""

from __future__ import annotations

import difflib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import ImageChops

from jharokha.core.models import Box, Frame, Line
from jharokha.core.text import clean, wrap_untrusted

_MAX_CHANGED_AREA = 100  # px^2: a text caret, not a character


@dataclass(frozen=True)
class Change:
    seq: int
    ts: float
    frame_id: int
    bbox: Box | None  # pixels that changed, in client coordinates
    added: list[Line]
    removed: list[Line]


def diff_lines(old: list[Line], new: list[Line]) -> tuple[list[Line], list[Line]]:
    """Lines in new that were not in old, and the reverse, by text in reading order."""
    sm = difflib.SequenceMatcher(None, [ln.text for ln in old], [ln.text for ln in new])
    added: list[Line] = []
    removed: list[Line] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new[j1:j2])
    return added, removed


def format_diff(old: list[Line], new: list[Line]) -> str:
    """The lines that changed between two reads: added ones with their ref in the new read
    (`+ L<n> [x,y] text`, so a click can target them) and removed ones (`- [x,y] text`)."""
    sm = difflib.SequenceMatcher(None, [ln.text for ln in old], [ln.text for ln in new])
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            out.extend(f"- [{ln.box[0]},{ln.box[1]}] {clean(ln.text)}" for ln in old[i1:i2])
        if tag in ("replace", "insert"):
            out.extend(
                f"+ L{j} [{new[j].box[0]},{new[j].box[1]}] {clean(new[j].text)}"
                for j in range(j1, j2)
            )
    return wrap_untrusted("\n".join(out) or "(no text changed)")


def changed_bbox(old: Frame | None, new: Frame) -> Box | None:
    """Where the pixels differ, or None when the frames are the same for our purposes."""
    if old is None or (old.width, old.height) != (new.width, new.height):
        return (0, 0, new.width, new.height)
    if old.bgra == new.bgra:
        return None
    bb = ImageChops.difference(old.image(), new.image()).getbbox(alpha_only=False)
    if bb is None or (bb[2] - bb[0]) * (bb[3] - bb[1]) <= _MAX_CHANGED_AREA:
        return None
    return (bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1])


def format_changes(changes: list[Change]) -> str:
    out = []
    for c in changes:
        where = f" bbox=[{','.join(map(str, c.bbox))}]" if c.bbox else ""
        out.append(f"#{c.seq} ts={c.ts:.3f} frame_id={c.frame_id}{where}")
        out.extend(f"+ [{ln.box[0]},{ln.box[1]}] {clean(ln.text)}" for ln in c.added)
        out.extend(f"- [{ln.box[0]},{ln.box[1]}] {clean(ln.text)}" for ln in c.removed)
    return wrap_untrusted("\n".join(out) or "(no changes)")


@dataclass
class Watcher:
    """Polls capture() on its own thread; ocr() runs only when pixels changed, and a change
    is logged only when the text differs. Stops itself after idle_s without anyone asking
    for changes, so a forgotten watcher does not burn CPU for the rest of the day.

    Without a thread it still works as a one-shot differ: baseline() after a read, then
    observe() with the next frame."""

    capture: Callable[[], Frame]
    ocr: Callable[[Frame], list[Line]]
    interval_s: float = 0.5
    idle_s: float = 600
    maxlen: int = 200
    log: deque[Change] = field(init=False)
    seq: int = 0
    frames: int = 0
    error: str | None = None
    _frame: Frame | None = None
    _lines: list[Line] | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_asked: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.log = deque(maxlen=self.maxlen)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def baseline(self, frame: Frame, lines: list[Line]) -> None:
        with self._lock:
            self._frame, self._lines = frame, lines

    def start(self) -> None:
        if self.running:
            return
        self.log = deque(maxlen=self.maxlen)
        self.error = None
        self.frames = 0
        self._stop.clear()
        self._last_asked = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="jharokha-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def since(self, seq: int, limit: int) -> list[Change]:
        self._last_asked = time.monotonic()
        with self._lock:
            return [c for c in self.log if c.seq > seq][:limit]

    def observe(self, frame: Frame, lines: list[Line] | None = None) -> Change | None:
        """Compare with the baseline; OCR the frame (unless lines are given) only when pixels
        changed; log a change when the text differs. The frame becomes the new baseline."""
        with self._lock:
            prev_frame, prev_lines = self._frame, self._lines
        bbox = changed_bbox(prev_frame, frame)
        if bbox is None and lines is None:
            return None
        if lines is None:
            lines = self.ocr(frame)
        change = None
        if prev_lines is not None:
            added, removed = diff_lines(prev_lines, lines)
            if added or removed:
                with self._lock:
                    self.seq += 1
                    change = Change(self.seq, frame.ts, frame.frame_id, bbox, added, removed)
                    self.log.append(change)
        self.baseline(frame, lines)
        return change

    def _run(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() - self._last_asked > self.idle_s:
                break
            t = time.monotonic()
            try:
                self.observe(self.capture())
                self.frames += 1
                self.error = None
                wait = self.interval_s
            except Exception as e:  # keep watching; the window may come back
                self.error = str(e)
                wait = max(self.interval_s, 2.0)
            self._stop.wait(max(0.0, wait - (time.monotonic() - t)))
        self._thread = None
