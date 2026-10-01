"""Screen and window capture for ``screenshot`` on Windows (ctypes + Pillow).

Builds on :mod:`._win32` for DPI awareness, the virtual desktop, window
enumeration/matching and process names; this module adds only what capture
needs: grabbing the screen, ``PrintWindow`` (so covered windows come out
whole) with a screen-copy fallback, capturing minimized windows, and the
parent-process walk behind ``terminal_app_name``.

All coordinates are physical pixels on the virtual desktop — the same space
``click_at`` uses — including monitors left of / above the primary one.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any

from . import _win32 as w32
from ._win32 import WindowInfo, ensure_dpi_aware, virtual_screen  # noqa: F401  (re-exported)

user32 = w32.user32
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SM_CMONITORS = 80
PW_RENDERFULLCONTENT = 0x00000002
SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7
TH32CS_SNAPPROCESS = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
MIN_WIDTH, MIN_HEIGHT = 60, 40  # smaller "windows" are never what the user means


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


# Own WinDLL handles, so these prototypes never clash with other modules'.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_WINDOWPLACEMENT)]
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            wintypes.LPVOID, wintypes.LPVOID, wintypes.UINT]
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


# ── screen ───────────────────────────────────────────────────────────────


def monitor_count() -> int:
    return max(1, user32.GetSystemMetrics(SM_CMONITORS))


def grab_screen(bbox: tuple[int, int, int, int] | None = None):
    """PIL image of every monitor, or of ``bbox`` (left, top, right, bottom)."""
    from PIL import ImageGrab

    ensure_dpi_aware()
    return ImageGrab.grab(bbox=bbox, all_screens=True)


# ── windows ──────────────────────────────────────────────────────────────


def is_minimized(win: WindowInfo) -> bool:
    return bool(_user32.IsIconic(win.hwnd))


def restored_rect(win: WindowInfo) -> tuple[int, int, int, int]:
    """Where ``win`` is — or, when minimized (parked at -32000 with a
    caption-sized rect), where it will be once restored."""
    if not is_minimized(win):
        return win.rect
    placement = _WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
    _user32.GetWindowPlacement(win.hwnd, ctypes.byref(placement))
    r = placement.rcNormalPosition
    return r.left, r.top, r.right, r.bottom


def app_windows(include_minimized: bool = False) -> list[WindowInfo]:
    """Real app windows front to back (:func:`_win32.list_windows`, minus
    tiny ones, our own, and — unless asked — minimized ones)."""
    own_pid = os.getpid()
    out = []
    for win in w32.list_windows():
        if win.pid == own_pid or (not include_minimized and is_minimized(win)):
            continue
        left, top, right, bottom = restored_rect(win)
        if right - left >= MIN_WIDTH and bottom - top >= MIN_HEIGHT:
            out.append(win)
    return out


def find_windows(app: str) -> list[WindowInfo]:
    """Windows of ``app`` (exe stem, product name, alias or title — see
    :func:`_win32.match_windows`), best first; on-screen windows are
    preferred, minimized ones are the fallback."""
    return (w32.match_windows(app, app_windows())
            or w32.match_windows(app, app_windows(include_minimized=True)))


def app_names() -> list[str]:
    """Sorted friendly names of apps with an on-screen window (for errors)."""
    return sorted({w.app_name for w in app_windows() if w.app_name})


def _print_window(hwnd: int):
    """``(image, window_rect)`` via ``PrintWindow``; image is None on failure."""
    from PIL import Image

    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None, rect
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None, rect
    hdc = _user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not _user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            return None, rect
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, BI_RGB, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(header), DIB_RGB_COLORS) != h:
            return None, rect
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1), rect
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        _user32.ReleaseDC(hwnd, hdc)


def _is_blank(img) -> bool:
    extrema = img.getextrema()
    return all(hi == lo for lo, hi in extrema) if extrema else True


def capture_window(win: WindowInfo) -> tuple[Any, dict, str]:
    """Capture one window: ``(image, bounds, note)``.

    ``bounds`` holds the final ``x/y/w/h`` in screen pixels. A minimized
    window is restored without focus and minimized again afterwards. Falls
    back to copying the window's area of the screen when the app won't render
    into ``PrintWindow`` (then overlapping windows may show).
    """
    ensure_dpi_aware()
    hwnd = win.hwnd
    restored = is_minimized(win)
    if restored:
        _user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(0.4)  # let the restore animation finish
    try:
        left, top, right, bottom = w32.window_rect(hwnd)
        img, rect = _print_window(hwnd)
        note = ""
        if img is not None:
            # Trim the invisible resize borders (window rect → frame bounds).
            img = img.crop((left - rect.left, top - rect.top, right - rect.left, bottom - rect.top))
        if img is None or _is_blank(img):
            img = grab_screen((left, top, right, bottom))
            note = "Copied from the screen (the app doesn't support window capture), so windows on top of it may show."
        return img, {"x": left, "y": top, "w": right - left, "h": bottom - top}, note
    finally:
        if restored:
            _user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)


# ── process tree (terminal detection) ────────────────────────────────────


def process_table() -> dict[int, tuple[int, str]]:
    """``{pid: (parent_pid, exe_name)}`` for every running process."""
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    table: dict[int, tuple[int, str]] = {}
    if not snap or snap == wintypes.HANDLE(-1).value:
        return table
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return table


def ancestor_exes(pid: int | None = None, limit: int = 24) -> list[str]:
    """Exe names of ``pid``'s parents, nearest first (lower-case)."""
    table = process_table()
    out: list[str] = []
    seen: set[int] = set()
    cur = table.get(pid or os.getpid(), (0, ""))[0]
    while cur and cur not in seen and len(out) < limit and cur in table:
        seen.add(cur)
        parent, exe = table[cur]
        out.append(exe.lower())
        cur = parent
    return out
