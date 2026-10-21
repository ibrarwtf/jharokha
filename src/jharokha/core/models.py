"""Plain data shared by core, win and server. No Windows, no MCP."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from PIL import Image

Box = tuple[int, int, int, int]  # x, y, w, h in client pixels


def union(boxes: Iterable[Box]) -> Box:
    bs = list(boxes)
    x1 = min(b[0] for b in bs)
    y1 = min(b[1] for b in bs)
    x2 = max(b[0] + b[2] for b in bs)
    y2 = max(b[1] + b[3] for b in bs)
    return (x1, y1, x2 - x1, y2 - y1)


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
class Word:
    text: str
    box: Box


@dataclass(frozen=True)
class Line:
    text: str
    box: Box
    words: tuple[Word, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str
    minimized: bool
    foreground: bool
    client_size: tuple[int, int]
    dpi_scale: float
