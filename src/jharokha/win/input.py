"""Input to the client window. Only loaded when the server runs with --allow-input.

The RDP client forwards input only while it is the foreground window, so every action
brings it to the front, parks the cursor where needed, and restores both afterwards.
Keys go as scan codes: that is what the client translates for the session."""

from __future__ import annotations

import ctypes
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
WHEEL_DELTA = 120
GA_ROOT = 2
BUTTONS = {  # down, up flags
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
KEYS = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "win": 0x5B,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "menu": 0x5D,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D}


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


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


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
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short


class Covered(RuntimeError):
    pass


class NotForeground(RuntimeError):
    pass


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


@contextmanager
def _active(hwnd: int, x: int | None = None, y: int | None = None) -> Iterator[None]:
    prev = user32.GetForegroundWindow()
    _bring_to_front(hwnd)
    time.sleep(0.1)
    old = _POINT()
    user32.GetCursorPos(ctypes.byref(old))
    try:
        if x is not None and y is not None:
            pt = _POINT(x, y)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            if user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT) != hwnd:
                raise Covered("another window covers that point")
            user32.SetCursorPos(pt.x, pt.y)
        yield
    finally:
        user32.SetCursorPos(old.x, old.y)
        if prev and prev != hwnd:
            _bring_to_front(prev)


def _send(*inputs: _INPUT) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _mouse(flags: int, data: int = 0) -> _INPUT:
    i = _INPUT(type=INPUT_MOUSE)
    i.u.mi.dwFlags = flags
    i.u.mi.mouseData = data & 0xFFFFFFFF
    return i


def _key(vk: int, up: bool) -> _INPUT:
    i = _INPUT(type=INPUT_KEYBOARD)
    i.u.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    i.u.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if vk in EXTENDED:
        i.u.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY
    return i


def _unicode(ch: str, up: bool) -> _INPUT:
    i = _INPUT(type=INPUT_KEYBOARD)
    i.u.ki.wScan = ord(ch)
    i.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return i


def scroll(hwnd: int, x: int, y: int, ticks: int) -> None:
    """Wheel ticks at client point (x, y); negative scrolls down."""
    step = WHEEL_DELTA if ticks > 0 else -WHEEL_DELTA
    with _active(hwnd, x, y):
        for _ in range(abs(ticks)):
            _send(_mouse(MOUSEEVENTF_WHEEL, step))
            time.sleep(0.03)


def click(hwnd: int, x: int, y: int, button: str = "left", double: bool = False) -> None:
    down, up = BUTTONS[button]
    with _active(hwnd, x, y):
        for _ in range(2 if double else 1):
            _send(_mouse(down), _mouse(up))
            time.sleep(0.05)


def _ensure_foreground(hwnd: int, click_focus: bool) -> None:
    """Keys go to whatever window is in the foreground, so none is sent unless that is the
    session. Windows may refuse SetForegroundWindow; a real click in the client, at the point
    _active parked the cursor on, activates it where that fails."""
    if user32.GetForegroundWindow() == hwnd:
        return
    if click_focus:
        down, up = BUTTONS["left"]
        _send(_mouse(down), _mouse(up))
        time.sleep(0.3)
    if user32.GetForegroundWindow() != hwnd:
        raise NotForeground("the session did not take the foreground, so nothing was sent")


def type_text(hwnd: int, text: str, focus_at: tuple[int, int] | None = None) -> None:
    """Characters on the current keyboard layout go as scan codes (with shift when needed),
    anything else as a unicode event. focus_at is a client point to click when the session
    does not come to the front by itself."""
    x, y = focus_at or (None, None)
    with _active(hwnd, x, y):
        _ensure_foreground(hwnd, focus_at is not None)
        for ch in text:
            if user32.GetForegroundWindow() != hwnd:
                raise NotForeground("focus left the session while typing; the rest was not sent")
            if ch == "\n":
                _send(_key(KEYS["enter"], False), _key(KEYS["enter"], True))
                continue
            vk = user32.VkKeyScanW(ch)
            if vk == -1 or (vk >> 8) & 0x6:  # not on the layout, or needs ctrl/alt
                _send(_unicode(ch, False), _unicode(ch, True))
                continue
            shift = (vk >> 8) & 0x1
            seq = [_key(vk & 0xFF, False), _key(vk & 0xFF, True)]
            if shift:
                seq = [_key(KEYS["shift"], False), *seq, _key(KEYS["shift"], True)]
            _send(*seq)
            time.sleep(0.01)


def press(hwnd: int, combo: str, focus_at: tuple[int, int] | None = None) -> None:
    """A chord like 'ctrl+shift+esc' or 'enter'. Single characters are keys ('ctrl+c')."""
    vks = []
    for part in combo.lower().split("+"):
        if part in KEYS:
            vks.append(KEYS[part])
        elif len(part) == 1 and (vk := user32.VkKeyScanW(part)) != -1:
            vks.append(vk & 0xFF)
        else:
            raise ValueError(f"unknown key '{part}'; use names like enter, esc, ctrl, f5")
    x, y = focus_at or (None, None)
    with _active(hwnd, x, y):
        _ensure_foreground(hwnd, focus_at is not None)
        _send(*[_key(vk, False) for vk in vks])
        time.sleep(0.03)
        _send(*[_key(vk, True) for vk in reversed(vks)])
