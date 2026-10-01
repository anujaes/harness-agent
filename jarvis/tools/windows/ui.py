"""Read the UI Automation tree and click elements by text (Windows twin of the JXA tools).

Output mirrors the macOS dump so prompts and habits carry over::

    [UI of Notepad]
    [Window 1] title="Untitled - Notepad"
      [Edit] name="Text editor" value="hello" @640,410
"""
from __future__ import annotations

import time

from . import _win32

READ_UI_TIMEOUT = 25.0
CLICK_SEARCH_TIMEOUT = 15.0
CLICK_MAX_DEPTH = 12

# macOS AX role words the model already knows → UIA control type names.
_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    # Rich editors (Notepad, Word, browsers' contenteditable) report Document, not Edit.
    "edit": ("edit", "document"), "editor": ("edit", "document"), "document": ("document",),
    "textfield": ("edit",), "textarea": ("edit", "document"), "text": ("text", "edit"),
    "field": ("edit",), "input": ("edit", "combobox"), "searchfield": ("edit",),
    "row": ("dataitem", "listitem", "treeitem"), "cell": ("dataitem", "custom"),
    "link": ("hyperlink",), "popupbutton": ("combobox", "splitbutton", "button"),
    "checkbox": ("checkbox",), "radio": ("radiobutton",), "radiobutton": ("radiobutton",),
    "tab": ("tabitem",), "statictext": ("text",), "image": ("image",),
    "menu": ("menu", "menuitem"), "menuitem": ("menuitem",), "button": ("button", "splitbutton"),
}


def _uia():
    import uiautomation  # lazy: Windows-only dependency

    return uiautomation


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s[:n] + "…" if len(s) > n else s


def _role(ctrl) -> str:
    name = ctrl.ControlTypeName or "Control"
    return name[:-7] if name.endswith("Control") else name


def _value(ctrl) -> str:
    uia = _uia()
    try:
        v = ctrl.GetPropertyValue(uia.PropertyId.ValueValueProperty)
        return "" if v is None else str(v)
    except Exception:
        return ""


def _center(ctrl) -> tuple[int, int] | None:
    try:
        r = ctrl.BoundingRectangle
    except Exception:
        return None
    if not r or r.width() <= 0 or r.height() <= 0:
        return None
    return r.xcenter(), r.ycenter()


def _describe(ctrl, depth: int) -> str:
    role = _role(ctrl)
    try:
        name = ctrl.Name or ""
    except Exception:
        name = ""
    value = _value(ctrl)
    try:
        desc = ctrl.HelpText or ""
    except Exception:
        desc = ""
    line = "  " * depth + f"[{role}]"
    if name:
        line += f' name="{_clip(name, 80)}"'
    if value and value != name:
        line += f' value="{_clip(value, 120)}"'
    if desc and desc not in (name, value):
        line += f' desc="{_clip(desc, 80)}"'
    if not name:
        try:
            aid = ctrl.AutomationId or ""
        except Exception:
            aid = ""
        if aid and not aid.isdigit():
            line += f' id="{_clip(aid, 60)}"'
    pos = _center(ctrl)
    if pos:
        line += f" @{pos[0]},{pos[1]}"
    return line


def _children(ctrl) -> list:
    try:
        return ctrl.GetChildren()
    except Exception:
        return []


def _target_windows(app: str) -> tuple[str, list[_win32.WindowInfo]] | str:
    """(display name, windows) for ``app`` (blank = foreground app), or an error string."""
    if app.strip():
        wins = _win32.match_windows(app)
        if not wins:
            return f"ERROR: process '{app}' not found (no open window — try list_apps)"
        exe = wins[0].exe_name
        return wins[0].app_name, [w for w in wins if w.exe_name == exe]
    fg = _win32.foreground_window()
    if not fg:
        return "ERROR: no frontmost app"
    same = [w for w in _win32.list_windows() if w.pid == fg.pid]
    return fg.app_name, same or [fg]


def read_ui(app: str = "", max_depth: int = 7, max_lines: int = 400, max_chars: int = 14000) -> str:
    """Dump the UI Automation tree of ``app``'s windows (blank = frontmost app)."""
    uia = _uia()
    _win32.ensure_dpi_aware()
    with uia.UIAutomationInitializerInThread():
        target = _target_windows(app or "")
        if isinstance(target, str):
            return target
        name, wins = target
        out = [f"[UI of {name}]"]
        deadline = time.monotonic() + READ_UI_TIMEOUT
        timed_out = False

        def walk(ctrl, depth: int) -> None:
            nonlocal timed_out
            for child in _children(ctrl):
                if len(out) >= max_lines:
                    return
                if time.monotonic() > deadline:
                    timed_out = True
                    return
                out.append(_describe(child, depth))
                if depth < max_depth:
                    walk(child, depth + 1)

        for i, w in enumerate(wins, 1):
            ctrl = uia.ControlFromHandle(w.hwnd)
            out.append(f'[Window {i}] title="{_clip(w.title, 100)}"')
            if ctrl is not None:
                walk(ctrl, 1)
            if len(out) >= max_lines:
                out.append(f"… [truncated at {max_lines} lines]")
                break
            if timed_out:
                out.append(f"… [stopped after {READ_UI_TIMEOUT:.0f}s — tree is very large; lower max_depth]")
                break
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… [truncated, {len(text)} chars total]"
    return text if len(out) > 1 else "(empty UI tree)"


def _role_matches(ctrl, role_filter: str) -> bool:
    if not role_filter:
        return True
    role = _role(ctrl).lower()
    wanted = _ROLE_ALIASES.get(role_filter, (role_filter,))
    return any(w in role for w in wanted) or role_filter in role


def _haystack(ctrl) -> str:
    parts = []
    for attr in ("Name", "HelpText", "AutomationId"):
        try:
            parts.append(getattr(ctrl, attr) or "")
        except Exception:
            pass
    parts.append(_value(ctrl))
    return "\n".join(parts).lower()


def find_elements(wins: list[_win32.WindowInfo], query: str, role: str = "", limit: int = 6) -> list:
    """Controls under ``wins`` whose name/value/help/id contains ``query``, in tree order."""
    uia = _uia()
    q = query.lower()
    role_filter = role.lower().replace(" ", "")
    hits: list = []
    deadline = time.monotonic() + CLICK_SEARCH_TIMEOUT

    def walk(ctrl, depth: int) -> None:
        if depth > CLICK_MAX_DEPTH or len(hits) >= limit or time.monotonic() > deadline:
            return
        for child in _children(ctrl):
            if len(hits) >= limit:
                return
            if _role_matches(child, role_filter) and q in _haystack(child):
                hits.append(child)
            walk(child, depth + 1)

    for w in wins:
        root = uia.ControlFromHandle(w.hwnd)
        if root is not None:
            walk(root, 0)
    return hits


def activate(ctrl) -> str:
    """Press ``ctrl`` the way a user would, preferring accessibility actions over clicks.

    Returns ``PRESSED at x,y`` (pattern action) or ``CLICKED at x,y`` (mouse).
    """
    uia = _uia()
    pos = _center(ctrl)
    at = f"{pos[0]},{pos[1]}" if pos else "?"
    role = _role(ctrl).lower()
    actions = (
        (uia.PatternId.InvokePattern, lambda p: p.Invoke()),
        (uia.PatternId.TogglePattern, lambda p: p.Toggle()),
        (uia.PatternId.SelectionItemPattern, lambda p: p.Select()),
        (uia.PatternId.ExpandCollapsePattern, lambda p: p.Expand()),
    )
    # Text fields want focus + caret, not "invoke".
    if role not in ("edit", "document"):
        for pattern_id, act in actions:
            pattern = ctrl.GetPattern(pattern_id)
            if pattern is None:
                continue
            try:
                act(pattern)
                return f"PRESSED at {at}"
            except Exception:
                continue
    if pos is None:
        try:
            ctrl.SetFocus()
            return "FOCUSED (element has no on-screen position)"
        except Exception as e:
            return f"ERROR clicking: element is off-screen and cannot take focus ({e})"
    top = ctrl.GetTopLevelControl()
    if top is not None and top.NativeWindowHandle:
        _win32.focus_window(top.NativeWindowHandle)
    _win32.click(*pos)
    return f"CLICKED at {at}"


def click_element(app: str, query: str, role: str = "", nth: int = 1) -> str:
    """Find the ``nth`` element in ``app`` whose text contains ``query`` and click it."""
    if not (query or "").strip():
        return "ERROR: query is required"
    uia = _uia()
    _win32.ensure_dpi_aware()
    nth = max(1, int(nth or 1))
    with uia.UIAutomationInitializerInThread():
        target = _target_windows(app or "")
        if isinstance(target, str):
            return target
        _name, wins = target
        hits = find_elements(wins, query, role, limit=nth + 5)
        if len(hits) < nth:
            return f"NOT_FOUND ({len(hits)} matches)"
        try:
            return activate(hits[nth - 1])
        except Exception as e:
            return f"ERROR clicking: {e}"


def wait(seconds: float = 0.8) -> str:
    """Sleep — useful after launching/clicking so the UI settles before read_ui."""
    time.sleep(max(0.0, min(float(seconds), 10.0)))
    return f"slept {seconds}s"


def _process_is_elevated(pid: int) -> bool | None:
    """Whether another process runs as Administrator (None = can't tell)."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    handle = _win32.kernel32.OpenProcess(_win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True  # access denied to a limited query ≈ a higher-integrity process
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return True  # a medium-integrity caller is denied an elevated process's token
        elevated, size = wintypes.DWORD(), wintypes.DWORD()
        ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(elevated),  # TokenElevation
                                          ctypes.sizeof(elevated), ctypes.byref(size))
        return bool(elevated.value) if ok else None
    finally:
        if token:
            _win32.kernel32.CloseHandle(token)
        _win32.kernel32.CloseHandle(handle)


def check_permissions() -> str:
    """Diagnose what can block desktop control on Windows (there is no permission prompt).

    The real blocker is UIPI: a normal process can't read or send input to an
    app running as Administrator.
    """
    lines = []
    try:
        uia = _uia()
        with uia.UIAutomationInitializerInThread():
            fg = _win32.foreground_window()
            root = uia.GetRootControl()
            ok = root is not None and bool(root.GetChildren())
        lines.append(f"UI Automation OK. Frontmost app: {fg.app_name if fg else '(none)'}" if ok
                     else "UI AUTOMATION UNAVAILABLE — the desktop tree came back empty.")
    except ImportError:
        return ("UI AUTOMATION MISSING — the 'uiautomation' package is not installed. "
                "Reinstall Jarvis (pip install -e .) to get the Windows desktop tools.")
    except Exception as e:
        lines.append(f"UI AUTOMATION ERROR: {e}")
        fg = None
    me_admin = _win32.is_elevated()
    lines.append(f"Jarvis is running {'as Administrator' if me_admin else 'without elevation'}.")
    if fg and not me_admin and _process_is_elevated(fg.pid):
        lines.append(f"BLOCKED: {fg.app_name} runs as Administrator, so Windows (UIPI) blocks reading its UI "
                     "and sending it clicks/keys. Restart that app normally, or run your terminal as "
                     "Administrator.")
    try:
        from PIL import ImageGrab  # noqa: F401

        lines.append("Screen capture OK — screenshot() of the screen / app windows works (no permission needed).")
    except ImportError:
        lines.append("SCREEN CAPTURE MISSING — Pillow is not installed (pip install -e .).")
    return "\n".join(lines)
