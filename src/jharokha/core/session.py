"""A frame as the tools and actions see it: which app and view it shows, where that view's
named regions are, and its terminal grids read cell by cell (core.glyphs on a grid from
core.rows). The server's read tools and the actions' executor share one Session, so both
read a screen the same way."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jharokha.core import apps, glyphs, rows
from jharokha.core.apps import SESSION, Catalog
from jharokha.core.capture import Capture
from jharokha.core.identify import Identity, identify
from jharokha.core.locate import RegionInfo, Spot, spot_px
from jharokha.core.models import Box, Frame, Line, inside, iou
from jharokha.core.panes import background, panes
from jharokha.core.screen import Screen, ScreenError
from jharokha.core.text import grid
from jharokha.core.watch import changed_bbox

TASKBAR = f"{SESSION}.taskbar"


class Where:
    """What is on one frame: the app and view, the panes found from its pixels, and the
    whole screen's lines as OCR read them (partial: outside a region, those of an earlier
    read of the unchanged pixels there)."""

    def __init__(
        self,
        frame: Frame,
        ident: Identity,
        found: list[Box],
        lines: list[Line],
        partial: bool = False,
    ) -> None:
        self.frame, self.ident, self.panes, self.lines = frame, ident, found, lines
        self.partial = partial


def _swap(lines: list[Line], area: Box, within: Box | None, new: list[Line]) -> list[Line]:
    """lines with those inside area (and within, for a region read) replaced by new, where
    the first of them was."""
    if within is not None:
        new = inside(new, within)
    gone = set(map(id, inside(lines, area)))
    rest = [ln for ln in lines if id(ln) not in gone]
    first = next((i for i, ln in enumerate(lines) if id(ln) in gone), None)
    if first is None:
        before = sum(ln.box[1] < area[1] for ln in rest)
    else:
        before = sum(id(ln) not in gone for ln in lines[:first])
    return rest[:before] + new + rest[before:]


class Session:
    def __init__(self, screen: Screen, catalog: Catalog, capture: Capture | None = None) -> None:
        self.screen, self.catalog = screen, catalog
        self.capture = capture
        self.last: Where | None = None  # the frame identified last, so a call does it once
        # A grid calibrated from the pixels, per app, view, region, window size and box, kept
        # once it was measured on three rows of text or more; and the last read of each, so
        # only rows whose pixels changed are read again.
        self._grids: dict[tuple[Any, ...], rows.Grid] = {}
        self._reads: dict[tuple[Any, ...], glyphs.Read] = {}
        # The glyphs known per font and size (core.glyphs.store_key).
        self._stores: dict[tuple[int, int], glyphs.Store] = {}
        # Inked cells the last shape() found no glyph for, and the glyphs known for the font
        # it read (None: it read no terminal grid).
        self.unknown = 0
        self.glyphs: int | None = None

    def where(self, frame: Frame, lines: list[Line]) -> Where:
        """The app and view on a frame, from the whole screen's lines. The screen's cache
        hands back the same lines while the pixels hold still, so those are not identified
        twice."""
        last = self.last
        if last is not None and (last.frame is frame or (lines is last.lines and not last.partial)):
            return self._reused(last)
        return self._identify(frame, lines, False)

    def around(
        self, frame: Frame, box: Box, region: list[Line], whole: Callable[[], list[Line]]
    ) -> Where:
        """The app and view for a read of region box: the last identity while the pixels
        have not changed since it, or changed only inside box, where the region's own lines
        stand in for the old ones (identifying costs a few ms, the whole screen's OCR about
        a hundred); else whole() is read and identified."""
        last = self.last
        if last is not None:
            bb = changed_bbox(last.frame, frame)
            if bb is None:
                return self._reused(last)
            x, y, w, h = box
            if x <= bb[0] and y <= bb[1] and bb[0] + bb[2] <= x + w and bb[1] + bb[3] <= y + h:
                gone = set(map(id, inside(last.lines, box)))
                kept = [ln for ln in last.lines if id(ln) not in gone]
                return self._identify(frame, kept + region, True)
        return self.where(frame, whole())

    def _identify(self, frame: Frame, lines: list[Line], partial: bool) -> Where:
        found = panes(frame)
        ident = identify(
            self.catalog.apps.values(), lines, found, background(frame), frame.width, frame.height
        )
        self.last = Where(frame, ident, found, lines, partial)
        if self.capture is not None and self.capture.enabled:
            self.capture.save(frame, ident, lines, self._placed(self.last), partial)
        return self.last

    def _reused(self, last: Where) -> Where:
        """last, noted in the capture as a frame the current call saw."""
        if self.capture is not None and self.capture.enabled:
            self.capture.reused()
        return last

    def _placed(self, at: Where) -> dict[str, Box]:
        return self.regions(at)[0]

    def regions(
        self, at: Where, short: bool = False
    ) -> tuple[dict[str, Box], dict[str, RegionInfo]]:
        """The session's regions and the view's, placed on at (`teams.thread`; with short,
        `thread` too), and how each of the view's reads as items: what every tool, action
        and capture places the same way."""
        f, ident, cat = at.frame, at.ident, self.catalog
        boxes: dict[str, Box] = {}
        info: dict[str, RegionInfo] = {}
        for name in dict.fromkeys([*cat.session_cal, *cat.session]):
            if p := cat.session_box(name, at.panes, f.width, f.height):
                boxes[name] = p[0]
        if ident.app and ident.view:
            for name, reg in cat.apps[ident.app].views[ident.view].regions.items():
                if p := self.place(at, ident.app, ident.view, name):
                    inner = tuple(self.nested(at, ident.app, ident.view, name))
                    for k in (f"{ident.app}.{name}", name) if short else (f"{ident.app}.{name}",):
                        boxes.setdefault(k, p[0])
                        info.setdefault(k, RegionInfo(reg.reader, reg.kind, inner))
        return boxes, info

    def spots(self, at: Where, app: str | None) -> dict[str, Spot]:
        """The session's spots and app's (the one in front; None: none of an app's), in
        pixels of at's frame. A spot given as a slot is the nth icon found on the session's
        taskbar now (core.elements), and missing while the bar shows fewer."""
        f, cat = at.frame, self.catalog
        out = {
            k: spot_px(p, f.width, f.height)
            for k, p in cat.spots_for(app, f.width, f.height).items()
        }
        if cat.slots and (bar := cat.session_box(TASKBAR, at.panes, f.width, f.height)):
            from jharokha.core.elements import taskbar_icons  # numpy: the server starts without it

            icons = taskbar_icons(f, bar[0])
            for k, n in cat.slots.items():
                if n <= len(icons):
                    x, y, w, h = icons[n - 1]
                    out[k] = (x + w // 2, y + h // 2)
        return out

    def place(self, at: Where, app: str, view: str, name: str) -> tuple[Box, str] | None:
        f = at.frame
        return self.catalog.region_box(app, view, name, at.panes, at.ident.found, f.width, f.height)

    def nested(self, at: Where, app: str, view: str, name: str) -> list[Box]:
        """The boxes of the view's regions inside region name."""
        f = at.frame
        return self.catalog.nested(app, view, name, at.panes, at.ident.found, f.width, f.height)

    def grids(self, at: Where) -> list[Box]:
        """Where the view on screen has terminal grids."""
        ident, out = at.ident, []
        if ident.app is None or ident.view is None:
            return out
        for name in self.catalog.apps[ident.app].views[ident.view].grids:
            if placed := self.place(at, ident.app, ident.view, name):
                out.append(placed[0])
        return out

    def shape(self, at: Where, lines: list[Line], region: Box | None = None) -> list[Line]:
        """Grid regions of the view on screen read cell by cell (core.glyphs), or, where no
        grid can be measured, Windows OCR's words snapped to the grid calibration gives."""
        ident, f = at.ident, at.frame
        self.unknown, self.glyphs = 0, None
        if ident.app is None or ident.view is None:
            return lines
        for name in self.catalog.apps[ident.app].views[ident.view].grids:
            placed = self.place(at, ident.app, ident.view, name)
            if placed and (region is None or iou(placed[0], region) > 0):
                g = self.catalog.grid(ident.app, ident.view, name, ident.found, f.width, f.height)
                got = self._by_cells(at, name, placed[0], g)
                if got is not None and self.capture is not None and self.capture.enabled:
                    self.capture.rows(f, f"{ident.app}.{name}", got)
                if got is None:
                    lines = grid(lines, placed[0], g.pitch, g.char_width, g.origin)
                else:
                    lines = _swap(lines, placed[0], region, got)
        return lines

    def _grid(self, at: Where, name: str, box: Box, cal: apps.Grid) -> rows.Grid | None:
        """The grid of region name on this frame: the one the glyphs were learned on while
        they fit; else from calibration, else measured once from the pixels, snapped to
        whole pixels and kept. A read whose cells nearly all sit far from the templates
        means the grid is off (a zoom step, a resize), and it is searched back onto them."""
        f, ident = at.frame, at.ident
        key = (ident.app, ident.view, name, f.width, f.height)
        g = rows.measured(box, cal.pitch, cal.char_width, cal.origin) or self._grids.get(key)
        if g is None:
            g = rows.calibrate(f, at.lines, box)
            if g is None:
                return None
            # Kept across the region's width: each read ends it at that frame's text edge,
            # which a scrollbar appearing later or a mark going away moves.
            g = glyphs.snapped(glyphs.refine(f, rows.spanning(g, box), box))
            if len(inside(at.lines, box)) >= 3:
                self._grids[key] = g
        store = self._stores.get(glyphs.store_key(g))
        if store is not None and store.grid is not None:
            last = self._reads.get(key)
            if last is not None and last.distance > glyphs.OFF_GRID:
                moved = glyphs.snapped(glyphs.refine(f, store.grid, box, store))
                if moved != store.grid:
                    store.grid = moved
                    self._reads.pop(key, None)
            g = rows.spanning(store.grid, box)
        return g

    def _by_cells(self, at: Where, name: str, box: Box, cal: apps.Grid) -> list[Line] | None:
        f, ident = at.frame, at.ident
        key = (ident.app, ident.view, name, f.width, f.height)
        g = self._grid(at, name, box, cal)
        if g is None:
            return None
        g = rows.bounded(g, f, box)
        store = self._stores.setdefault(glyphs.store_key(g), glyphs.Store())
        self.glyphs = len(store)
        source = self.screen.source
        no_engine = False

        def engine(boxes: list[Box]) -> list[str]:
            nonlocal no_engine
            try:
                return source.reread(f, boxes, 0)
            except ScreenError:
                no_engine = True
                return [""] * len(boxes)

        got = glyphs.read(f, g, box, store, engine, self._reads.get(key))
        if no_engine and not len(store):
            return None  # nothing can name a cell yet: Windows OCR's words on the grid
        self._reads[key] = got
        self.unknown += got.unknown
        return [ln for ln in rows.unwrap(got.lines, got.grid) if ln.text]

    def learn_glyphs(self, at: Where) -> int:
        """Templates from the two alphabet lines on screen (core.glyphs.learn_alphabet) in
        each grid region of the view in front; how many glyphs were learned."""
        ident, f = at.ident, at.frame
        if ident.app is None or ident.view is None:
            return 0
        learned = 0
        for name in self.catalog.apps[ident.app].views[ident.view].grids:
            placed = self.place(at, ident.app, ident.view, name)
            if not placed:
                continue
            cal = self.catalog.grid(ident.app, ident.view, name, ident.found, f.width, f.height)
            g = self._grid(at, name, placed[0], cal)
            if g is None:
                continue
            store = glyphs.learn_fitted(f, rows.bounded(g, f, placed[0]), placed[0])
            if store.grid is not None:
                # The grid the glyphs fit is the region's from now on, its pitch included.
                self._stores[glyphs.store_key(store.grid)] = store
                key = (ident.app, ident.view, name, f.width, f.height)
                self._grids[key] = rows.spanning(store.grid, placed[0])
            learned += len(store)
        if learned:
            self._reads.clear()
        return learned

    def focus_box(self, at: Where) -> tuple[str, Box] | None:
        ident = at.ident
        if ident.app is None or ident.view is None:
            return None
        name = self.catalog.apps[ident.app].views[ident.view].focus
        placed = self.place(at, ident.app, ident.view, name) if name else None
        return (f"{ident.app}.{name}", placed[0]) if placed else None
