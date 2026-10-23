"""The layouts file of v0.6 to v0.15: per app, boxes as fractions of the client area, so one
file fits any window size, with an optional anchor (text that must be on screen). It is
read as calibration of the app model (core.apps.from_layouts). Also the geometry both
share: zoom variants chosen by where their anchor is, and fractions to pixels."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from jharokha.core.models import Box

Fraction = tuple[float, float, float, float]


@dataclass(frozen=True)
class Layout:
    name: str
    anchor: str | None
    regions: dict[str, Fraction]
    # Where the anchor sits (x, y fractions) in this variant. `app@variant` layouts of one
    # app, e.g. one per zoom level, are told apart by it.
    anchor_at: tuple[float, float] | None = None
    # The region input tools report changes in while this app is on screen: the thread,
    # not the chat list beside it.
    focus: str | None = None
    # A region that is a character grid (a terminal's body), read row by row on it.
    grid: str | None = None
    # A regex for a shell's prompt at the start of a line.
    prompt: str | None = None

    @property
    def app(self) -> str:
        return self.name.split("@")[0]


class Anchored(Protocol):
    @property
    def anchor(self) -> str | None: ...
    @property
    def anchor_at(self) -> tuple[float, float] | None: ...


A = TypeVar("A", bound=Anchored)


def choose(
    group: Sequence[A], found: Mapping[str, Box | None], width: int, height: int
) -> A | None:
    """The variant whose anchor is on screen and nearest to where it was measured; a
    variant without an anchor always applies."""
    best: tuple[float, A] | None = None
    for lay in group:
        box = found.get(lay.anchor) if lay.anchor else None
        if lay.anchor and box is None:
            continue
        d = 0.0
        if box and lay.anchor_at:
            d = (box[0] / width - lay.anchor_at[0]) ** 2 + (box[1] / height - lay.anchor_at[1]) ** 2
        if best is None or d < best[0]:
            best = (d, lay)
    return best[1] if best else None


class LayoutError(ValueError):
    pass


def parse(text: str, source: str = "layouts") -> dict[str, Layout]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise LayoutError(f"{source}: {e}") from e
    layouts = {}
    for name, table in data.items():
        if not isinstance(table, dict):
            raise LayoutError(f"{source}: [{name}] must be a table")
        anchor = table.get("anchor")
        if anchor is not None and not isinstance(anchor, str):
            raise LayoutError(f"{source}: {name}.anchor must be text")
        at = table.get("anchor_at")
        if at is not None and not (
            anchor and isinstance(at, list) and len(at) == 2 and all(0 <= f <= 1 for f in at)
        ):
            raise LayoutError(f"{source}: {name}.anchor_at must be [x, y] fractions, with anchor")
        regions: dict[str, Fraction] = {}
        for key, v in table.items():
            if key in ("anchor", "anchor_at", "focus", "grid", "prompt"):
                continue
            ok = isinstance(v, list) and len(v) == 4
            ok = ok and all(isinstance(f, int | float) and 0 <= f <= 1 for f in v)
            if not ok or v[0] + v[2] > 1.001 or v[1] + v[3] > 1.001 or not (v[2] and v[3]):
                raise LayoutError(
                    f"{source}: {name}.{key} must be [x, y, width, height] as fractions 0..1 "
                    "of the client area that stay inside it"
                )
            regions[key] = (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
        focus, grid = table.get("focus"), table.get("grid")
        for key, v in (("focus", focus), ("grid", grid)):
            if v is not None and (not anchor or v not in regions):
                raise LayoutError(
                    f"{source}: {name}.{key} must name one of its regions, with anchor"
                )
        prompt = table.get("prompt")
        if prompt is not None:
            try:
                re.compile(prompt)
            except (re.error, TypeError) as e:
                raise LayoutError(f"{source}: {name}.prompt must be a regex: {e}") from e
        at_xy = (float(at[0]), float(at[1])) if at else None
        layouts[name] = Layout(name, anchor, regions, at_xy, focus, grid, prompt)
    return layouts


def load(path: Path) -> dict[str, Layout]:
    return parse(path.read_text(encoding="utf-8"), str(path))


def to_box(f: Fraction, width: int, height: int) -> Box:
    x, y = round(f[0] * width), round(f[1] * height)
    return (x, y, min(round(f[2] * width), width - x), min(round(f[3] * height), height - y))


def to_fraction(box: Box, width: int, height: int) -> Fraction:
    x, y, w, h = box
    return (x / width, y / height, w / width, h / height)
