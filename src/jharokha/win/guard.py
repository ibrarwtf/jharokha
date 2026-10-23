"""The Windows side of core/guard.py: the input lease file, the last local input, and what
covers the session window. Used only with --allow-input."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import msvcrt
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes

from jharokha.core.guard import (
    LEASE_TTL_S,
    OWN_MARGIN_MS,
    USER_IDLE_S,
    Holder,
    InputLeased,
    check_lease,
    check_user_idle,
    covered_parts,
    sample_points,
)
from jharokha.core.guard import user_idle_s as _idle_from_ticks
from jharokha.core.models import Point
from jharokha.win.window import _process_name

# Own DLL objects: argtypes are cached per object, and win/input.py declares WindowFromPoint
# with its own POINT type on windll.user32.
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")

GA_ROOT, MONITOR_DEFAULTTONEAREST = 2, 2
PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
COVER_STEP = 32  # 2000 samples on a 1080p client, a few ms


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


kernel32.GetTickCount.restype = wintypes.DWORD  # unsigned: an int goes negative after 24.8 days
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]


def _alive(pid: int) -> bool:
    """A pid we cannot open is gone: every jharokha runs as the same user, and limited query
    rights are granted even across elevation."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(h, ctypes.byref(code))) and (
            code.value == STILL_ACTIVE
        )
    finally:
        kernel32.CloseHandle(h)


class Lease:
    """One writer per session window across processes. take() before every send: it raises
    InputLeased while another live process sent to the same window within ttl_s, and
    otherwise makes this process the holder as of now. A holder that stops sending lapses
    after ttl_s; one that crashed frees the window at once (its pid no longer runs).

    The record is a small JSON file, {"pid", "last_s"}, under %TEMP%\\jharokha, named by a
    hash of key (the hwnd or title) so no title is written to disk. Each read-decide-write
    happens under msvcrt.locking on its first byte; the OS drops that lock with the handle,
    so a process killed mid-update blocks nobody."""

    def __init__(
        self,
        key: str | int,
        ttl_s: float = LEASE_TTL_S,
        root: str | os.PathLike[str] | None = None,
        pid: int | None = None,
        clock: Callable[[], float] = time.time,
        alive: Callable[[int], bool] = _alive,
    ) -> None:
        name = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]
        self.root = os.fspath(root) if root else os.path.join(tempfile.gettempdir(), "jharokha")
        self.path = os.path.join(self.root, f"lease-{name}.json")
        self.ttl_s = ttl_s
        self.pid = os.getpid() if pid is None else pid
        self.clock = clock
        self.alive = alive

    def take(self) -> None:
        with self._locked() as fd:
            now = self.clock()
            check_lease(_read(fd), self.pid, now, self.ttl_s, self.alive)
            _write(fd, json.dumps({"pid": self.pid, "last_s": now}).encode())

    def release(self) -> None:
        """Frees the window now instead of after ttl_s, if this process holds it."""
        with self._locked() as fd:
            held = _read(fd)
            if held is not None and held.pid == self.pid:
                _write(fd, b"")

    def holder(self) -> Holder | None:
        with self._locked() as fd:
            return _read(fd)

    def other(self) -> InputLeased | None:
        """What take() would refuse with now, without taking the lease: for status."""
        try:
            check_lease(self.holder(), self.pid, self.clock(), self.ttl_s, self.alive)
        except InputLeased as e:
            return e
        return None

    @contextlib.contextmanager
    def _locked(self) -> Iterator[int]:
        os.makedirs(self.root, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_BINARY)
        try:
            # LK_LOCK retries for 10 s; holders keep the lock for microseconds.
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield fd
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


def _read(fd: int) -> Holder | None:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        d = json.loads(os.read(fd, 4096) or b"null")
        return Holder(int(d["pid"]), float(d["last_s"])) if d else None
    except (ValueError, KeyError, TypeError):
        return None  # a torn or foreign file holds nothing


def _write(fd: int, data: bytes) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, data)


_own_input_ms: int | None = None


def record_own_input() -> None:
    """Called right after each SendInput, so our own input is not taken for the user's."""
    global _own_input_ms
    _own_input_ms = kernel32.GetTickCount()


def user_idle_s(margin_ms: int = OWN_MARGIN_MS) -> float | None:
    """Seconds since the user last touched this laptop's keyboard or mouse; None when the
    newest local input is our own (no newer than our last SendInput plus margin_ms).
    Only SendInput is recorded: SetCursorPos, which input.py parks the cursor with, is not
    input and is not known to move this clock (it does not keep a screen saver off)."""
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError()
    return _idle_from_ticks(info.dwTime, kernel32.GetTickCount(), _own_input_ms, margin_ms)


def ensure_user_idle(threshold_s: float = USER_IDLE_S, margin_ms: int = OWN_MARGIN_MS) -> None:
    """Raises UserActive when the user's last input is under threshold_s old."""
    check_user_idle(user_idle_s(margin_ms), threshold_s)


def covered(hwnd: int, step: int = COVER_STEP) -> list[dict]:
    """Parts of hwnd's client area hidden by the taskbar or other windows, as
    core.guard.covered_parts describes. Empty when all of it shows."""
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    client = (origin.x, origin.y, origin.x + rc.right, origin.y + rc.bottom)
    mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    user32.GetMonitorInfoW(
        user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi)
    )
    monitor = (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom)
    work = (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)

    apps: dict[int, str] = {}
    owners: dict[Point, str | None] = {}
    for x, y in sample_points((rc.right, rc.bottom), step):
        top = user32.GetAncestor(
            user32.WindowFromPoint(wintypes.POINT(origin.x + x, origin.y + y)), GA_ROOT
        )
        if top == hwnd:
            owners[(x, y)] = None
            continue
        if top not in apps:
            apps[top] = (_process_name(top) or "unknown") if top else ""
        owners[(x, y)] = apps[top]
    return covered_parts(client, monitor, work, owners, step)
