"""Boxes as fractions of the client area, so one calibration fits any window size, and zoom
variants chosen by where their anchor text sits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, TypeVar

from jharokha.core.models import Box

Fraction = tuple[float, float, float, float]


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


def to_box(f: Fraction, width: int, height: int) -> Box:
    x, y = round(f[0] * width), round(f[1] * height)
    return (x, y, min(round(f[2] * width), width - x), min(round(f[3] * height), height - y))


def to_fraction(box: Box, width: int, height: int) -> Fraction:
    x, y, w, h = box
    return (x / width, y / height, w / width, h / height)
