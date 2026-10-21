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
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32 = ctypes.windll.kernel32


def _bring_to_front(hwnd: int) -> None:
    """A process without a window may not take the foreground; attaching to the current
    foreground thread lifts that restriction."""
    if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
        return
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(me, fg_thread, False)


class Covered(RuntimeError):
    pass


def scroll(hwnd: int, x: int, y: int, ticks: int) -> None:
    """Wheel ticks at client point (x, y); negative scrolls down. The RDP client forwards
    input only while it is the foreground window, so it is brought to the front, the cursor
    parked over the point, and both put back after. If another window still covers the point,
    nothing is sent."""
    pt = _POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    prev = user32.GetForegroundWindow()
    _bring_to_front(hwnd)
    time.sleep(0.1)
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
        if prev and prev != hwnd:
            _bring_to_front(prev)
