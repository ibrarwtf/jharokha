"""Learning an app from what is on screen, so a new app (or a new view of a known one) needs
no code. Hollow for now: only the session's taskbar is learned, the one part real use has
needed. The rest comes back, shaped by the live session, the first time a new app is learned
there; until then nobody knows what it needs. v0.24.0 in git history has the first attempt.

The shape it will fill:

    scan       panes, their roles and readers, anchors (labels the app draws in its bars,
               never content), icons
    propose    a definition in core.apps' `format = 1` schema, with its calibration
    verify     on more screens: identified, regions placed, anchors where they were
    variants   one calibration per zoom or window size
    save       what apps.toml should hold now; refuses anchors that look like content

Everything here is pure: no capture, no OCR, no file is written."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from jharokha.core import apps
from jharokha.core.apps import App
from jharokha.core.elements import elements as find_elements
from jharokha.core.models import Box, Frame, Line
from jharokha.core.panes import panes as find_panes
from jharokha.core.watch import diff_lines


@dataclass(frozen=True)
class Icon:
    """A text-less element (core.elements) a person, an agent or its tooltip can name."""

    index: int
    box: Box

    @property
    def center(self) -> tuple[int, int]:
        return (self.box[0] + self.box[2] // 2, self.box[1] + self.box[3] // 2)


# Scanning: to come.

# Proposing a definition: to come.

# Verifying on more screens: to come.

# Calibration variants (zoom, window size): to come.


# Naming icons by their tooltips

TIP_PX = 250  # how far from an icon its tooltip may appear
_COVERED_PX = 6  # a line that starts where one was is that line, partly covered


def tooltip(before: Sequence[Line], after: Sequence[Line], icon: Box) -> str | None:
    """The text that appeared near icon between two reads of the same place, with the
    pointer away and then resting on it: its tooltip. A line a tooltip covers part of reads
    shorter where it was and is not the tooltip."""
    added, removed = diff_lines(list(before), list(after))
    new = [
        ln
        for ln in added
        if not any(
            abs(ln.box[0] - r.box[0]) <= _COVERED_PX and abs(ln.box[1] - r.box[1]) <= _COVERED_PX
            for r in removed
        )
    ]
    cx, cy = icon[0] + icon[2] / 2, icon[1] + icon[3] / 2

    def dist(ln: Line) -> float:
        return abs(ln.box[0] + ln.box[2] / 2 - cx) + abs(ln.box[1] + ln.box[3] / 2 - cy)

    near = sorted((ln for ln in new if dist(ln) <= TIP_PX), key=dist)
    if not near:
        return None
    first = near[0]
    # A tooltip of two lines: the next line right under the first.
    parts = [first] + [
        ln
        for ln in near[1:]
        if 0 < ln.box[1] - first.box[1] <= 1.6 * first.box[3] and abs(ln.box[0] - first.box[0]) <= 8
    ][:1]
    text = " ".join(ln.text.strip() for ln in parts)
    return text[:80] or None


def icon_name(tip: str) -> str | None:
    """A spot name from a tooltip: its first three words, lower case, joined by _ ('Assign
    to me (Ctrl+M)' -> 'assign_to_me'). None when nothing is left."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", " ", tip).lower())[:3]
    return "_".join(words) or None


# The session's taskbar: its icons as spots, which no built-in definition can know

# What a taskbar tooltip says for an app the built-ins define by another name.
_TASKBAR_NAMES = {
    "edge": "browser",
    "chrome": "browser",
    "firefox": "browser",
    "powershell": "terminal",
    "command prompt": "terminal",
    "terminal": "terminal",
    "teams": "teams",
    "outlook": "outlook",
}


def taskbar_icons(frame: Frame, lines: Sequence[Line], box: Box) -> list[Icon]:
    """The text-less elements in the session's taskbar box, left to right: its app icons
    (the clock is text and drops out)."""

    def inside(e: Box) -> bool:
        cx, cy = e[0] + e[2] / 2, e[1] + e[3] / 2
        return box[0] <= cx < box[0] + box[2] and box[1] <= cy < box[1] + box[3]

    found = [e for e in find_elements(frame, list(lines), find_panes(frame)) if inside(e)]
    found.sort(key=lambda e: e[0])
    return [Icon(i, e) for i, e in enumerate(found)]


def taskbar_name(tip: str, known: Iterable[App]) -> str | None:
    """The app id a taskbar tooltip names: a known app's name or id in it, or one of the
    names its program goes by (Microsoft Edge is the browser); else a name made of the
    tooltip's words."""
    t = " ".join(re.findall(r"[a-z0-9]+", tip.lower()))
    for a in known:
        if a.id != apps.GENERIC and (a.name.lower() in t or re.search(rf"\b{a.id}\b", t)):
            return a.id
    for k, v in _TASKBAR_NAMES.items():
        if k in t:
            return v
    return icon_name(tip)


def save_spots(text: str, spots: Mapping[str, tuple[float, float]]) -> str:
    """apps.toml text with these session spots (`taskbar.teams` -> the icon's centre, as
    fractions of the client area) added to those it has, replacing any of the same name;
    its session regions and everything else stay. Checked to parse and load."""
    doc = apps.parse(text, "apps.toml") if text.strip() else apps.Doc()
    regions = {k.removeprefix(f"{apps.SESSION}."): v for k, v in doc.session_cal.items()}
    have = {k.removeprefix(f"{apps.SESSION}."): v for k, v in doc.spots.items()}
    have.update(spots)
    keep, drop = [], False
    for line in text.splitlines():
        if m := apps._HEADER.match(line.strip()):
            drop = apps._path(m.group(1))[:2] == ["calibration", apps.SESSION]
        if not drop:
            keep.append(line)
    out = [f"[calibration.{apps.SESSION}]", "# the session's own taskbar, learned"]
    if regions:
        parts = ", ".join(f"{json.dumps(k)} = {apps._nums(f)}" for k, f in regions.items())
        out.append(f"regions = {{ {parts} }}")
    parts = ", ".join(
        f"{json.dumps(k)} = [{round(x, 4):g}, {round(y, 4):g}]" for k, (x, y) in have.items()
    )
    out.append(f"spots = {{ {parts} }}")
    body = "\n".join(keep).rstrip() or f"format = {apps.FORMAT}"
    result = body + "\n\n" + "\n".join(out) + "\n"
    apps.catalog(apps.parse(result, "apps.toml"))
    return result


# Saving an app's definition: to come.
