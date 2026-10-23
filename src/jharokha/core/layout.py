"""Named regions from a layouts file: per app, boxes as fractions of the client area, so one
file fits any window size. An optional anchor (text that must be on screen) keeps a Teams
layout from being used on an Outlook screen."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jharokha.core.models import Box, Line
from jharokha.core.text import panels

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
    # A regex for a shell's prompt at the start of a line, for jharokha_run.
    prompt: str | None = None

    @property
    def app(self) -> str:
        return self.name.split("@")[0]


def choose(
    group: list[Layout], found: dict[str, Box | None], width: int, height: int
) -> Layout | None:
    """The variant whose anchor is on screen and nearest to where it was measured; a
    variant without an anchor always applies."""
    best: tuple[float, Layout] | None = None
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


_EDGE = 0.15  # top and bottom bars live in the outer 15% of the height


def measure(lines: list[Line], width: int, height: int) -> dict[str, Box]:
    """Regions read off one screen: columns at the edges of full-height panels (col1, col2,
    ...), and the widest column as body (its tallest panel) with what lies above it as top
    and the separate bars at its foot (a compose box, a taskbar) as bottom1, bottom2, ..."""
    cells = panels(lines, (0, 0, width, height))
    edges = {0, width}
    for (x, _, w, h), _ in cells:
        if h >= 0.6 * height:
            edges |= {x, x + w}
    xs = sorted(edges)
    cols = list(zip(xs, xs[1:], strict=False))
    wide = max(cols, key=lambda c: c[1] - c[0])
    regions: dict[str, Box] = {}
    n = 0
    for x0, x1 in cols:
        if (x0, x1) != wide:
            n += 1
            regions[f"col{n}"] = (x0, 0, x1 - x0, height)
            continue
        inside = [(b, ls) for b, ls in cells if x0 <= b[0] and b[0] + b[2] <= x1]
        top = max(inside, key=lambda c: c[0][3])[0][1] if inside else 0
        main = [ln for ln in lines if x0 <= ln.box[0] + ln.box[2] / 2 < x1 and ln.box[1] >= top]
        bands = _bands(main, top, height)

        # Bars above the body that panels did not already split off belong to the top.
        i, j = 0, len(bands)
        while i < j - 1 and max(_tops(main, bands[i])) <= _EDGE * height:
            i += 1
        while j - 1 > i and min(_tops(main, bands[j - 1])) >= (1 - _EDGE) * height:
            j -= 1
        foot = bands[j:]
        start, end = bands[i][0], bands[j - 1][1]
        if start:
            regions["top"] = (x0, 0, x1 - x0, start)
        regions["body"] = (x0, start, x1 - x0, end - start)
        for i, (y0, y1) in enumerate(foot, 1):
            regions[f"bottom{i}"] = (x0, y0, x1 - x0, y1 - y0)
    return regions


def _tops(lines: list[Line], band: tuple[int, int]) -> list[int]:
    return [ln.box[1] for ln in lines if band[0] <= ln.box[1] < band[1]] or [band[0]]


def _bands(lines: list[Line], y0: int, y1: int) -> list[tuple[int, int]]:
    """y0..y1 split at the middle of every blank band of 1.5 line heights or more."""
    if not lines:
        return [(y0, y1)]
    hs = sorted(ln.box[3] for ln in lines)
    gap = 1.5 * hs[len(hs) // 2]
    spans = sorted((ln.box[1], ln.box[1] + ln.box[3]) for ln in lines)
    cuts, end = [y0], spans[0][1]
    for start, stop in spans[1:]:
        if start - end >= gap:
            cuts.append((start + end) // 2)
        end = max(end, stop)
    cuts.append(y1)
    return list(zip(cuts, cuts[1:], strict=False))


def to_fraction(box: Box, width: int, height: int) -> Fraction:
    x, y, w, h = box
    return (x / width, y / height, w / width, h / height)


def table(
    name: str,
    anchor: str,
    regions: dict[str, Fraction],
    at: tuple[float, float] | None,
    profile: dict[str, str] | None = None,
) -> str:
    """One TOML table, in the format parse() reads. profile holds focus and grid."""

    def nums(v: tuple[float, ...]) -> str:
        return "[" + ", ".join(f"{f:.4f}".rstrip("0").rstrip(".") or "0" for f in v) + "]"

    out = [f'["{name}"]' if "@" in name else f"[{name}]", f"anchor = {json.dumps(anchor)}"]
    if at:
        out.append(f"anchor_at = {nums(at)}")
    out += [f"{k} = {json.dumps(v)}" for k, v in (profile or {}).items()]
    out += [f"{k} = {nums(f)}" for k, f in regions.items()]
    return "\n".join(out) + "\n"


_HEADER = re.compile(r'^\[\s*"?([^"\]]+?)"?\s*\]\s*$')


def replace_app(text: str, app: str, tables: str) -> str:
    """text with every table of app (app and app@...) dropped and tables appended."""
    keep, drop = [], False
    for line in text.splitlines():
        if m := _HEADER.match(line.strip()):
            drop = m.group(1).split("@")[0] == app
        if not drop:
            keep.append(line)
    body = "\n".join(keep).rstrip()
    return (body + "\n\n" if body else "") + tables
