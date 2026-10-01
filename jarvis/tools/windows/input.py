"""Keyboard & mouse input on Windows: type_text, key_press, click_menu, click_at."""
from __future__ import annotations

import re
import time

from . import _win32

# Friendly key names → virtual-key codes.
_VK: dict[str, int] = {
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20, "spacebar": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "forwarddelete": 0x2E,
    "escape": 0x1B, "esc": 0x1B, "insert": 0x2D, "ins": 0x2D,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "pause": 0x13,
    "printscreen": 0x2C, "prtsc": 0x2C, "menu": 0x5D, "apps": 0x5D, "contextmenu": 0x5D,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD, "mute": 0xAD,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{f"num{i}": 0x60 + i for i in range(10)},
}

# Modifier names. macOS habits map naturally: cmd → Ctrl, option → Alt.
_MODS: dict[str, int] = {
    "ctrl": _win32.VK_CONTROL, "control": _win32.VK_CONTROL,
    "cmd": _win32.VK_CONTROL, "command": _win32.VK_CONTROL,
    "shift": _win32.VK_SHIFT,
    "alt": _win32.VK_MENU, "opt": _win32.VK_MENU, "option": _win32.VK_MENU,
    "win": _win32.VK_LWIN, "windows": _win32.VK_LWIN, "super": _win32.VK_LWIN, "meta": _win32.VK_LWIN,
}


# Typing is paced for reliability (~25 chars/s); longer text is pasted instead.
PASTE_THRESHOLD = 200


def _paste(text: str) -> bool:
    """Paste ``text`` via the clipboard, then restore the user's clipboard text.

    Only used when the clipboard currently holds text (or nothing), so an
    image or file list the user copied is never replaced. (Rich formatting of
    copied text is not restored — only its plain text.)
    """
    from ...utils import win_clipboard

    if win_clipboard.holds_non_text():
        return False
    previous = win_clipboard.get_text()
    if previous is None or not win_clipboard.set_text(text):
        return False
    time.sleep(0.05)
    _win32.press_chord([_win32.VK_CONTROL], 0x56)  # Ctrl+V
    time.sleep(0.4)  # let the app read the clipboard before it changes back
    win_clipboard.set_text(previous)
    return True


def type_text(text: str) -> str:
    """Type ``text`` into the focused control of the frontmost app."""
    if not text:
        return "ERROR: text is empty"
    if len(text) > PASTE_THRESHOLD and _paste(text):
        return "OK (pasted)"
    sent = _win32.type_unicode(text)
    if sent == 0:
        return ("ERROR: Windows rejected the keystrokes — the frontmost app may be running as "
                "Administrator (check_permissions)")
    return "OK"


def key_press(keys: str) -> str:
    """Press a key or chord: 'enter', 'ctrl+f', 'ctrl+shift+t', 'alt+f4', 'win+d', 'down'."""
    spec = (keys or "").strip()
    if not spec:
        return f"ERROR: bad key spec: {keys}"
    # "+" itself as a key: "ctrl++" / "+"
    parts = [p.strip().lower() for p in re.split(r"\+(?!$)", spec)] if spec != "+" else ["+"]
    mods = [_MODS[p] for p in parts if p in _MODS]
    key = [p for p in parts if p not in _MODS]
    if len(key) != 1:
        return f"ERROR: bad key spec: {keys}"
    k = key[0].replace(" ", "").replace("_", "")
    if k in _VK:
        vk = _VK[k]
    elif len(key[0]) == 1:
        mapped = _win32.vk_for_char(key[0])
        if mapped is None:
            if mods:
                return f"ERROR: {key[0]!r} has no key on this keyboard layout"
            _win32.type_unicode(key[0])
            return "OK"
        vk, needs_shift = mapped
        if needs_shift and _win32.VK_SHIFT not in mods:
            mods.append(_win32.VK_SHIFT)
    else:
        return f"ERROR: unknown key {key[0]!r}"
    if _win32.press_chord(mods, vk) == 0:
        return "ERROR: Windows rejected the key press (frontmost app may be elevated — check_permissions)"
    return "OK"


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> str:
    """Click at absolute physical screen coordinates (as shown by screenshot / read_ui)."""
    left, top, width, height = _win32.virtual_screen()
    x, y = int(x), int(y)
    if not (left <= x < left + width and top <= y < top + height):
        return f"ERROR: ({x}, {y}) is off-screen (desktop spans {left},{top} → {left + width},{top + height})"
    if not _win32.click(x, y, button=(button or "left").lower(), clicks=2 if double else 1):
        return "ERROR: click was blocked (the app under the cursor may be elevated — check_permissions)"
    return f"clicked {button or 'left'} at {x},{y}"


def _menu_label(s: str) -> str:
    """Normalise a menu label: drop '&' accelerators, shortcut text and trailing ellipses."""
    s = (s or "").split("\t")[0].replace("&", "")
    return s.strip().rstrip(".…").strip().lower()


def _menu_roots(uia, window) -> list:
    """Where an app's open menus live: its window (XAML flyouts, e.g. Windows 11
    Notepad) plus its other top-level windows (classic ``#32768`` popups)."""
    roots = [window]
    try:
        pid = window.ProcessId
        roots += [c for c in uia.GetRootControl().GetChildren()
                  if c.ProcessId == pid and c.NativeWindowHandle != window.NativeWindowHandle]
    except Exception:
        pass
    return roots


def _find_menu_item(uia, roots: list, label: str, timeout: float = 3.0, depth: int = 10):
    want = _menu_label(label)

    def search():
        exact, prefix = None, None
        stack = [(root, 0) for root in roots]
        while stack:
            ctrl, d = stack.pop()
            try:
                children = ctrl.GetChildren()
            except Exception:
                continue
            for child in children:
                if child.ControlTypeName == "MenuItemControl":
                    got = _menu_label(child.Name)
                    if got == want:
                        exact = exact or child
                    elif got.startswith(want):
                        prefix = prefix or child
                if d < depth:
                    stack.append((child, d + 1))
            if exact:
                break
        return exact or prefix

    return _win32.wait_for(search, timeout, interval=0.2)


def _open(uia, item) -> None:
    pattern = item.GetPattern(uia.PatternId.ExpandCollapsePattern)
    if pattern is not None:
        try:
            pattern.Expand()
            return
        except Exception:
            pass
    pattern = item.GetPattern(uia.PatternId.InvokePattern)
    if pattern is not None:
        try:
            pattern.Invoke()
            return
        except Exception:
            pass
    item.Click(simulateMove=False)


def click_menu(app: str, path: list) -> str:
    """Click a menu item by path, e.g. app='Notepad', path=['File', 'Save as'].

    Works for classic menu bars (Notepad, Explorer, VS Code, most Win32 apps).
    Ribbon apps (Office) expose tabs instead — use click_element there.
    """
    if not path:
        return "ERROR: path required"
    import uiautomation as uia

    wins = _win32.match_windows(app)
    if not wins:
        return f"ERROR: no open window for {app!r}"
    _win32.focus_window(wins[0].hwnd)
    time.sleep(0.2)
    with uia.UIAutomationInitializerInThread():
        window = uia.ControlFromHandle(wins[0].hwnd)
        item = _find_menu_item(uia, [window], path[0], timeout=2.0)
        if item is None:
            names = _top_menu_names(window)
            hint = f" Menus: {', '.join(names)}" if names else " This app has no classic menu bar."
            return f"ERROR: menu {path[0]!r} not found in {wins[0].app_name}.{hint}"
        for i, label in enumerate(path[1:], 1):
            _open(uia, item)
            time.sleep(0.15)
            item = _find_menu_item(uia, _menu_roots(uia, window), label)
            if item is None:
                uia.SendKeys("{Esc}{Esc}", waitTime=0)
                return f"ERROR: menu item {label!r} not found under {' > '.join(path[:i])}"
        try:
            pattern = item.GetPattern(uia.PatternId.InvokePattern)
            if pattern is not None:
                pattern.Invoke()
            else:
                _open(uia, item)
        except Exception as e:
            return f"ERROR clicking menu item: {e}"
    return f"clicked {' > '.join(path)}"


def _top_menu_names(window) -> list[str]:
    names: list[str] = []
    stack = [(window, 0)]
    while stack:
        ctrl, d = stack.pop()
        try:
            children = ctrl.GetChildren()
        except Exception:
            continue
        for child in children:
            if child.ControlTypeName == "MenuBarControl":
                names += [c.Name for c in child.GetChildren() if c.Name]
            elif d < 3:
                stack.append((child, d + 1))
    return [n for n in names if n.lower() not in ("system", "system menu bar")]
