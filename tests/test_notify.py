"""Desktop notifications: command building per OS and the sync result API."""
import base64
import sys

import pytest

from jarvis.utils import notify


def _decoded_script(cmd: list[str]) -> str:
    return base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")


def test_windows_toast_escapes_xml_and_uses_powershell_app_id(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify, "_windows_powershell", lambda: r"C:\ps\powershell.exe")
    cmd, timeout = notify._notify_cmd("Build <done> & ok", "héllo — 日本\n'@ \"quoted\"")
    assert cmd[0] == r"C:\ps\powershell.exe" and "-WindowStyle" in cmd and timeout > 5
    script = _decoded_script(cmd)
    assert "<text>Build &lt;done&gt; &amp; ok</text>" in script
    assert "<text>héllo — 日本 '@ \"quoted\"</text>" in script  # one line: can't end the here-string
    assert notify._WIN_TOAST_APP_ID in script


def test_sync_reports_unsupported_platform(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "sunos5")
    assert notify.desktop_notify_sync("t", "m") == (False, "desktop notifications are not supported on sunos5")
    assert notify.desktop_notify("t", "m") is False


def test_sync_surfaces_helper_errors(monkeypatch):
    monkeypatch.setattr(notify, "_notify_cmd", lambda t, m: (["helper"], 5))

    class R:
        returncode, stdout, stderr = 1, b"", b"Toast failed: access denied\nmore"

    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: R())
    assert notify.desktop_notify_sync("t", "m") == (False, "Toast failed: access denied")
    R.returncode, R.stderr = 0, b""
    assert notify.desktop_notify_sync("t", "m") == (True, "")


def test_async_variant_runs_the_sync_one_in_a_thread(monkeypatch):
    import threading

    done = threading.Event()
    monkeypatch.setattr(notify, "_notify_cmd", lambda t, m: (["helper"], 5))
    monkeypatch.setattr(notify, "desktop_notify_sync", lambda t, m: done.set() or (True, ""))
    assert notify.desktop_notify("t", "m") is True
    assert done.wait(2)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS osascript")
def test_macos_command_is_unchanged():
    cmd, timeout = notify._notify_cmd('Ti"tle', "msg")
    assert cmd == ["osascript", "-e", 'display notification "msg" with title "Ti\\"tle"'] and timeout == 5
