"""Text-less UI elements from pixels: the icons along a taskbar, left to right.

A taskbar's icons keep their order but not their places: a taskbar holding two more
running apps can draw the same pinned icons 40 px apart instead of 55, so a spot measured
as a fraction clicks an icon two places off. A spot given as a slot (the nth icon) is
found again on every frame (core.session).

An icon locator for an app's own buttons (an `{"icon": true}` target, blobs off each pane's
background less the text OCR read) existed until v0.24.6; bring it back from git history
the first time a live task needs a button without text.
"""

from __future__ import annotations

import numpy as np

from jharokha.core.models import Box, Frame

INK = 60  # summed channel difference from the bar's colour that is an icon's pixel
GAP = 4  # blank columns that part two icons: an icon's own gaps are narrower
ICON_MIN, ICON_MAX = 0.2, 0.8  # an icon's width as a share of the bar's height
MARK = 0.22  # of the bar's height, top and bottom: the running-app marks under the icons


def taskbar_icons(frame: Frame, bar: Box) -> list[Box]:
    """The icons on the bar, left to right: column runs of pixels off the bar's colour,
    parted by a few blank columns, as wide as an icon. The bar's middle band only: the
    marks under running apps would join neighbours."""
    x, y, w, h = bar
    x, y = max(0, x), max(0, y)
    w, h = min(w, frame.width - x), min(h, frame.height - y)
    if w <= 0 or h <= 4:
        return []
    top, bottom = y + int(h * MARK), y + h - int(h * MARK)
    px = np.frombuffer(frame.bgra, np.uint8).reshape(frame.height, frame.width, 4)
    band = px[top:bottom, x : x + w, :3].astype(np.int32)
    bg = np.median(band.reshape(-1, 3), axis=0)
    ink = (np.abs(band - bg).sum(axis=2) > INK).sum(axis=0)
    cols = np.flatnonzero(ink > 1)
    if not len(cols):
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(cols[0])
    for c in cols[1:]:
        if c - prev > GAP:
            runs.append((start, prev))
            start = int(c)
        prev = int(c)
    runs.append((start, prev))
    out = []
    for a, b in runs:
        if ICON_MIN * h <= b - a + 1 <= ICON_MAX * h:
            out.append((x + a, top, b - a + 1, bottom - top))
    return out
