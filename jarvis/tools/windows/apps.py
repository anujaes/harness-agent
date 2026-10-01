"""App lifecycle controls on Windows: launch / focus / quit / list / frontmost."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

from ...constants import SETTLE_WAIT
from ...utils.osinfo import CREATE_NEW_PROCESS_GROUP, hidden_subprocess_kwargs
from . import _win32
from .powershell import run_ps

# Windows apps can take several seconds to put up their first window (cold
# start of Office, Electron apps …); longer than the macOS probe loop.
LAUNCH_WINDOW_TIMEOUT = 15.0
QUIT_TIMEOUT = 5.0
_START_APPS_TTL = 300.0
# Launched apps must outlive Jarvis and never share its console.
_DETACHED = 0x00000008 | CREATE_NEW_PROCESS_GROUP  # DETACHED_PROCESS

_start_apps_lock = threading.Lock()
_start_apps_cache: tuple[float, list[dict]] = (0.0, [])


def start_menu_apps(refresh: bool = False) -> list[dict]:
    """Every app in the Start menu (desktop + Store apps) as ``{"Name", "AppID"}``.

    ``Get-StartApps`` is the only API that sees both classic and packaged
    apps; it takes ~1 s, so the list is cached for a few minutes.
    """
    global _start_apps_cache
    with _start_apps_lock:
        stamp, apps = _start_apps_cache
        if apps and not refresh and time.monotonic() - stamp < _START_APPS_TTL:
            return apps
        try:
            r = run_ps("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=20)
            data = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        except (subprocess.TimeoutExpired, OSError, ValueError):
            data = []
        if isinstance(data, dict):
            data = [data]
        apps = [a for a in data if isinstance(a, dict) and a.get("Name") and a.get("AppID")]
        _start_apps_cache = (time.monotonic(), apps)
        return apps


def _find_start_app(name: str) -> dict | None:
    q = name.strip().lower()
    alias_target = _win32.APP_ALIASES.get(q, "")
    apps = start_menu_apps()
    for pick in (
        lambda a: a["Name"].lower() == q,
        lambda a: a["Name"].lower().startswith(q),
        lambda a: bool(alias_target) and alias_target in a["AppID"].lower(),
        lambda a: q in a["Name"].lower(),
    ):
        hits = [a for a in apps if pick(a)]
        if hits:
            # Prefer the shortest name: "Word" over "Word Mobile Viewer".
            return min(hits, key=lambda a: len(a["Name"]))
    return None


def _launch(name: str) -> tuple[bool, str]:
    """Start ``name``; returns (ok, how-or-error)."""
    target = os.path.expandvars(os.path.expanduser(name.strip().strip('"')))
    if os.path.exists(target):
        os.startfile(target)  # exe, .lnk, document — whatever the shell would open
        return True, target
    app = _find_start_app(name)
    if app:
        # shell:AppsFolder launches both Store apps (AUMID) and desktop apps.
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"], **hidden_subprocess_kwargs())
        return True, app["Name"]
    exe = shutil.which(name) or shutil.which(_win32.APP_ALIASES.get(name.lower(), name))
    if exe:
        subprocess.Popen([exe], cwd=os.path.expanduser("~"), close_fds=True,
                         creationflags=_DETACHED)
        return True, exe
    try:
        # ShellExecute resolves "App Paths" registrations (chrome, winword …).
        os.startfile(_win32.APP_ALIASES.get(name.lower(), name))
        return True, name
    except OSError as e:
        return False, f"could not find an app called {name!r} ({e.strerror or e})"


def launch_app(name: str) -> str:
    if not (name or "").strip():
        return "ERROR: name is required"
    running = _win32.match_windows(name)
    if running:
        _win32.focus_window(running[0].hwnd)
        time.sleep(SETTLE_WAIT)
        return f"{running[0].app_name} was already running — focused it"
    before = {w.hwnd for w in _win32.list_windows()}
    ok, how = _launch(name)
    if not ok:
        return f"ERROR: {how}"

    def _new_window():
        wins = _win32.list_windows()
        hits = _win32.match_windows(name, wins) or _win32.match_windows(how, wins)
        return next((w for w in hits if w.hwnd not in before), None) or (hits[0] if hits else None)

    win = _win32.wait_for(_new_window, LAUNCH_WINDOW_TIMEOUT)
    if not win:
        return (f"launched {how}, but no window appeared within {LAUNCH_WINDOW_TIMEOUT:.0f}s "
                "(it may still be starting, or run in the tray) — wait, then list_apps")
    _win32.focus_window(win.hwnd)
    time.sleep(SETTLE_WAIT)
    return f"launched and focused {win.app_name}"


def _no_window(name: str) -> str:
    return f"ERROR: no open window for {name!r} (is it running? try list_apps or launch_app)"


def focus_app(name: str) -> str:
    wins = _win32.match_windows(name)
    if not wins:
        return _no_window(name)
    if not _win32.focus_window(wins[0].hwnd):
        return f"ERROR: Windows refused to switch to {wins[0].app_name} (focus stealing prevention)"
    return f"focused {wins[0].app_name} — {wins[0].title}"


def quit_app(name: str, force: bool = False) -> str:
    """Close every window of the app (graceful, like clicking X); ``force`` kills it."""
    wins = _win32.match_windows(name)
    if not wins:
        return _no_window(name)
    app = wins[0].app_name
    exe = wins[0].exe_name
    pids = {w.pid for w in wins if w.exe_name == exe}
    if force:
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15,
                           **hidden_subprocess_kwargs())
        return f"force-quit {app}"
    targets = [w for w in wins if w.pid in pids]
    for w in targets:
        _win32.close_window(w.hwnd)

    def _gone():
        alive = {w.hwnd for w in _win32.list_windows()}
        return all(w.hwnd not in alive for w in targets)

    if _win32.wait_for(_gone, QUIT_TIMEOUT):
        return f"quit {app}"
    return (f"asked {app} to close, but a window is still open — it may be asking to save "
            "changes (read_ui to see it), or use force=true")


def list_apps() -> str:
    """Apps with visible windows, one line each: ``App — window title``."""
    lines = []
    seen = set()
    for w in _win32.list_windows():
        key = (w.app_name, w.title)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{w.app_name} — {w.title}")
    return "\n".join(lines) or "(no app windows open)"


def frontmost_app() -> str:
    w = _win32.foreground_window()
    if not w:
        return "(no foreground window)"
    return f"{w.app_name} — {w.title}" if w.title else w.app_name
