"""Find the remote-desktop client window and describe its state. Win32 via ctypes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from jharokha.core.models import WindowInfo

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DEFAULT_PROCESSES = ("msrdc.exe", "mstsc.exe", "windowsapp.exe", "windows365.exe")

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_dpi_aware = False


def ensure_dpi_aware() -> None:
    """Per-monitor-v2 so client rects are in physical pixels; once per process."""
    global _dpi_aware
    if not _dpi_aware:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        _dpi_aware = True


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(h)


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def list_windows(processes: tuple[str, ...] = DEFAULT_PROCESSES) -> list[tuple[int, str, str]]:
    """Visible top-level windows of allowlisted processes as (hwnd, title, process)."""
    out: list[tuple[int, str, str]] = []

    @_ENUM_PROC
    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd) and (title := _title(hwnd)):
            proc = _process_name(hwnd)
            if proc in processes:
                out.append((hwnd, title, proc))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(
    window_title: str | None = None, processes: tuple[str, ...] = DEFAULT_PROCESSES
) -> tuple[int, str, str] | None:
    """First allowlisted window whose title contains window_title (case-insensitive)."""
    for hwnd, title, proc in list_windows(processes):
        if window_title is None or window_title.lower() in title.lower():
            return hwnd, title, proc
    return None


def window_info(hwnd: int, title: str, process: str) -> WindowInfo:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    dpi = user32.GetDpiForWindow(hwnd) or 96
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        process=process,
        minimized=bool(user32.IsIconic(hwnd)),
        foreground=user32.GetForegroundWindow() == hwnd,
        client_size=(rect.right, rect.bottom),
        dpi_scale=dpi / 96,
    )
