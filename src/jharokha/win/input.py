"""Input to the client window. Only loaded when the server runs with --allow-input."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
GA_ROOT = 2


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", _MOUSEINPUT)]


user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [_POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]


class Covered(RuntimeError):
    pass


def scroll(hwnd: int, x: int, y: int, ticks: int) -> None:
    """Wheel ticks at client point (x, y); negative scrolls down. The wheel goes to whatever
    is under the cursor, so the cursor is parked there for the duration and put back after;
    if another window covers that point, nothing is sent."""
    pt = _POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    if user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT) != hwnd:
        raise Covered("another window covers that point")
    old = _POINT()
    user32.GetCursorPos(ctypes.byref(old))
    user32.SetCursorPos(pt.x, pt.y)
    try:
        step = WHEEL_DELTA if ticks > 0 else -WHEEL_DELTA
        inp = _INPUT(type=INPUT_MOUSE)
        inp.mi.mouseData = step & 0xFFFFFFFF
        inp.mi.dwFlags = MOUSEEVENTF_WHEEL
        for _ in range(abs(ticks)):
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(0.03)
    finally:
        user32.SetCursorPos(old.x, old.y)
