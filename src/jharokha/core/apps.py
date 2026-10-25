"""Apps as data. A definition (TOML, `format = 1`) says how to recognise an app and each of
its views from text and pixels, and where each view's regions are; calibration measured on
one setup says where they are exactly there. Generic definitions ship in jharokha/apps/; a
local apps.toml adds calibration, new apps and overrides of built-in ones.

    format = 1
    [app.teams]
    name = "Microsoft Teams"
    [[app.teams.cue]]                     # text, regex, background or pane; weighted
    text = "Search (Ctrl+E)"
    at = [0.3, 0.02]                      # where it belongs, as fractions of the client area
    weight = 2
    [app.teams.view.chat]
    ready = "Type a message"              # on screen once the view has finished loading
    [app.teams.view.chat.region.thread]
    reader = "list"                       # text | grid | list | table | form (core.readers)
    kind = "messages"                     # a list's items: messages | entries | mails
    at = [0.65, 0.5]                      # the pane under this point
    box = [0.378, 0.122, 0.622, 0.828]    # fallback, and the check on the pane
    focus = true

A region is the pane (core.panes) under a point, widened with `to` to the pane under a
second point: panes are found from the app's own divider lines and background colours, so
they follow the window size and the app's zoom where a fixed box does not. The box is the
fallback for a part with no edges of its own (a compose box inside the thread's pane), and
the sanity check on the pane: one that overlaps it too little (IoU under PANE_FIT) is a
dialog or a different arrangement, and the box is used instead. Calibration, measured on
the user's own screen, comes before both.

An app's `policy` table shapes and limits input to it (core.guard.Policy):

    [app.teams.policy]
    newline = "shift+enter"               # a typed newline adds a line, never sends
    irreversible_keys = ["enter"]         # refused unless the call passes allow="irreversible"

An app that declares no policy table gets DEFAULT_POLICY: Enter, Ctrl+Enter and Alt+S held
back and newlines typed as Shift+Enter, since most reply boxes send on one of them. A table
declared empty (`[app.x.policy]` alone, as the terminal's) opts out. The generic app, the
screen no definition knows, has none.

An app's `spots` name its text-less targets, points measured on the user's screen:

    [app.tickets]
    spots = { reply = [0.127, 0.06] }     # a target as {spot = "tickets.reply"}, only while
                                          # tickets is in front

Top-level `[action.<app>.<name>]` tables are local actions, read by core.actions."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from jharokha.core.geometry import Fraction, choose, to_box
from jharokha.core.guard import Policy
from jharokha.core.models import Box, iou
from jharokha.core.readers import KINDS, READERS

FORMAT = 1
GENERIC = "generic"
SESSION = "session"
PANE_FIT = 0.6
# For an app whose definition has no [app.x.policy]: a learned app, or one written by hand.
# Its reply box may send on any of these, so typing never submits and each needs
# allow="irreversible" until the user declares what the app really does.
DEFAULT_POLICY: dict[str, Any] = {
    "newline": "shift+enter",
    "irreversible_keys": ["enter", "ctrl+enter", "alt+s"],
}

Point2 = tuple[float, float]
Spot = tuple[float, ...]  # x, y; or the centre and the size: x, y, width, height
RGB = tuple[int, int, int]


class AppError(ValueError):
    pass


@dataclass(frozen=True)
class Cue:
    """One piece of evidence: text (any of several spellings) or a regex on screen, the
    screen's background colour, or a pane about where the app draws one."""

    text: tuple[str, ...] = ()
    regex: str | None = None
    background: RGB | None = None
    pane: Fraction | None = None
    at: tuple[Point2, ...] = ()  # where the text's top-left belongs; any of these
    near: float = 0.1  # how far from `at` still counts as there, as a fraction
    weight: float = 1.0
    report: str | None = None  # report the matched text under this name (a URL)

    @property
    def label(self) -> str:
        if self.text:
            return self.text[0]
        if self.regex:
            return f"/{self.regex}/"
        return f"background {self.background}" if self.background else f"pane {self.pane}"


@dataclass(frozen=True)
class Region:
    name: str
    reader: str = "text"
    at: Point2 | None = None
    to: Point2 | None = None
    box: Fraction | None = None
    focus: bool = False
    kind: str | None = None  # a list's items: messages, entries or mails

    @property
    def label(self) -> str:
        """The reader as jharokha_apps names it: list:messages, table, ..."""
        return f"{self.reader}:{self.kind or 'entries'}" if self.reader == "list" else self.reader


@dataclass(frozen=True)
class View:
    id: str
    cues: tuple[Cue, ...] = ()
    ready: tuple[str, ...] = ()  # the view has loaded once any of these is on screen
    regions: dict[str, Region] = field(default_factory=dict)

    @property
    def focus(self) -> str | None:
        return next((r.name for r in self.regions.values() if r.focus), None)

    @property
    def grids(self) -> list[str]:
        return [r.name for r in self.regions.values() if r.reader == "grid"]


@dataclass(frozen=True)
class App:
    id: str
    name: str
    cues: tuple[Cue, ...] = ()
    views: dict[str, View] = field(default_factory=dict)
    # As written, checked when parsed; None when the definition has no policy table.
    policy: dict[str, Any] | None = None
    source: str = "built-in"
    # Text-less targets (a send button, an attach icon) by name, as fractions of the client
    # area. The locator {spot = "<app>.<name>"} takes one only while the app is in front.
    spots: dict[str, Spot] = field(default_factory=dict)

    @property
    def input_policy(self) -> Policy:
        table = self.policy
        if table is None:
            table = {} if self.id == GENERIC else DEFAULT_POLICY
        return Policy.parse(table, f"app.{self.id}.policy")


@dataclass(frozen=True)
class Grid:
    """A character grid as measured: row pitch and character width in pixels, and the
    first cell's top-left relative to its region."""

    pitch: float | None = None
    char_width: float | None = None
    origin: tuple[int, int] | None = None


@dataclass(frozen=True)
class Variant:
    """One way a view was measured, such as one zoom level; told apart by where its
    anchor text sat (geometry.choose)."""

    name: str
    anchor: str | None = None
    anchor_at: Point2 | None = None
    regions: dict[str, Fraction] = field(default_factory=dict)
    grids: dict[str, Grid] = field(default_factory=dict)


@dataclass(frozen=True)
class Calibration:
    variants: tuple[Variant, ...]
    focus: str | None = None
    readers: dict[str, str] = field(default_factory=dict)


@dataclass
class Doc:
    """What one file holds."""

    apps: dict[str, App] = field(default_factory=dict)
    session: dict[str, Region] = field(default_factory=dict)  # "session.taskbar"
    calibration: dict[tuple[str, str], Calibration] = field(default_factory=dict)
    session_cal: dict[str, Fraction] = field(default_factory=dict)
    spots: dict[str, Point2] = field(default_factory=dict)


# Parsing


def _only(table: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    extra = sorted(set(table) - set(allowed))
    if extra:
        raise AppError(f"{where}: unknown key {', '.join(extra)}")


def _tables(v: Any, where: str) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict) or not all(isinstance(t, dict) for t in v.values()):
        raise AppError(f"{where} must be tables")
    return v


def _id(name: str, where: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise AppError(f"{where}: '{name}' must be letters, digits, - or _")
    return name


def _frac(v: Any, where: str) -> Fraction:
    ok = isinstance(v, list) and len(v) == 4
    ok = ok and all(isinstance(f, int | float) and 0 <= f <= 1 for f in v)
    if not ok or v[0] + v[2] > 1.001 or v[1] + v[3] > 1.001 or not (v[2] and v[3]):
        raise AppError(
            f"{where} must be [x, y, width, height] as fractions 0..1 of the client area "
            "that stay inside it"
        )
    return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))


def _point(v: Any, where: str) -> Point2:
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(f, int | float) for f in v)):
        raise AppError(f"{where} must be [x, y] as fractions of the client area")
    if not all(0 <= f <= 1 for f in v):
        raise AppError(f"{where} must be [x, y] as fractions 0..1")
    return (float(v[0]), float(v[1]))


def _spot(v: Any, where: str) -> Spot:
    ok = isinstance(v, list) and len(v) in (2, 4)
    if not ok or not all(isinstance(f, int | float) and 0 <= f <= 1 for f in v):
        raise AppError(
            f"{where} must be [x, y], or [x, y, width, height] with x, y the centre, as "
            "fractions 0..1 of the client area"
        )
    return tuple(float(f) for f in v)


def _num(v: Any, where: str) -> float:
    if not isinstance(v, int | float) or isinstance(v, bool) or v < 0:
        raise AppError(f"{where} must be a number of 0 or more")
    return float(v)


def _text(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise AppError(f"{where} must be text")
    return v


def _texts(v: Any, where: str) -> tuple[str, ...]:
    """One text or a list of them, none empty."""
    items = v if isinstance(v, list) else [v]
    out = tuple(_text(s, where) for s in items)
    if not out:
        raise AppError(f"{where} must not be empty")
    return out


def _regex(v: Any, where: str) -> str:
    try:
        re.compile(_text(v, where))
    except re.error as e:
        raise AppError(f"{where} must be a regex: {e}") from e
    return v


def _cue(t: dict[str, Any], where: str) -> Cue:
    _only(t, ("text", "regex", "background", "pane", "at", "near", "weight", "report"), where)
    kinds = [k for k in ("text", "regex", "background", "pane") if k in t]
    if len(kinds) != 1:
        raise AppError(f"{where}: needs exactly one of text, regex, background, pane")
    text: tuple[str, ...] = ()
    if "text" in t:
        text = _texts(t["text"], f"{where}.text")
    bg = None
    if "background" in t:
        v = t["background"]
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, int) for c in v)):
            raise AppError(f"{where}.background must be [r, g, b]")
        bg = (int(v[0]), int(v[1]), int(v[2]))
    at: tuple[Point2, ...] = ()
    if "at" in t:
        v = t["at"]
        many = isinstance(v, list) and v and isinstance(v[0], list)
        at = tuple(_point(p, f"{where}.at") for p in (v if many else [v]))
        if not (text or "regex" in t):
            raise AppError(f"{where}.at is for text and regex cues")
    report = t.get("report")
    if report is not None:
        _id(report, f"{where}.report")
    return Cue(
        text=text,
        regex=_regex(t["regex"], f"{where}.regex") if "regex" in t else None,
        background=bg,
        pane=_frac(t["pane"], f"{where}.pane") if "pane" in t else None,
        at=at,
        near=_num(t.get("near", 0.1), f"{where}.near"),
        weight=_num(t.get("weight", 1.0), f"{where}.weight"),
        report=report,
    )


def _cues(v: Any, where: str) -> tuple[Cue, ...]:
    if not isinstance(v, list) or not all(isinstance(t, dict) for t in v):
        raise AppError(f"{where} must be [[...cue]] tables")
    return tuple(_cue(t, f"{where}[{i}]") for i, t in enumerate(v))


def _region(name: str, t: dict[str, Any], where: str) -> Region:
    _only(t, ("reader", "kind", "at", "to", "box", "focus"), where)
    reader = t.get("reader", "text")
    if reader not in READERS:
        raise AppError(f"{where}.reader must be one of {', '.join(READERS)}")
    kind = t.get("kind")
    if kind is not None and (reader != "list" or kind not in KINDS):
        raise AppError(f"{where}.kind is for reader = 'list': one of {', '.join(KINDS)}")
    if "at" not in t and "box" not in t:
        raise AppError(f"{where}: needs at (a pane) or box")
    if "to" in t and "at" not in t:
        raise AppError(f"{where}.to needs at")
    focus = t.get("focus", False)
    if not isinstance(focus, bool):
        raise AppError(f"{where}.focus must be true or false")
    return Region(
        name=_id(name, where),
        reader=reader,
        at=_point(t["at"], f"{where}.at") if "at" in t else None,
        to=_point(t["to"], f"{where}.to") if "to" in t else None,
        box=_frac(t["box"], f"{where}.box") if "box" in t else None,
        focus=focus,
        kind=kind,
    )


def _view(vid: str, t: dict[str, Any], where: str) -> View:
    _only(t, ("cue", "ready", "region"), where)
    regions = {
        k: _region(k, r, f"{where}.region.{k}")
        for k, r in _tables(t.get("region", {}), f"{where}.region").items()
    }
    if sum(r.focus for r in regions.values()) > 1:
        raise AppError(f"{where}: at most one region has focus = true")
    return View(
        id=_id(vid, where),
        cues=_cues(t.get("cue", []), f"{where}.cue"),
        ready=_texts(t["ready"], f"{where}.ready") if "ready" in t else (),
        regions=regions,
    )


def _app(aid: str, t: dict[str, Any], where: str, source: str) -> App:
    _only(t, ("name", "cue", "view", "policy", "spots"), where)
    if aid == SESSION:
        raise AppError(f"{where}: '{SESSION}' is reserved for the session's own regions")
    views = {
        k: _view(k, v, f"{where}.view.{k}")
        for k, v in _tables(t.get("view", {}), f"{where}.view").items()
    }
    if not views:
        raise AppError(f"{where}: needs at least one [{where}.view.<id>]")
    if not isinstance(t.get("policy", {}), dict):
        raise AppError(f"{where}.policy must be a table")
    try:
        Policy.parse(t.get("policy", {}), f"{where}.policy")
    except ValueError as e:
        raise AppError(str(e)) from None
    if not isinstance(t.get("spots", {}), dict):
        raise AppError(f"{where}.spots must be a table of [x, y]")
    spots = {
        _id(k, f"{where}.spots"): _spot(p, f"{where}.spots.{k}")
        for k, p in t.get("spots", {}).items()
    }
    return App(
        id=_id(aid, where),
        name=_text(t.get("name", aid), f"{where}.name"),
        cues=_cues(t.get("cue", []), f"{where}.cue"),
        views=views,
        policy=dict(t["policy"]) if "policy" in t else None,
        source=source,
        spots=spots,
    )


def _grids(v: Any, where: str) -> dict[str, Grid]:
    out = {}
    for name, g in _tables(v, where).items():
        _only(g, ("pitch", "char_width", "origin"), f"{where}.{name}")
        origin = g.get("origin")
        if origin is not None and not (
            isinstance(origin, list)
            and len(origin) == 2
            and all(isinstance(o, int) for o in origin)
        ):
            raise AppError(f"{where}.{name}.origin must be [x, y] in pixels")
        out[name] = Grid(
            _num(g["pitch"], f"{where}.{name}.pitch") if "pitch" in g else None,
            _num(g["char_width"], f"{where}.{name}.char_width") if "char_width" in g else None,
            (origin[0], origin[1]) if origin else None,
        )
    return out


def _variant(name: str, t: dict[str, Any], base: Variant, where: str) -> Variant:
    _only(t, ("anchor", "anchor_at", "regions", "grids"), where)
    regions = dict(base.regions)
    if not isinstance(t.get("regions", {}), dict):
        raise AppError(f"{where}.regions must be a table of [x, y, width, height]")
    for k, f in t.get("regions", {}).items():
        regions[_id(k, f"{where}.regions")] = _frac(f, f"{where}.regions.{k}")
    v = Variant(
        name=name,
        anchor=_text(t["anchor"], f"{where}.anchor") if "anchor" in t else base.anchor,
        anchor_at=_point(t["anchor_at"], f"{where}.anchor_at")
        if "anchor_at" in t
        else base.anchor_at,
        regions=regions,
        grids={**base.grids, **_grids(t.get("grids", {}), f"{where}.grids")},
    )
    if v.anchor_at and not v.anchor:
        raise AppError(f"{where}.anchor_at needs anchor")
    return v


def _calibration(t: dict[str, Any], where: str) -> Calibration:
    _only(t, ("anchor", "anchor_at", "regions", "grids", "focus", "readers", "variant"), where)
    common = {k: t[k] for k in ("anchor", "anchor_at", "regions", "grids") if k in t}
    base = _variant("", common, Variant(""), where)
    named = _tables(t.get("variant", {}), f"{where}.variant")
    variants = tuple(_variant(n, v, base, f"{where}.variant.{n}") for n, v in named.items())
    readers = t.get("readers", {})
    ok = (*READERS, *(f"list:{k}" for k in KINDS))
    if not isinstance(readers, dict) or not all(r in ok for r in readers.values()):
        raise AppError(f"{where}.readers must map regions to one of {', '.join(ok)}")
    return Calibration(
        variants=variants or (base,),
        focus=_id(t["focus"], f"{where}.focus") if "focus" in t else None,
        readers=dict(readers),
    )


def parse(text: str, source: str = "apps", origin: str = "local") -> Doc:
    """One definitions and calibration file. origin names where its apps come from."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise AppError(f"{source}: {e}") from e
    if data.get("format") != FORMAT:
        raise AppError(f"{source}: needs `format = {FORMAT}` at the top")
    _only(data, ("format", "app", "session", "calibration", "action"), source)
    doc = Doc()
    for aid, t in _tables(data.get("app", {}), f"{source}: app").items():
        doc.apps[aid] = _app(aid, t, f"{source}: app.{aid}", origin)
    session = data.get("session", {})
    _only(session, ("region",), f"{source}: session")
    for name, t in _tables(session.get("region", {}), f"{source}: session.region").items():
        doc.session[f"{SESSION}.{name}"] = _region(name, t, f"{source}: session.region.{name}")
    for aid, views in _tables(data.get("calibration", {}), f"{source}: calibration").items():
        where = f"{source}: calibration.{aid}"
        if aid == SESSION:
            _only(views, ("regions", "spots"), where)
            for k, f in views.get("regions", {}).items():
                doc.session_cal[f"{SESSION}.{k}"] = _frac(f, f"{where}.regions.{k}")
            for k, p in views.get("spots", {}).items():
                doc.spots[f"{SESSION}.{k}"] = _point(p, f"{where}.spots.{k}")
            continue
        for vid, t in _tables(views, where).items():
            doc.calibration[(_id(aid, where), _id(vid, where))] = _calibration(t, f"{where}.{vid}")
    return doc


@cache
def builtin() -> tuple[Doc, ...]:
    """The definitions shipped in jharokha/apps/."""
    root = resources.files("jharokha") / "apps"
    files = sorted((f for f in root.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name)
    return tuple(
        parse(f.read_text(encoding="utf-8"), f"built-in {f.name}", "built-in") for f in files
    )


# The catalog: every file merged, calibration applied


@dataclass(frozen=True)
class Catalog:
    apps: dict[str, App]
    calibration: dict[tuple[str, str], Calibration]
    session: dict[str, Region]
    session_cal: dict[str, Fraction]
    spots: dict[str, Point2]

    def knows(self, target: str) -> bool:
        """target is an app id or 'app.view', as input tools' expect takes it."""
        app, _, view = target.partition(".")
        return app in self.apps and (not view or view in self.apps[app].views)

    def views(self) -> list[str]:
        return [f"{a.id}.{v}" for a in self.apps.values() for v in a.views]

    def spots_for(self, app: str | None) -> dict[str, Spot]:
        """The session's spots, and those of app (the one in front) by `app.name`: an app's
        icon is a target only while its app is on screen."""
        out: dict[str, Spot] = dict(self.spots)
        a = self.apps.get(app or "")
        if a is not None:
            out.update({f"{a.id}.{k}": p for k, p in a.spots.items()})
        return out

    def policies(self) -> dict[str, Policy]:
        """Each app's input policy, by app id."""
        return {a.id: a.input_policy for a in self.apps.values()}

    def region_names(self) -> list[str]:
        names = [
            f"{a.id}.{r}" for a in self.apps.values() for v in a.views.values() for r in v.regions
        ]
        return list(dict.fromkeys(names + list(self.session_cal) + list(self.session)))

    def variant(
        self, app: str, view: str, found: Mapping[str, Box | None], width: int, height: int
    ) -> Variant | None:
        cal = self.calibration.get((app, view))
        return choose(cal.variants, found, width, height) if cal else None

    def region_box(
        self,
        app: str,
        view: str,
        name: str,
        panes: list[Box],
        found: Mapping[str, Box | None],
        width: int,
        height: int,
    ) -> tuple[Box, str] | None:
        """Where a region is now, and how that was decided: calibrated (variant), pane or
        box. None when it cannot be placed."""
        var = self.variant(app, view, found, width, height)
        if var and name in var.regions:
            how = f"calibrated {var.name}" if var.name else "calibrated"
            return to_box(var.regions[name], width, height), how
        reg = self.apps[app].views[view].regions.get(name)
        return place(reg, panes, width, height) if reg else None

    def nested(
        self,
        app: str,
        view: str,
        name: str,
        panes: list[Box],
        found: Mapping[str, Box | None],
        width: int,
        height: int,
    ) -> list[Box]:
        """Where the view's other regions lie inside region name (a compose box in a
        thread's pane): their lines are not the region's items."""
        outer = self.region_box(app, view, name, panes, found, width, height)
        if outer is None:
            return []
        out = []
        for other in self.apps[app].views[view].regions:
            placed = self.region_box(app, view, other, panes, found, width, height)
            if other == name or placed is None:
                continue
            b = placed[0]
            if b[2] * b[3] < outer[0][2] * outer[0][3] and _covered(b, outer[0]) >= 0.9:
                out.append(b)
        return out

    def grid(
        self, app: str, view: str, name: str, found: Mapping[str, Box | None], w: int, h: int
    ) -> Grid:
        var = self.variant(app, view, found, w, h)
        return var.grids.get(name, Grid()) if var else Grid()

    def session_box(self, name: str, panes: list[Box], w: int, h: int) -> tuple[Box, str] | None:
        if name in self.session_cal:
            return to_box(self.session_cal[name], w, h), "calibrated"
        reg = self.session.get(name)
        return place(reg, panes, w, h) if reg else None


def place(reg: Region, panes: list[Box], width: int, height: int) -> tuple[Box, str] | None:
    fallback = to_box(reg.box, width, height) if reg.box else None
    if reg.at and (b := pane_at(panes, reg.at, reg.to, width, height)):
        if fallback is None or iou(b, fallback) >= PANE_FIT:
            return b, "pane"
    return (fallback, "box") if fallback else None


def _covered(inner: Box, outer: Box) -> float:
    """The share of inner's area inside outer."""
    ix = max(0, min(inner[0] + inner[2], outer[0] + outer[2]) - max(inner[0], outer[0]))
    iy = max(0, min(inner[1] + inner[3], outer[1] + outer[3]) - max(inner[1], outer[1]))
    return ix * iy / max(1, inner[2] * inner[3])


def pane_at(panes: list[Box], at: Point2, to: Point2 | None, width: int, height: int) -> Box | None:
    """The pane under at, widened to the pane under to."""

    def under(p: Point2) -> Box | None:
        x, y = p[0] * width, p[1] * height
        return next((b for b in panes if b[0] <= x < b[0] + b[2] and b[1] <= y < b[1] + b[3]), None)

    a = under(at)
    b = under(to) if to else a
    if a is None or b is None:
        return None
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _calibrated(app: App, vid: str, cal: Calibration) -> App:
    """app with what calibration adds to a view: regions measured that the definition does
    not name, readers, focus and prompt set there, and its anchors as cues where they were
    measured."""
    if vid not in app.views:
        known = ", ".join(app.views)
        raise AppError(f"calibration.{app.id}.{vid}: {app.id} has no view '{vid}' ({known})")
    view = app.views[vid]
    regions = dict(view.regions)
    for name in dict.fromkeys(k for v in cal.variants for k in v.regions):
        regions.setdefault(name, Region(name))
    for name, reader in cal.readers.items():
        if name in regions:
            reader, _, kind = reader.partition(":")
            regions[name] = replace(regions[name], reader=reader, kind=kind or None)
    if cal.focus:
        if cal.focus not in regions:
            raise AppError(f"calibration.{app.id}.{vid}.focus: no region '{cal.focus}'")
        regions = {k: replace(r, focus=k == cal.focus) for k, r in regions.items()}
    app_cues, view_cues = list(app.cues), list(view.cues)
    for anchor in dict.fromkeys(v.anchor for v in cal.variants if v.anchor):
        assert anchor is not None
        at = tuple(v.anchor_at for v in cal.variants if v.anchor == anchor and v.anchor_at)
        for cues in (app_cues, view_cues):
            i = next((i for i, c in enumerate(cues) if _same_text(c, anchor)), None)
            if i is not None:
                c = cues[i]
                cues[i] = replace(c, text=(anchor, *c.text), at=at or c.at)
                break
        else:
            # A measured anchor is the strongest evidence a local setup has.
            view_cues.append(Cue(text=(anchor,), at=at, weight=2.0))
    view = replace(view, regions=regions, cues=tuple(view_cues))
    return replace(app, cues=tuple(app_cues), views={**app.views, vid: view})


def _same_text(c: Cue, anchor: str) -> bool:
    return any(t.casefold() == anchor.casefold() for t in c.text)


def catalog(*docs: Doc) -> Catalog:
    """Built-in definitions, then each doc in order: a later app replaces an earlier one
    of the same id, later calibration replaces earlier calibration of the same view."""
    apps: dict[str, App] = {}
    cal: dict[tuple[str, str], Calibration] = {}
    session: dict[str, Region] = {}
    session_cal: dict[str, Fraction] = {}
    spots: dict[str, Point2] = {}
    for d in (*builtin(), *docs):
        apps.update(d.apps)
        cal.update(d.calibration)
        session.update(d.session)
        session_cal.update(d.session_cal)
        spots.update(d.spots)
    for (aid, vid), c in cal.items():
        if aid not in apps:
            raise AppError(f"calibration.{aid}.{vid}: no app '{aid}' is defined")
        apps[aid] = _calibrated(apps[aid], vid, c)
    return Catalog(apps, cal, session, session_cal, spots)


def load(path: Path | None = None) -> Catalog:
    """Built-ins, then apps.toml, which wins."""
    docs = [parse(path.read_text(encoding="utf-8"), str(path))] if path is not None else []
    return catalog(*docs)
