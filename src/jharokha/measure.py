"""`jharokha measure`: write an app's named regions to the layouts file, from what is on screen.

With --zoom it also steps the app's zoom (Ctrl+0, Ctrl+=, Ctrl+-) and writes one variant per
step, then puts the zoom back where it was. Those keys are the only input it sends."""

from __future__ import annotations

import re
import time
from pathlib import Path

from jharokha.core import layout
from jharokha.core.models import Box
from jharokha.core.screen import Screen, ScreenError
from jharokha.core.text import find

_SETTLE_S = 1.5  # the app re-lays itself out after a zoom key


def run(
    screen: Screen,
    window_title: str | None,
    app: str,
    anchor: str,
    path: Path,
    zoom: str | None,
    names: str | None,
    focus: str | None = None,
    grid: str | None = None,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", app):
        raise SystemExit("measure: app must be letters, digits, - or _")
    steps = _zoom_steps(zoom) if zoom else []
    seen: dict[int, tuple[dict[str, Box], tuple[float, float]]] = {}
    size = (0, 0)

    def snap(step: int | None) -> tuple[float, float]:
        nonlocal size
        frame, lines = screen.read(window_title, None)
        size = (frame.width, frame.height)
        m = find(lines, anchor)
        if not m:
            raise ScreenError("layout_mismatch", f"'{anchor}' is not on screen")
        at = (m[0].box[0] / frame.width, m[0].box[1] / frame.height)
        if step is not None:
            seen[step] = (layout.measure(lines, frame.width, frame.height), at)
        return at

    def press(combo: str, times: int = 1) -> None:
        for _ in range(times):
            screen.source.press(screen.window(window_title), combo)
            time.sleep(0.3)
        time.sleep(_SETTLE_S)

    start = snap(None if steps else 0)
    original = 0
    try:
        cur: int | None = None
        for step in steps:
            if cur is not None and abs(step - cur) == 1:
                press("ctrl+=" if step > cur else "ctrl+-")
            else:
                press("ctrl+0")
                if step:
                    press("ctrl+=" if step > 0 else "ctrl+-", abs(step))
            cur = step
            snap(step)
    finally:
        if steps and seen:
            original = min(seen, key=lambda s: _dist(seen[s][1], start))
            press("ctrl+0")
            if original:
                press("ctrl+=" if original > 0 else "ctrl+-", abs(original))

    keys = {s: list(r) for s, (r, _) in seen.items()}
    if len({tuple(k) for k in keys.values()}) > 1:
        for s, k in sorted(keys.items()):
            print(f"  step {s:+d}: {', '.join(k)}")
        raise SystemExit("measure: zoom steps found different regions; nothing written")
    found = next(iter(keys.values()))
    rename = dict(zip(found, names.split(","), strict=True)) if names else {}
    if names and len(rename) != len(found):
        raise SystemExit(f"measure: --names needs {len(found)} names for {', '.join(found)}")

    w, h = size
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    # Remeasuring keeps the focus and grid set earlier, by hand or with --focus and --grid.
    before = [la for la in layout.parse(old, str(path)).values() if la.app == app]
    profile = {}
    for key, given in (("focus", focus), ("grid", grid)):
        value = given or next((getattr(la, key) for la in before if getattr(la, key)), None)
        if value and value not in {rename.get(k, k) for k in found}:
            raise SystemExit(f"measure: {key} {value} is not one of the regions found")
        if value:
            profile[key] = value
    tables = []
    for s, (regions, at) in sorted(seen.items(), reverse=True):
        name = f"{app}@{s:+d}" if steps else app
        fr = {rename.get(k, k): layout.to_fraction(b, w, h) for k, b in regions.items()}
        tables.append(layout.table(name, anchor, fr, at if steps else None, profile))
        print(f"{name}: " + "  ".join(f"{rename.get(k, k)}={list(b)}" for k, b in regions.items()))
    head = f"# {app}: written by `jharokha measure` at {w}x{h} client"
    path.write_text(layout.replace_app(old, app, head + "\n" + "\n".join(tables)), encoding="utf-8")
    layout.load(path)  # what we wrote must read back

    regions, _ = seen[original]
    print(f"zoom back at step {original:+d}; reading each region there:")
    for k, b in regions.items():
        _, lines = screen.read(window_title, b)
        print(f"  {app}.{rename.get(k, k):10} {len(lines):3} lines")
    print(f"wrote {path}")


def _zoom_steps(spec: str) -> list[int]:
    m = re.fullmatch(r"(-?\d+)\.\.\+?(\d+)", spec)
    if not m or int(m.group(1)) > 0:
        raise SystemExit("measure: --zoom is LOW..HIGH around 0, e.g. --zoom=-5..2")
    lo, hi = int(m.group(1)), int(m.group(2))
    return [0, *range(1, hi + 1), *range(-1, lo - 1, -1)]


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
