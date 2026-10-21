"""Named regions from a layouts file: per app, boxes as fractions of the client area, so one
file fits any window size. An optional anchor (text that must be on screen) keeps a Teams
layout from being used on an Outlook screen."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from jharokha.core.models import Box

Fraction = tuple[float, float, float, float]


@dataclass(frozen=True)
class Layout:
    name: str
    anchor: str | None
    regions: dict[str, Fraction]


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
        regions: dict[str, Fraction] = {}
        for key, v in table.items():
            if key == "anchor":
                continue
            ok = isinstance(v, list) and len(v) == 4
            ok = ok and all(isinstance(f, int | float) and 0 <= f <= 1 for f in v)
            if not ok or v[0] + v[2] > 1.001 or v[1] + v[3] > 1.001 or not (v[2] and v[3]):
                raise LayoutError(
                    f"{source}: {name}.{key} must be [x, y, width, height] as fractions 0..1 "
                    "of the client area that stay inside it"
                )
            regions[key] = (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
        layouts[name] = Layout(name, anchor, regions)
    return layouts


def load(path: Path) -> dict[str, Layout]:
    return parse(path.read_text(encoding="utf-8"), str(path))


def to_box(f: Fraction, width: int, height: int) -> Box:
    x, y = round(f[0] * width), round(f[1] * height)
    return (x, y, min(round(f[2] * width), width - x), min(round(f[3] * height), height - y))
