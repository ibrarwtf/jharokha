"""What changed on the screen: where the pixels differ between two frames, and a diff of
text lines between two reads, so an agent can ask what changed since its last read instead
of reading the whole screen again. (A background watcher that logged every change lived
here up to 0.20; no scenario used it, and `wait` and `read(since="last")` cover it.)"""

from __future__ import annotations

import difflib

from PIL import ImageChops

from jharokha.core.models import Box, Frame, Line
from jharokha.core.text import clean, wrap_untrusted

_MAX_CHANGED_AREA = 100  # px^2: a text caret, not a character


MOVED_PX = 4  # a line this close to where the same text was has not moved


def fresh(old: list[Line], new: list[Line]) -> list[Line]:
    """Lines of new that old did not have: no old line with the same text in about the
    same place. A line that moved (a terminal scrolled) is fresh, as is one whose text
    changed; one that sat there all along is not."""
    seen: dict[str, list[tuple[int, int]]] = {}
    for ln in old:
        seen.setdefault(ln.text, []).append((ln.box[0], ln.box[1]))
    return [
        ln
        for ln in new
        if not any(
            abs(ln.box[0] - x) <= MOVED_PX and abs(ln.box[1] - y) <= MOVED_PX
            for x, y in seen.get(ln.text, ())
        )
    ]


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
