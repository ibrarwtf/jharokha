"""Plain data shared by core, win and server. No Windows, no MCP."""

from __future__ import annotations

from dataclasses import dataclass, field

Box = tuple[int, int, int, int]  # x, y, w, h in client pixels


@dataclass(frozen=True)
class Frame:
    """One capture of the client area, raw BGRA, row-major, no padding."""

    width: int
    height: int
    bgra: bytes
    frame_id: int
    ts: float


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
