"""Draw a generated screen into a Frame, the way an app in the session would.

`gdi` draws with Windows GDI and ClearType, as real apps do; `pil` draws plain greyscale
antialiased text, as the first evals did. `codec` then passes the frame through lossy
compression with 4:2:0 chroma, as the RDP client's video codecs do to what it receives.
Each renderer also measures text, so a screen can say where a word is to the pixel."""

from __future__ import annotations

import ctypes
import io
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from jharokha.core.models import Box, Frame

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Text:
    x: int
    y: int
    text: str
    font: str = "Segoe UI"
    px: int = 16
    color: RGB = (0, 0, 0)
    bold: bool = False


@dataclass
class Find:
    """A query an agent would make, and the drawn text it should land on: texts[index],
    from character start, length characters (default: as long as the query)."""

    query: str
    index: int
    start: int = 0
    regex: bool = False
    length: int | None = None


@dataclass
class Screen:
    name: str
    app: str
    width: int
    height: int
    bg: RGB
    texts: list[Text]
    truth: list[str]  # the lines in the order a reader should get them
    finds: list[Find] = field(default_factory=list)
    locked: bool = False
    grid: Box | None = None  # the region a layouts file would mark as a terminal grid
    # Filled boxes drawn before the text, in order: pane backgrounds and divider lines.
    rects: list[tuple[Box, RGB]] = field(default_factory=list)
    panes: list[Box] = field(default_factory=list)  # the true panes, as drawn
    view: str | None = None  # the app's view it shows, as the built-in definitions name it
    # What each named region's reader should return (evals.items), by region name.
    items: dict[str, dict[str, Any]] = field(default_factory=dict)


class FontMissing(RuntimeError):
    pass


Measure = Callable[[Text, str], tuple[int, int]]


def render(screen: Screen, engine: str = "gdi", codec: bool = False) -> tuple[Frame, list[Box]]:
    """The frame, and the box of each find's target text."""
    draw = _gdi if engine == "gdi" else _pil
    boxes: list[Box] = []

    def locate(measure: Measure) -> None:
        for f in screen.finds:
            t = screen.texts[f.index]
            n = f.length if f.length is not None else len(f.query)
            x0 = measure(t, t.text[: f.start])[0]
            w, h = measure(t, t.text[f.start : f.start + n])
            boxes.append((t.x + x0, t.y, w, h))

    img = draw(screen, locate)
    if codec:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=80, subsampling=2)
        img = Image.open(buf).convert("RGBA")
    frame = Frame(screen.width, screen.height, img.tobytes("raw", "BGRA"), 0, time.time())
    return frame, boxes


# Cascadia Mono, Windows Terminal's font, is not installed with Windows; it is vendored here
# (SIL OFL, licence beside it) and loaded for this process only.
CASCADIA = Path(__file__).parent / "fonts" / "CascadiaMono-Regular.ttf"
_PIL_FONTS = {
    ("Segoe UI", False): "segoeui.ttf",
    ("Segoe UI", True): "seguisb.ttf",
    ("Consolas", False): "consola.ttf",
    ("Consolas", True): "consolab.ttf",
    ("Cascadia Mono", False): str(CASCADIA),
}


def _pil(screen: Screen, locate: Callable[[Measure], None]) -> Image.Image:
    img = Image.new("RGBA", (screen.width, screen.height), screen.bg)
    d = ImageDraw.Draw(img)
    for (x, y, w, h), color in screen.rects:
        d.rectangle((x, y, x + w - 1, y + h - 1), fill=color)
    fonts: dict[tuple[str, bool, int], ImageFont.FreeTypeFont] = {}

    def font(t: Text) -> ImageFont.FreeTypeFont:
        key = (t.font, t.bold, t.px)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(_PIL_FONTS[(t.font, t.bold)], t.px)
        return fonts[key]

    for t in screen.texts:
        d.text((t.x, t.y), t.text, fill=t.color, font=font(t))
    locate(lambda t, s: (round(font(t).getlength(s)), round(t.px * 1.3)))
    return img


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_CLEARTYPE_QUALITY, _TRANSPARENT = 5, 1


def _colorref(c: RGB) -> int:
    return c[0] | (c[1] << 8) | (c[2] << 16)


_FR_PRIVATE = 0x10


@cache
def _private_fonts() -> None:
    g = ctypes.windll.gdi32
    g.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    if not g.AddFontResourceExW(str(CASCADIA), _FR_PRIVATE, None):
        raise FontMissing(f"could not load {CASCADIA}")


def _gdi(screen: Screen, locate: Callable[[Measure], None]) -> Image.Image:
    _private_fonts()
    g, u = ctypes.windll.gdi32, ctypes.windll.user32
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.CreateFontW.restype = wintypes.HFONT
    g.CreateSolidBrush.restype = wintypes.HBRUSH
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    g.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    g.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
    g.GetTextExtentPoint32W.argtypes = [
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(wintypes.SIZE),
    ]
    g.GetTextFaceW.argtypes = [wintypes.HDC, ctypes.c_int, wintypes.LPWSTR]
    u.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]

    w, h = screen.width, screen.height
    dc = g.CreateCompatibleDC(None)
    bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = g.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = g.SelectObject(dc, bmp)
    fonts: dict[tuple[str, bool, int], int] = {}

    def font(t: Text) -> int:
        key = (t.font, t.bold, t.px)
        if key not in fonts:
            f = g.CreateFontW(
                -t.px,
                0,
                0,
                0,
                600 if t.bold else 400,
                0,
                0,
                0,
                1,
                0,
                0,
                _CLEARTYPE_QUALITY,
                0,
                t.font,
            )
            fonts[key] = f
            g.SelectObject(dc, f)
            face = ctypes.create_unicode_buffer(64)
            g.GetTextFaceW(dc, 64, face)
            if face.value.lower() != t.font.lower():
                raise FontMissing(f"{t.font} is not installed; GDI substituted {face.value}")
        return fonts[key]

    def measure(t: Text, s: str) -> tuple[int, int]:
        g.SelectObject(dc, font(t))
        size = wintypes.SIZE()
        g.GetTextExtentPoint32W(dc, s or "x", len(s or "x"), ctypes.byref(size))
        return (size.cx if s else 0), size.cy

    try:
        brush = g.CreateSolidBrush(_colorref(screen.bg))
        u.FillRect(dc, ctypes.byref(wintypes.RECT(0, 0, w, h)), brush)
        g.DeleteObject(brush)
        for (x, y, rw, rh), color in screen.rects:
            brush = g.CreateSolidBrush(_colorref(color))
            u.FillRect(dc, ctypes.byref(wintypes.RECT(x, y, x + rw, y + rh)), brush)
            g.DeleteObject(brush)
        g.SetBkMode(dc, _TRANSPARENT)
        for t in screen.texts:
            g.SelectObject(dc, font(t))
            g.SetTextColor(dc, _colorref(t.color))
            g.TextOutW(dc, t.x, t.y, t.text, len(t.text))
        g.GdiFlush()
        raw = ctypes.string_at(bits.value or 0, w * h * 4)
        locate(measure)
        return Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1).convert("RGBA")
    finally:
        g.SelectObject(dc, old)
        g.DeleteObject(bmp)
        for f in fonts.values():
            g.DeleteObject(f)
        g.DeleteDC(dc)
