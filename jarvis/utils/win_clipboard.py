"""Unicode text clipboard on Windows via the Win32 API (``CF_UNICODETEXT``).

``clip.exe`` only writes, and garbles non-ASCII unless fed UTF-16; this module
reads and writes the clipboard directly with ctypes, so text such as
``"héllo — 日本"`` round-trips exactly. Every function returns a failure value
(``None`` / ``False``) instead of raising, including on non-Windows hosts.
"""
from __future__ import annotations

import sys
import time

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_OPEN_ATTEMPTS = 20        # another app may hold the clipboard briefly
_OPEN_RETRY_DELAY = 0.025  # seconds between attempts

_api = None


def _win32():
    """Lazily bind the user32/kernel32 functions with 64-bit-safe signatures."""
    global _api
    if _api is not None:
        return _api
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t

    _api = (ctypes, user32, kernel32)
    return _api


def _open_clipboard(user32) -> bool:
    for _ in range(_OPEN_ATTEMPTS):
        if user32.OpenClipboard(None):
            return True
        time.sleep(_OPEN_RETRY_DELAY)
    return False


def get_text() -> str | None:
    """Clipboard text (``\\n`` line endings), ``""`` when it holds no text,
    ``None`` on failure."""
    if sys.platform != "win32":
        return None
    try:
        ctypes, user32, kernel32 = _win32()
    except Exception:
        return None
    if not _open_clipboard(user32):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            # Bound the read by the allocation size in case the data is not
            # NUL-terminated; wstring_at stops at the first NUL otherwise.
            chars = kernel32.GlobalSize(handle) // ctypes.sizeof(ctypes.c_wchar)
            text = ctypes.wstring_at(ptr, chars) if chars else ""
            return text.split("\x00", 1)[0].replace("\r\n", "\n")
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def holds_non_text() -> bool:
    """True when the clipboard has content but no text (an image, copied files …)."""
    if sys.platform != "win32":
        return False
    try:
        ctypes, user32, _kernel32 = _win32()
        user32.CountClipboardFormats.restype = ctypes.c_int
        return user32.CountClipboardFormats() > 0 and not user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
    except Exception:
        return True  # unknown → treat as precious, don't overwrite


def set_text(text: str) -> bool:
    """Replace the clipboard with ``text`` (stored with CRLF line endings, the
    Windows convention); True on success."""
    if sys.platform != "win32":
        return False
    try:
        ctypes, user32, kernel32 = _win32()
    except Exception:
        return False
    data = (text or "").replace("\r\n", "\n").replace("\n", "\r\n")
    buf = ctypes.create_unicode_buffer(data)  # includes the terminating NUL
    size = ctypes.sizeof(buf)
    if not _open_clipboard(user32):
        return False
    handle = None
    try:
        if not user32.EmptyClipboard():
            return False
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, buf, size)
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        handle = None  # the clipboard owns the memory now
        return True
    except Exception:
        return False
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()
