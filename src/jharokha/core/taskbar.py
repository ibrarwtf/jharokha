"""Where the session's own Windows taskbar is: at the bottom, at the top, or hidden.

Calibrations (regions of an app's views, taskbar spots) are measured with the taskbar at
the bottom, where Windows puts it. With the taskbar at the top, every app draws 59 px
lower, the terminal's calibrated region takes the tab strip in and cuts its last rows off
(a wrapped command's end, the prompt that ends a command), and the taskbar spots point
into the app at the bottom of the screen. The bar is found on every
frame from its clock and date, the text it always shows at its right end; an app's regions
move down by its height when it is at the top, and its spots move with it."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jharokha.core.models import Box, Line

# The date at the bar's right end, under its clock, in the formats Windows shows it; the
# clock itself OCR misreads too often to count on (`lg:oo`, `oo:ol`, `22'.22` on captured
# frames), the date almost never.
_DATE = re.compile(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d\d-\d\d)\s*$")
RIGHT = 0.8  # of the width: the date sits right of this
EDGE = 0.08  # of the height: a bar's text sits this close to the top or bottom edge
SMALL = 0.03  # of the height: the most a line of the bar's text measures
BAR_MIN, BAR_MAX = 0.02, 0.1  # a bar's height as a share of the client's


@dataclass(frozen=True)
class Bar:
    edge: str  # "top" or "bottom"
    box: Box

    @property
    def shift(self) -> int:
        """How far an app is drawn down from where calibration measured it."""
        return self.box[3] if self.edge == "top" else 0


def find(lines: list[Line], panes: list[Box], width: int, height: int, guess: int) -> Bar | None:
    """The bar on a frame: its date, small text read by OCR at the right end of the top or
    bottom edge; its height that of the full-width pane along that edge when there is one
    of a bar's height, else guess (pixels). None when no such date is seen: the bar is
    hidden, or covered."""
    for ln in lines:
        x, y, _, h = ln.box
        if x < RIGHT * width or h > SMALL * height or not _DATE.search(ln.text):
            continue
        cy = y + h / 2
        if cy <= EDGE * height:
            edge = "top"
        elif cy >= (1 - EDGE) * height:
            edge = "bottom"
        else:
            continue
        bar_h = _pane_height(panes, edge, width, height) or guess
        return Bar(edge, (0, 0 if edge == "top" else height - bar_h, width, bar_h))
    return None


def _pane_height(panes: list[Box], edge: str, width: int, height: int) -> int | None:
    for _, y, w, h in panes:
        if w < 0.9 * width or not BAR_MIN * height <= h <= BAR_MAX * height:
            continue
        if (edge == "top" and y <= 1) or (edge == "bottom" and y + h >= height - 1):
            return h
    return None
