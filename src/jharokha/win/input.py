"""Input to the client window. Only loaded when the server runs with --allow-input.

The RDP client forwards input only while it is the foreground window, so every action
brings it to the front, parks the cursor where needed, and restores both afterwards.
Named keys and chords go as scan codes, which the client translates for the session; typed
text goes as unicode events, which need no shift and no matching keyboard layout."""

from __future__ import annotations

import ctypes
import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from ctypes import wintypes
from typing import Protocol

from jharokha.core.guard import USER_IDLE_S, TypingInterrupted
from jharokha.win.guard import record_own_input

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
WHEEL_DELTA = 120
WHEEL_BATCH = 10  # ticks in one wheel event
KEY_S = 0.002  # between typed characters; the live session took them exact at none
PASTE_WAIT_S = 5.0  # for pasted text to arrive; the session fetches it from this machine
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
    "plus": 0xBB,  # the =/+ key: Ctrl+plus zooms in
    "equals": 0xBB,
    "minus": 0xBD,
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
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
user32.VkKeyScanW.restype = ctypes.c_short


class Covered(RuntimeError):
    pass


class NotForeground(RuntimeError):
    pass


class ClipboardBusy(RuntimeError):
    pass


class Seen(Protocol):
    def user_seen(self) -> float | None: ...


# Starts watching for the user's own input (win/guard.UserWatch) and stops on leaving.
Watch = Callable[[], AbstractContextManager[Seen]]


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


# The window held in front for a run of inputs (an action), and the one in front before it.
_held: dict[int, int] = {}


@contextmanager
def held(hwnd: int) -> Iterator[None]:
    """hwnd keeps the foreground across the inputs inside, and the window in front before
    them gets it back once, at the end: an action's thirty scrolls would otherwise switch
    the foreground sixty times, at 0.3 s each for the session to take it."""
    if hwnd in _held:
        yield
        return
    _held[hwnd] = user32.GetForegroundWindow()
    try:
        yield
    finally:
        prev = _held.pop(hwnd)
        if prev and prev != hwnd and user32.GetForegroundWindow() == hwnd:
            _bring_to_front(prev)


@contextmanager
def _active(
    hwnd: int, x: int | None = None, y: int | None = None, park: bool = True
) -> Iterator[None]:
    prev = user32.GetForegroundWindow()
    _bring_to_front(hwnd)
    # A client that was just activated can swallow the first click; give it time.
    time.sleep(0.1 if prev == hwnd else 0.3)
    old = _POINT()
    user32.GetCursorPos(ctypes.byref(old))
    moved = False
    try:
        if x is not None and y is not None:
            pt = _POINT(x, y)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            if user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT) != hwnd:
                raise Covered("another window covers that point")
            user32.SetCursorPos(pt.x, pt.y)
            moved = True
        yield
    finally:
        if moved and park:
            _park(hwnd)
        user32.SetCursorPos(old.x, old.y)
        if prev and prev != hwnd and hwnd not in _held:
            _bring_to_front(prev)


def _park(hwnd: int) -> None:
    """The session keeps the last cursor position it saw, so a cursor left on a link or a
    name keeps its tooltip or hover card on screen, in every later read. The middle of the
    right edge is a scrollbar in most apps: nothing pops up there."""
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = _POINT(rc.right - 2, rc.bottom // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    user32.SetCursorPos(pt.x, pt.y)
    time.sleep(0.05)  # for the client to send the move before the cursor leaves


def _send(*inputs: _INPUT) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    record_own_input()  # it moves the last-input clock as the user's keys do


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
    sign = 1 if ticks > 0 else -1
    with _active(hwnd, x, y):
        # One event carries several ticks (a free-spinning wheel does the same), so a long
        # scroll costs a few events, not a pause per tick.
        for left in range(abs(ticks), 0, -WHEEL_BATCH):
            _send(_mouse(MOUSEEVENTF_WHEEL, sign * WHEEL_DELTA * min(left, WHEEL_BATCH)))
            time.sleep(0.03)


def hover(hwnd: int, x: int, y: int) -> None:
    """The pointer to client point (x, y) with no button, left there for the session: the
    session keeps the last position it saw once our cursor is put back, so a tooltip shows
    until the next hover or click moves it (_park's reason, used on purpose)."""
    with _active(hwnd, x, y, park=False):
        record_own_input()  # not known to move the last-input clock; noted in case it does
        time.sleep(0.05)  # for the client to send the move before the cursor leaves


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


def type_text(
    hwnd: int,
    text: str,
    focus_at: tuple[int, int] | None = None,
    watch: Watch | None = None,
) -> None:
    """Each character goes as one unicode event, Enter as its key. focus_at is a client point
    to click when the session does not come to the front by itself. watch looks out for the
    user's own keys and clicks while typing; once it has seen one, TypingInterrupted is
    raised before the next character: their keys would land in the middle of ours, so the
    rest is not sent. It starts after the focus click and cursor moves, which are ours."""
    x, y = focus_at or (None, None)
    with _active(hwnd, x, y):
        _ensure_foreground(hwnd, focus_at is not None)
        with watch() if watch else nullcontext() as seen:
            _type(hwnd, text, seen)


def _type(hwnd: int, text: str, seen: Seen | None) -> None:
    for i, ch in enumerate(text):
        if seen is not None and (ago := seen.user_seen()) is not None:
            raise TypingInterrupted(ago, USER_IDLE_S, i, text)
        if user32.GetForegroundWindow() != hwnd:
            raise NotForeground(
                f"focus left the session after {i} of {len(text)} characters, "
                f"{text[:i][-20:]!r} being the last sent; the rest was not sent"
            )
        if ch == "\n":
            _send(_key(KEYS["enter"], False), _key(KEYS["enter"], True))
            continue
        _send(_unicode(ch, False), _unicode(ch, True))
        time.sleep(KEY_S)


def paste_text(
    hwnd: int,
    text: str,
    focus_at: tuple[int, int] | None = None,
    watch: Watch | None = None,
) -> None:
    """Text through the clipboard and Ctrl+V: exact and as fast for 10 KB as for 10 bytes,
    where typing goes key by key. Needs clipboard redirection from this machine to the
    session. A final newline is sent as Enter after the paste, unless watch saw the user's
    own input since the Ctrl+V. The local clipboard's text is put back afterwards."""
    body, enter = (text[:-1], True) if text.endswith("\n") else (text, False)
    old = _clipboard()
    _set_clipboard(body.replace("\r\n", "\n").replace("\n", "\r\n"))
    try:
        time.sleep(0.2)  # the client announces the new clipboard to the session
        x, y = focus_at or (None, None)
        with _active(hwnd, x, y):
            _ensure_foreground(hwnd, focus_at is not None)
            with watch() if watch and enter else nullcontext() as seen:
                _chord(_vks("ctrl+v"))
                if enter:
                    time.sleep(0.2)
                    if seen is not None and (ago := seen.user_seen()) is not None:
                        raise TypingInterrupted(ago, USER_IDLE_S, len(body), body + "\n")
                    _ensure_foreground(hwnd, False)
                    _chord([KEYS["enter"]])
        # The session fetches the text only when it pastes, after the keys arrive.
        time.sleep(0.5)
    finally:
        _set_clipboard(old)


class PasteNotArrived(RuntimeError):
    pass


def paste_line(
    hwnd: int,
    text: str,
    arrived: Callable[[], bool],
    warning: Callable[[], tuple[int, int] | None],
    timeout_s: float = PASTE_WAIT_S,
) -> bool:
    """One line through the clipboard and Ctrl+V, no Enter: the caller presses it once the
    text is in. The session fetches the clipboard only when Ctrl+V lands, so the text can
    arrive a second later, after an Enter sent behind it (which then runs half a command);
    arrived() says it is on screen. A terminal may ask first ("Paste anyway"): warning()
    gives that button's point, clicked once. The local clipboard is put back only after the
    text arrived, or the session could fetch it instead. Returns whether a warning was
    accepted; raises PasteNotArrived when nothing came within timeout_s."""
    if "\n" in text or "\r" in text:
        raise ValueError("paste_line takes one line; its newline is the caller's Enter")
    old = _clipboard()
    _set_clipboard(text)
    accepted = False
    try:
        time.sleep(0.2)  # the client announces the new clipboard to the session
        with _active(hwnd):
            _ensure_foreground(hwnd, False)
            _chord(_vks("ctrl+v"))
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if arrived():
                point = None if accepted else warning()
                if point is None:
                    return accepted
                click(hwnd, *point)
                accepted = True
            time.sleep(0.1)
        raise PasteNotArrived(f"the pasted text did not arrive within {timeout_s:g} s")
    finally:
        _set_clipboard(old)


def _clipboard() -> str | None:
    with _open_clipboard():
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            kernel32.GlobalUnlock(h)


def _set_clipboard(text: str | None) -> None:
    with _open_clipboard():
        user32.EmptyClipboard()
        if text is None:
            return
        data = ctypes.create_unicode_buffer(text)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
        ctypes.memmove(kernel32.GlobalLock(h), data, ctypes.sizeof(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)


@contextmanager
def _open_clipboard() -> Iterator[None]:
    for _ in range(20):  # another program may hold it for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ClipboardBusy("another program holds the clipboard")
    try:
        yield
    finally:
        user32.CloseClipboard()


def press(hwnd: int, combo: str, focus_at: tuple[int, int] | None = None) -> None:
    """A chord like 'ctrl+shift+esc' or 'enter'. Single characters are keys ('ctrl+c')."""
    vks = _vks(combo)
    x, y = focus_at or (None, None)
    with _active(hwnd, x, y):
        _ensure_foreground(hwnd, focus_at is not None)
        _chord(vks)


def _vks(combo: str) -> list[int]:
    vks = []
    # Split on the + between keys, so a + key itself survives: 'ctrl++' is ctrl and +.
    for part in re.split(r"\+(?=.)", combo.lower()):
        if part in KEYS:
            vks.append(KEYS[part])
        elif len(part) == 1 and (vk := user32.VkKeyScanW(part)) != -1:
            vks.append(vk & 0xFF)
        else:
            raise ValueError(f"unknown key '{part}'; use names like enter, esc, ctrl, f5")
    return vks


def _chord(vks: list[int]) -> None:
    _send(*[_key(vk, False) for vk in vks])
    time.sleep(0.03)
    _send(*[_key(vk, True) for vk in reversed(vks)])
