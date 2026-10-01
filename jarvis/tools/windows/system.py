"""System-level tools on Windows: open_url, notify, speck, task_run, system_control."""
from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import threading
import winreg
from ctypes import wintypes
from pathlib import Path

from ...constants import HARNESS_HOME, MAX_TOOL_OUTPUT, SPECK_MAX_CHARS
from . import _win32
from .powershell import _base_argv, approve, ps, ps_quote, run_argv, run_ps

# Where task_run looks for PowerShell scripts by name (Windows' stand-in for
# the Shortcuts app): <project>/.harness/tasks/<name>.ps1, then ~/.harness/tasks.
TASKS_DIRNAME = "tasks"
TASK_TIMEOUT = 120


def _run_async(coro_factory, timeout: float = 15.0):
    """Run a WinRT coroutine to completion from any thread (tools run in workers)."""
    result: dict = {}

    def _worker():
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as e:  # surfaced to the caller below
            result["error"] = e

    t = threading.Thread(target=_worker, name="winrt-call", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Windows API call timed out after {timeout:.0f}s")
    if "error" in result:
        raise result["error"]
    return result.get("value")


# ── open / notify / speak ───────────────────────────────────────────────────

def open_url(url: str) -> str:
    """Open a URL, file or folder with its default handler (browser, app, Explorer)."""
    target = (url or "").strip()
    if not target:
        return "ERROR: url is required"
    if "://" not in target and not target.startswith(("mailto:", "ms-", "tel:")):
        target = os.path.expandvars(os.path.expanduser(target))
    try:
        os.startfile(target)
    except OSError as e:
        return f"ERROR: {e.strerror or e}"
    return "opened"


def notify(title: str, message: str = "") -> str:
    from ...utils import notify as _notify

    sync = getattr(_notify, "desktop_notify_sync", None)
    if sync is None:
        return "shown" if _notify.desktop_notify(title, message) else "ERROR: notifications unavailable"
    ok, err = sync(title, message)
    return "shown" if ok else f"ERROR: {err or 'notification failed'}"


_SPEAK_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$want = {voice}
if ($want -eq '?') {{
  $s.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {{
    $v = $_.VoiceInfo; "{{0}} ({{1}}, {{2}})" -f $v.Name, $v.Culture, $v.Gender
  }}
  exit 0
}}
if ($want) {{
  $v = $s.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Name -like "*$want*" }} | Select-Object -First 1
  if (-not $v) {{ [Console]::Error.WriteLine("no installed voice matches '$want' (speck voice='?' lists them)"); exit 2 }}
  $s.SelectVoice($v.VoiceInfo.Name)
}}
$s.Rate = {rate}
$s.Speak([Console]::In.ReadToEnd())
"""

# macOS `say -r` takes words per minute (~180 default); SAPI takes -10..10.
_DEFAULT_WPM = 180


def _sapi_rate(wpm: int) -> int:
    if not wpm or wpm <= 0:
        return 0
    return max(-10, min(10, round((wpm - _DEFAULT_WPM) / 18)))


def speck(text: str, voice: str = "", rate: int = 0) -> str:
    """Speak text aloud with Windows' built-in speech synthesizer (voice='?' lists voices)."""
    listing = (voice or "").strip() == "?"
    t = (text or "").strip()
    if not t and not listing:
        return "ERROR: text is empty"
    if len(t) > SPECK_MAX_CHARS:
        return f"ERROR: text exceeds {SPECK_MAX_CHARS} characters (split into shorter speck calls)"
    script = _SPEAK_SCRIPT.format(voice=ps_quote((voice or "").strip()), rate=_sapi_rate(int(rate or 0)))
    try:
        r = run_ps(script, timeout=max(30.0, min(600.0, len(t) * 0.08)), stdin=t)
    except subprocess.TimeoutExpired:
        return "ERROR: speech timed out"
    except OSError as e:
        return f"ERROR: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"ERROR: speech failed: {err[:MAX_TOOL_OUTPUT]}"
    return (r.stdout.strip() or "(no voices installed)") if listing else "spoke"


# ── tasks (Windows' answer to Apple Shortcuts) ──────────────────────────────

def _task_dirs() -> list[Path]:
    from ...constants import CWD

    return [Path(CWD) / ".harness" / TASKS_DIRNAME, Path(HARNESS_HOME) / TASKS_DIRNAME]


def _find_script(name: str) -> Path | None:
    """``<name>.ps1`` from the tasks folders (project first, then ~/.harness/tasks).

    Only bare names resolve — never an arbitrary path — so a script the model
    just wrote elsewhere can't be run through here.
    """
    stem = name[:-4] if name.lower().endswith(".ps1") else name
    if not stem or Path(stem).name != stem or stem in (".", ".."):
        return None
    for d in _task_dirs():
        cand = d / f"{stem}.ps1"
        if cand.is_file():
            return cand
    return None


_PREVIEW_LINES = 12


def _task_preview(script: Path) -> str:
    """What the approval prompt shows: the path plus the start of the script.

    A project's ``.harness/tasks`` comes with the repo, so the user should see
    what they're approving, not just a file name.
    """
    try:
        lines = script.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        lines = []
    body = "\n".join(lines[:_PREVIEW_LINES])
    more = f"\n… ({len(lines) - _PREVIEW_LINES} more lines)" if len(lines) > _PREVIEW_LINES else ""
    return f"task_run {script}\n{body}{more}".rstrip()


def _list_tasks() -> str:
    scripts = sorted({s.stem for d in _task_dirs() if d.is_dir() for s in d.glob("*.ps1")})
    listing = ps(
        "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } | "
        "ForEach-Object { $_.TaskPath + $_.TaskName }",
        timeout=20,
    )
    lines = []
    if scripts:
        lines.append("Scripts (" + ", ".join(str(d) for d in _task_dirs()) + "):")
        lines += [f"  {s}" for s in scripts]
    tasks = [ln.strip() for ln in listing.splitlines() if ln.strip() and not listing.startswith(("ERROR", "TIMEOUT"))]
    if tasks and listing != "OK":
        lines.append("Scheduled tasks:")
        lines += [f"  {t}" for t in tasks]
    return "\n".join(lines) or (
        "No tasks found. Save a PowerShell script as "
        f"{_task_dirs()[1] / 'my-task.ps1'} (or create a Task Scheduler task) and run it by name."
    )


def task_run(name: str, input_text: str = "") -> str:
    """Run a saved task by name: ``<name>.ps1`` from the tasks folders (project
    ``.harness/tasks`` first, then ``~/.harness/tasks``), else a Scheduled Task.

    ``input_text`` reaches a script on stdin and as ``$env:JARVIS_INPUT``.
    ``name='?'`` lists what is available. Running anything asks for approval
    first (like ``run_bash``), since a task can do whatever its author wrote.
    """
    name = (name or "").strip()
    if not name or name == "?":
        return _list_tasks()
    script = _find_script(name)
    denied = approve(_task_preview(script) if script is not None else f"task_run scheduled task {name!r}")
    if denied:
        return denied
    try:
        if script is not None:
            env = {**os.environ, "JARVIS_INPUT": input_text or ""}
            r = run_argv([*_base_argv(), "-File", str(script)], timeout=TASK_TIMEOUT,
                         stdin=input_text or "", env=env, cwd=str(script.parent))
            out = r.stdout + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
            prefix = "" if r.returncode == 0 else f"ERROR (exit {r.returncode}): "
            return (prefix + out)[:MAX_TOOL_OUTPUT] or "OK"
        r = run_argv(["schtasks", "/Run", "/TN", name], timeout=30)
    except subprocess.TimeoutExpired:
        return f"ERROR: task timed out after {TASK_TIMEOUT}s"
    except OSError as e:
        return f"ERROR: could not start the task: {e}"
    if r.returncode == 0:
        note = " (input_text is ignored for Scheduled Tasks)" if input_text else ""
        return f"started scheduled task {name}{note}"
    return f"ERROR: no task named {name!r} — task_run name='?' lists scripts and scheduled tasks"


# ── system controls ─────────────────────────────────────────────────────────

def _speakers():
    from pycaw.pycaw import AudioUtilities  # lazy: Windows-only dependency

    return AudioUtilities.GetSpeakers().EndpointVolume


def _audio(action: str, value: str) -> str:
    import comtypes

    comtypes.CoInitialize()
    try:
        vol = _speakers()
        if action == "volume":
            if value == "":
                return f"volume is {round(vol.GetMasterVolumeLevelScalar() * 100)}%"
            level = max(0, min(100, int(float(value))))
            vol.SetMasterVolumeLevelScalar(level / 100, None)
            if level > 0:
                vol.SetMute(0, None)
            return f"volume set to {level}%"
        vol.SetMute(1 if action == "mute" else 0, None)
        return "muted" if action == "mute" else "unmuted"
    finally:
        comtypes.CoUninitialize()


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


def _battery() -> str:
    st = _SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return "ERROR: could not read power status"
    flag = st.BatteryFlag & 0xFF
    if flag == 128 or flag == 255:
        return "No battery (running on AC power)"
    pct = st.BatteryLifePercent & 0xFF
    parts = [f"Battery {pct}%" if pct != 255 else "Battery level unknown"]
    ac = st.ACLineStatus & 0xFF
    if flag & 8:
        parts.append("charging")
    elif ac == 1:
        parts.append("on AC power")
    else:
        parts.append("discharging")
    secs = st.BatteryLifeTime
    if ac != 1 and secs not in (0xFFFFFFFF, 0):
        parts.append(f"{secs // 3600}h{(secs % 3600) // 60:02d}m remaining")
    if st.SystemStatusFlag & 1:
        parts.append("battery saver on")
    return ", ".join(parts)


def _wifi(on: bool) -> str:
    from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState

    async def _toggle():
        if await Radio.request_access_async() != RadioAccessStatus.ALLOWED:
            return "ERROR: Windows denied access to the radios (Settings → Privacy → Radios)"
        radios = [r for r in await Radio.get_radios_async() if r.kind == RadioKind.WI_FI]
        if not radios:
            return "ERROR: no Wi-Fi adapter found"
        for r in radios:
            await r.set_state_async(RadioState.ON if on else RadioState.OFF)
        return f"Wi-Fi {'on' if on else 'off'}"

    return _run_async(_toggle)


_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _dark(mode: str) -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE, 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            light_now = winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
        except FileNotFoundError:
            light_now = 1
        light = {"dark_mode": 0, "light_mode": 1}.get(mode, 0 if light_now else 1)
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, light)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light)
    _win32.broadcast_setting_change("ImmersiveColorSet")
    return "light mode on" if light else "dark mode on"


def _brightness(value: str) -> str:
    if value == "":
        out = ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop)"
                 ".CurrentBrightness | Select-Object -First 1", timeout=15)
        return f"brightness is {out}%" if out.isdigit() else _no_brightness(out)
    level = max(0, min(100, int(float(value))))
    out = ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | "
             f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}} | Out-Null",
             timeout=15)
    return f"brightness set to {level}%" if out == "OK" else _no_brightness(out)


def _no_brightness(detail: str) -> str:
    return ("ERROR: this display doesn't support software brightness (external monitors usually "
            f"don't — only built-in laptop panels do). {detail[:200]}")


def system_control(action: str, value: str = "") -> str:
    """System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off,
    sleep, lock, dark_mode, light_mode, toggle_dark, brightness}. value optional."""
    a = (action or "").strip().lower()
    v = str(value or "").strip()
    try:
        if a in ("volume", "mute", "unmute"):
            return _audio(a, v)
        if a == "battery":
            return _battery()
        if a in ("wifi_on", "wifi_off"):
            return _wifi(a == "wifi_on")
        if a == "sleep":
            ok = ctypes.WinDLL("powrprof").SetSuspendState(False, False, False)
            return "going to sleep" if ok else "ERROR: sleep was refused (check power settings)"
        if a == "lock":
            return "locked" if _win32.user32.LockWorkStation() else "ERROR: could not lock the workstation"
        if a in ("dark_mode", "light_mode", "toggle_dark"):
            return _dark(a)
        if a == "brightness":
            return _brightness(v)
    except ValueError:
        return f"ERROR: bad value {value!r} for {action}"
    except ImportError as e:
        return f"ERROR: missing Windows component ({e.name}) — reinstall Jarvis (pip install -e .)"
    except Exception as e:
        return f"ERROR: {action} failed: {e}"
    return f"ERROR: unknown action {action}"
