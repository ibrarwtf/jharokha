"""Plain data shared by core, win and server. No Windows, no MCP."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from PIL import Image

Box = tuple[int, int, int, int]  # x, y, w, h in client pixels
Point = tuple[int, int]  # x, y in client pixels


@dataclass(frozen=True)
class Word:
    text: str
    box: Box


@dataclass(frozen=True)
class Line:
    text: str
    box: Box
    words: tuple[Word, ...] = field(default_factory=tuple)


def union(boxes: Iterable[Box]) -> Box:
    bs = list(boxes)
    x1 = min(b[0] for b in bs)
    y1 = min(b[1] for b in bs)
    x2 = max(b[0] + b[2] for b in bs)
    y2 = max(b[1] + b[3] for b in bs)
    return (x1, y1, x2 - x1, y2 - y1)


def within(inner: Box, outer: Box) -> bool:
    """Whether inner lies wholly inside outer."""
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[0] + inner[2] <= outer[0] + outer[2]
        and inner[1] + inner[3] <= outer[1] + outer[3]
    )


def centre_in(b: Box, box: Box) -> bool:
    """Whether b's centre lies in box."""
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    return box[0] <= cx < box[0] + box[2] and box[1] <= cy < box[1] + box[3]


def inside(lines: list[Line], box: Box) -> list[Line]:
    """Lines whose centre is in box."""
    return [ln for ln in lines if centre_in(ln.box, box)]


def rows(lines: list[Line]) -> list[list[Line]]:
    """Lines on one screen row (centres closer than half a line height), left to right."""
    out: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: ln.box[1] + ln.box[3] / 2):
        cy = ln.box[1] + ln.box[3] / 2
        if out:
            r = out[-1][0]
            if abs(cy - (r.box[1] + r.box[3] / 2)) < max(r.box[3], ln.box[3]) / 2:
                out[-1].append(ln)
                continue
        out.append([ln])
    return [sorted(r, key=lambda ln: ln.box[0]) for r in out]


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes."""
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / max(1, a[2] * a[3] + b[2] * b[3] - inter)


@dataclass(frozen=True)
class Frame:
    """One capture of the client area, raw BGRA, row-major, no padding."""

    width: int
    height: int
    bgra: bytes
    frame_id: int
    ts: float

    def image(self) -> Image.Image:
        """RGBA view over the buffer, no copy."""
        return Image.frombuffer("RGBA", (self.width, self.height), self.bgra, "raw", "BGRA", 0, 1)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str
    minimized: bool
    foreground: bool
    client_size: tuple[int, int]
    dpi_scale: float
