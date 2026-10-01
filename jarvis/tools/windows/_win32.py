"""Thin ctypes layer over the Win32 APIs the desktop tools need.

Windows only — import this module lazily from Windows code paths. Covers:
top-level window enumeration (the same set Alt+Tab shows), process image /
product names, reliable foreground switching, and synthetic keyboard + mouse
input through ``SendInput``.

All coordinates are physical screen pixels in the virtual-desktop space; call
:func:`ensure_dpi_aware` before reading or using them so they agree with
screenshots and UI Automation bounding rectangles.
"""
from __future__ import annotations

import ctypes
import functools
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
version = ctypes.WinDLL("version")

ULONG_PTR = ctypes.c_size_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# ── function prototypes (explicit types keep 64-bit handles intact) ─────────
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.LockWorkStation.restype = wintypes.BOOL
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR),
]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [
    ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
]

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


# ── DPI ─────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def ensure_dpi_aware() -> None:
    """Make this process per-monitor DPI aware so all coordinates are physical pixels.

    Idempotent. Without it Windows virtualises coordinates for scaled
    displays (125 %, 150 % …) and clicks land away from what screenshots show.
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except AttributeError:
        pass


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole multi-monitor desktop."""
    ensure_dpi_aware()
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


# ── processes ───────────────────────────────────────────────────────────────

def process_image_path(pid: int) -> str:
    """Full path of the executable for ``pid`` ("" when it can't be read)."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


@functools.lru_cache(maxsize=512)
def file_description(path: str) -> str:
    """The ``FileDescription`` (product-facing name) of an executable, e.g. "Visual Studio Code"."""
    if not path:
        return ""
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return ""
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    langs: list[tuple[int, int]] = []
    if version.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)):
        pairs = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2))
        for i in range(length.value // 4):
            lang, codepage = pairs[i]
            langs.append((lang, codepage))
    langs += [(0x0409, 0x04B0), (0x0409, 0x04E4)]  # en-US Unicode / Western fallbacks
    for lang, codepage in langs:
        key = rf"\StringFileInfo\{lang:04x}{codepage:04x}\FileDescription"
        if version.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            text = ctypes.wstring_at(ptr, length.value).rstrip("\0").strip()
            if text:
                return text
    return ""


# ── windows ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WindowInfo:
    """One top-level application window."""

    hwnd: int
    title: str
    pid: int
    exe_path: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom (physical px)

    @property
    def exe_name(self) -> str:
        """Executable stem, lower-case: ``code``, ``chrome``, ``calculatorapp``."""
        return os.path.splitext(os.path.basename(self.exe_path))[0].lower()

    @property
    def app_name(self) -> str:
        """Friendly app name: the exe's FileDescription, else its stem."""
        desc = file_description(self.exe_path)
        if desc.lower().endswith(".exe"):  # e.g. Windows 11 Notepad ships "Notepad.exe"
            desc = desc[:-4]
        if desc and desc.lower() not in {"application frame host", "windows explorer"} | _GENERIC_DESCRIPTIONS:
            return desc
        if self.exe_name == "explorer":
            return "File Explorer"
        return os.path.splitext(os.path.basename(self.exe_path))[0] or self.title

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2


_GENERIC_DESCRIPTIONS = {"", "application", "app", "main", "launcher"}


def window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    res = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return res == 0 and cloaked.value != 0


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Visible frame bounds (without the invisible resize border) of ``hwnd``."""
    ensure_dpi_aware()
    rect = wintypes.RECT()
    res = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if res != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _is_app_window(hwnd: int) -> bool:
    """The Alt+Tab filter: visible, titled, un-owned, not a tool window, not cloaked."""
    if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) <= 0:
        return False
    ex_style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
        return False
    if user32.GetWindow(hwnd, GW_OWNER) and not ex_style & WS_EX_APPWINDOW:
        return False
    return not _is_cloaked(hwnd)


def _real_pid(hwnd: int, pid: int, exe: str) -> tuple[int, str]:
    """Resolve UWP windows hosted by ApplicationFrameHost to the app's own process."""
    if os.path.basename(exe).lower() != "applicationframehost.exe":
        return pid, exe
    found: list[int] = []

    @WNDENUMPROC
    def _child(child, _lparam):
        child_pid = window_pid(child)
        if child_pid and child_pid != pid:
            found.append(child_pid)
            return False
        return True

    user32.EnumChildWindows(hwnd, _child, 0)
    if found:
        return found[0], process_image_path(found[0]) or exe
    return pid, exe


def window_info(hwnd: int) -> WindowInfo:
    pid = window_pid(hwnd)
    exe = process_image_path(pid)
    pid, exe = _real_pid(hwnd, pid, exe)
    return WindowInfo(hwnd=hwnd, title=window_text(hwnd), pid=pid, exe_path=exe, rect=window_rect(hwnd))


def list_windows() -> list[WindowInfo]:
    """Top-level app windows in z-order (front-most first)."""
    ensure_dpi_aware()
    handles: list[int] = []

    @WNDENUMPROC
    def _collect(hwnd, _lparam):
        if _is_app_window(hwnd):
            handles.append(hwnd)
        return True

    user32.EnumWindows(_collect, 0)
    return [window_info(h) for h in handles]


def foreground_window() -> WindowInfo | None:
    hwnd = user32.GetForegroundWindow()
    return window_info(hwnd) if hwnd else None


# Common names people (and models trained on macOS) use → exe stem.
APP_ALIASES: dict[str, str] = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge", "safari": "msedge",
    "firefox": "firefox", "mozilla firefox": "firefox",
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "terminal": "windowsterminal", "windows terminal": "windowsterminal",
    "explorer": "explorer", "file explorer": "explorer", "finder": "explorer",
    "word": "winword", "microsoft word": "winword",
    "excel": "excel", "microsoft excel": "excel",
    "powerpoint": "powerpnt", "microsoft powerpoint": "powerpnt",
    "outlook": "outlook", "teams": "ms-teams", "microsoft teams": "ms-teams",
    "calculator": "calculatorapp", "settings": "systemsettings", "system settings": "systemsettings",
    "paint": "mspaint", "notepad": "notepad", "task manager": "taskmgr",
    "cmd": "cmd", "command prompt": "cmd", "powershell": "powershell",
}


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    return s[:-4] if s.endswith(".exe") else s


def match_windows(query: str, windows: list[WindowInfo] | None = None) -> list[WindowInfo]:
    """Windows belonging to the app ``query`` names, best match first.

    Matches, in order of preference: the exe stem (``code``), the app's
    product name (``Visual Studio Code``), a known alias, then a substring of
    the product name or window title.
    """
    q = _norm(query)
    if not q:
        return []
    wins = list_windows() if windows is None else windows
    alias = APP_ALIASES.get(q, "")
    scored: list[tuple[int, int, WindowInfo]] = []
    for i, w in enumerate(wins):
        name = w.app_name.lower()
        title = w.title.lower()
        if w.exe_name == q or name == q:
            score = 0
        elif alias and w.exe_name == alias:
            score = 1
        elif name.startswith(q) or w.exe_name.startswith(q):
            score = 2
        elif q in name or q in w.exe_name:
            score = 3
        elif q in title:
            score = 4
        else:
            continue
        scored.append((score, i, w))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [w for _, _, w in scored]


def _press_alt() -> None:
    # A synthetic Alt tap lifts Windows' foreground-lock, so SetForegroundWindow
    # works from a background process (the documented "last input" rule).
    send_inputs([_key_input(VK_MENU, False), _key_input(VK_MENU, True)])


def focus_window(hwnd: int) -> bool:
    """Restore (if minimised) and bring ``hwnd`` to the foreground."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    if user32.GetForegroundWindow() == hwnd:
        return True
    _press_alt()
    if user32.SetForegroundWindow(hwnd):
        return True
    # Fallback: share input state with the current foreground thread.
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread) and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    return ok or user32.GetForegroundWindow() == hwnd


def close_window(hwnd: int) -> bool:
    """Ask a window to close, like clicking its X (the app may prompt to save)."""
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def wait_for(predicate, timeout: float, interval: float = 0.25):
    """Poll ``predicate`` until it returns something truthy or ``timeout`` passes."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


# ── input ───────────────────────────────────────────────────────────────────

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN = 0x10, 0x11, 0x12, 0x5B
VK_RETURN, VK_TAB = 0x0D, 0x09


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

# Keys that need KEYEVENTF_EXTENDEDKEY, or they arrive as their numpad twins.
_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # pgup pgdn end home ← ↑ → ↓
    0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90,        # ins del lwin rwin apps divide numlock
    0xA3, 0xA5, 0x2C,                                 # rctrl ralt printscreen
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,         # volume + media keys
}


def _key_input(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0))


def _unicode_input(code_unit: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags, time=0, dwExtraInfo=0))


def send_inputs(events: list[INPUT]) -> int:
    """Inject ``events``; returns how many the system accepted."""
    if not events:
        return 0
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


# Gap between individual keyboard events. Several events in one SendInput
# call garble input in WinUI/XAML apps (Windows 11 Notepad, Settings, Store
# apps): they read modifier state and VK_PACKET characters when they process
# a message, by which time a batch has moved on — "(" arrives as "9", Ctrl+C
# as "c", characters repeat or vanish. One event at a time is reliable.
KEY_EVENT_DELAY = 0.015


def send_paced(events: list[INPUT], delay: float = KEY_EVENT_DELAY) -> int:
    """Inject ``events`` one at a time with ``delay`` between them."""
    sent = 0
    for ev in events:
        sent += send_inputs([ev])
        time.sleep(delay)
    return sent


def _char_events(ch: str) -> list[INPUT]:
    """Key events for one character: a real key when the layout has it, else Unicode."""
    if ch == "\n":
        return [_key_input(VK_RETURN, False), _key_input(VK_RETURN, True)]
    if ch == "\t":
        return [_key_input(VK_TAB, False), _key_input(VK_TAB, True)]
    caps_lock = user32.GetKeyState(0x14) & 1  # VK_CAPITAL would flip letter case
    if ord(ch) <= 0xFFFF and ch.isprintable() and not (caps_lock and ch.isalpha()):
        res = user32.VkKeyScanW(ch)
        # Low byte = key, high byte = modifiers (1 Shift, 2 Ctrl, 4 Alt). Ctrl/Alt
        # means AltGr, which Unicode input handles more predictably.
        if res != -1 and not (res >> 8) & 0b110:
            vk = res & 0xFF
            keys = [_key_input(vk, False), _key_input(vk, True)]
            if (res >> 8) & 1:
                return [_key_input(VK_SHIFT, False), *keys, _key_input(VK_SHIFT, True)]
            return keys
    events: list[INPUT] = []
    raw = ch.encode("utf-16-le")
    for i in range(0, len(raw), 2):  # emoji outside the BMP → surrogate pair
        unit = int.from_bytes(raw[i:i + 2], "little")
        events += [_unicode_input(unit, False), _unicode_input(unit, True)]
    return events


def type_unicode(text: str) -> int:
    """Type ``text`` into the focused control; returns the number of events accepted.

    Characters on the current keyboard layout go as real key presses (apps
    with key handlers see normal typing); anything else — accents the layout
    lacks, CJK, emoji — goes as Unicode packets. Newlines/tabs are Enter/Tab.
    """
    sent = 0
    for ch in text.replace("\r\n", "\n"):
        sent += send_paced(_char_events(ch))
    return sent


def press_chord(modifiers: list[int], vk: int) -> int:
    """Press ``vk`` while holding ``modifiers`` (all released afterwards, in reverse)."""
    events = [_key_input(m, False) for m in modifiers]
    events += [_key_input(vk, False), _key_input(vk, True)]
    events += [_key_input(m, True) for m in reversed(modifiers)]
    return send_paced(events)


def vk_for_char(ch: str) -> tuple[int, bool] | None:
    """(virtual key, needs shift) for a printable character on the current layout."""
    res = user32.VkKeyScanW(ch)
    if res == -1:
        return None
    return res & 0xFF, bool((res >> 8) & 1)


_MOUSE_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click(x: int, y: int, button: str = "left", clicks: int = 1) -> bool:
    """Move the pointer to physical (x, y) and click ``button`` ``clicks`` times."""
    ensure_dpi_aware()
    down, up = _MOUSE_FLAGS.get(button, _MOUSE_FLAGS["left"])
    if not user32.SetCursorPos(int(x), int(y)):
        return False
    time.sleep(0.03)
    events = []
    for _ in range(max(1, clicks)):
        events += [INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, down, 0, 0)),
                   INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, up, 0, 0))]
    return send_inputs(events) == len(events)


# ── misc system ─────────────────────────────────────────────────────────────

def broadcast_setting_change(area: str) -> None:
    """Tell running apps a setting changed (e.g. ``ImmersiveColorSet`` for dark mode)."""
    result = ULONG_PTR()
    user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, area,
                               SMTO_ABORTIFHUNG, 2000, ctypes.byref(result))


def is_elevated() -> bool:
    """True when this process runs as Administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
