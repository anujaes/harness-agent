"""Best-effort desktop notifications (macOS · Windows · Linux).

``desktop_notify`` is fire-and-forget (the UI never waits); the notify tool
uses ``desktop_notify_sync`` to learn whether the notification was shown.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import threading
from xml.sax.saxutils import escape as _xml_escape

# Windows only shows toasts for a registered AppUserModelID. PowerShell's own
# id is registered on every install, so toasts appear without a Start-menu
# shortcut of our own.
_WIN_TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_WIN_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
__TOAST_XML__
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APP_ID__').Show($toast)
"""


def _escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:200]


def _windows_powershell() -> str | None:
    """Windows PowerShell 5.1 — PowerShell 7 can't load WinRT toast types."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    exe = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return exe if os.path.isfile(exe) else shutil.which("powershell")


def _toast_xml(title: str, message: str) -> str:
    # One line: the XML sits inside a PowerShell here-string, where a line
    # starting with ``'@`` would end the string early.
    return ('<toast><visual><binding template="ToastGeneric">'
            f"<text>{_xml_escape((title or '')[:80])}</text>"
            f"<text>{_xml_escape((message or '')[:200])}</text>"
            "</binding></visual></toast>").replace("\r", " ").replace("\n", " ")


def _windows_toast_cmd(title: str, message: str) -> list[str] | None:
    ps = _windows_powershell()
    if not ps:
        return None
    script = (_WIN_TOAST_PS.replace("__TOAST_XML__", _toast_xml(title, message))
              .replace("__APP_ID__", _WIN_TOAST_APP_ID))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


def _notify_cmd(title: str, message: str) -> tuple[list[str] | None, float]:
    """The command that shows the notification here and its timeout."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        return ["osascript", "-e",
                f'display notification "{_escape(message)}" with title "{_escape(title)}"'], 5
    if sys.platform == "win32":
        return _windows_toast_cmd(title, message), 20  # PowerShell starts slowly
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        return ["notify-send", "--app-name=Jarvis", title[:80], message[:200]], 5
    return None, 0


def desktop_notify_sync(title: str, message: str) -> tuple[bool, str]:
    """Show a system notification and wait for it: ``(shown, error)``."""
    cmd, timeout = _notify_cmd(title, message)
    if not cmd:
        return False, f"desktop notifications are not supported on {sys.platform}"
    kwargs = {}
    if sys.platform == "win32":
        from .osinfo import hidden_subprocess_kwargs

        kwargs = hidden_subprocess_kwargs()
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        return False, f"notification helper timed out after {timeout:g}s"
    except OSError as e:
        return False, f"could not run {os.path.basename(cmd[0])}: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or b"").decode("utf-8", errors="replace").strip()
        return False, err.splitlines()[0] if err else f"exit status {r.returncode}"
    return True, ""


def desktop_notify(title: str, message: str) -> bool:
    """Show a system notification without blocking; False when unsupported."""
    cmd, _timeout = _notify_cmd(title, message)
    if not cmd:
        return False

    def _run() -> None:
        try:
            desktop_notify_sync(title, message)
        except Exception:
            pass

    threading.Thread(target=_run, name="desktop-notify", daemon=True).start()
    return True
