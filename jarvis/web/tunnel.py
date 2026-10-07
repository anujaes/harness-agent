"""Public HTTPS link for the web remote ("Anywhere" mode).

Runs a tunnel client next to the local server and reads the public URL it
prints:

* **Cloudflare quick tunnel** (``cloudflared``) — no account, no warning
  page; preferred when installed.
* **ngrok** — needs a (free) account + authtoken; free domains show a one-time
  "Visit site" interstitial.

The tunnel only forwards to ``127.0.0.1:<port>``. Requests arriving through it
never get the token handed out (see ``handler._may_hand_out_token``), so the
public link must carry ``?token=`` — the QR / link built by ``public_link``.

While a tunnel runs on macOS it also holds a ``caffeinate`` assertion, so the
Mac stays awake (the *display* may still turn off) and Wi-Fi + the tunnel
survive an idle sleep. Closing the lid still sleeps the Mac. On Windows a
helper process holds ``SetThreadExecutionState(ES_SYSTEM_REQUIRED)`` instead.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..utils.osinfo import CREATE_NEW_PROCESS_GROUP, IS_WINDOWS, hidden_subprocess_kwargs

PROVIDERS = ("cloudflare", "ngrok")
_BINARIES = {"cloudflare": "cloudflared", "ngrok": "ngrok"}
if IS_WINDOWS:
    # PowerShell 5.1 has no `&&`, so the ngrok hint spells out the two steps.
    INSTALL_HINTS = {
        "cloudflare": "winget install Cloudflare.cloudflared",
        "ngrok": "winget install ngrok.ngrok, then ngrok config add-authtoken <token>",
    }
else:
    INSTALL_HINTS = {
        "cloudflare": "brew install cloudflared",
        "ngrok": "brew install ngrok && ngrok config add-authtoken <token>",
    }
START_TIMEOUT = 40.0


# Windows stand-in for `caffeinate -i -s -w <pid>`: hold ES_SYSTEM_REQUIRED until <pid> exits.
_WIN_KEEP_AWAKE = """\
import ctypes, sys
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.OpenProcess.restype = ctypes.c_void_p
k.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
k.CloseHandle.argtypes = (ctypes.c_void_p,)
h = k.OpenProcess(0x00100000, False, int(sys.argv[1]))  # SYNCHRONIZE
if not h:
    sys.exit(1)
k.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
k.WaitForSingleObject(h, 0xFFFFFFFF)  # INFINITE
k.CloseHandle(h)
"""


def keep_awake_command(pid: int | None = None) -> list[str] | None:
    """``caffeinate`` that blocks idle sleep (``-i``; plugged in, also ``-s``)
    for as long as process ``pid`` lives — so a crashed Jarvis can never leave
    the Mac awake. ``None`` where there is no ``caffeinate`` (not macOS)."""
    if IS_WINDOWS:
        # No caffeinate on Windows: a tiny Python helper sets the execution state
        # (system stays awake, display may still turn off) and waits on Jarvis's
        # process handle, so the hold ends with Jarvis even if it crashes. The
        # state is per-process, so Windows clears it when the helper exits.
        return [sys.executable, "-c", _WIN_KEEP_AWAKE, str(pid or os.getpid())]
    exe = shutil.which("caffeinate")
    if not exe:
        return None
    return [exe, "-i", "-s", "-w", str(pid or os.getpid())]


def awake_label() -> str:
    """Footer note shown while a tunnel keeps this computer awake."""
    return f"{'PC' if IS_WINDOWS else 'Mac'} kept awake (display may sleep)"

_CLOUDFLARE_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_NGROK_URL = re.compile(r"https://[a-zA-Z0-9.-]+\.ngrok(?:-free)?\.(?:app|dev|io)")


def _windows_install_dirs(provider: str) -> list[str]:
    """Places installers put the tunnel apps that may not be on PATH yet.

    winget links portable apps into ``WinGet\\Links`` but only terminals opened
    afterwards see the new PATH; the MSI / zip installs use Program Files or a
    folder under the user profile.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    roots = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    dirs = [os.path.join(local, "Microsoft", "WinGet", "Links")] if local else []
    folder = "cloudflared" if provider == "cloudflare" else "ngrok"
    dirs += [os.path.join(r, folder) for r in roots if r]
    if local:
        dirs.append(os.path.join(local, folder))
        dirs.append(os.path.join(local, "Programs", folder))
    if provider == "ngrok":
        dirs.append(os.path.join(os.path.expanduser("~"), "ngrok"))
    return dirs


def binary_for(provider: str) -> str | None:
    name = _BINARIES.get(provider)
    if not name:
        return None
    found = shutil.which(name)
    if found or not IS_WINDOWS:
        return found
    for folder in _windows_install_dirs(provider):
        cand = os.path.join(folder, f"{name}.exe")
        if os.path.isfile(cand):
            return cand
    return None


def available_providers() -> list[str]:
    return [p for p in PROVIDERS if binary_for(p)]


def pick_provider(preference: str = "auto") -> str | None:
    """The provider to use: the preferred one if installed, else the first installed."""
    pref = (preference or "auto").strip().lower()
    if pref in PROVIDERS and binary_for(pref):
        return pref
    found = available_providers()
    return found[0] if found else None


def command_for(provider: str, port: int) -> list[str]:
    target = f"http://127.0.0.1:{port}"
    if provider == "cloudflare":
        return [binary_for(provider) or "cloudflared", "tunnel", "--no-autoupdate", "--url", target]
    return [binary_for(provider) or "ngrok", "http", target, "--log", "stdout", "--log-format", "json"]


def parse_url(provider: str, line: str) -> str | None:
    """Public URL from one line of tunnel output, if it has one."""
    if provider == "ngrok":
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            rec = None
        if isinstance(rec, dict) and str(rec.get("url", "")).startswith("https://"):
            return str(rec["url"]).rstrip("/")
        m = _NGROK_URL.search(line or "")
        return m.group(0) if m else None
    m = _CLOUDFLARE_URL.search(line or "")
    return m.group(0) if m else None


def parse_error(provider: str, line: str) -> str | None:
    """A human-readable failure from one line of output, if it is one."""
    text = line or ""
    if provider == "ngrok":
        try:
            rec = json.loads(text)
        except (ValueError, TypeError):
            rec = None
        if isinstance(rec, dict) and rec.get("lvl") in ("eror", "crit", "error"):
            err = str(rec.get("err") or rec.get("msg") or "")
            if "authtoken" in err.lower() or "ERR_NGROK_4018" in err:
                return "ngrok needs a free account — run: ngrok config add-authtoken <token>"
            if "ERR_NGROK_108" in err or "simultaneous" in err.lower():
                return "ngrok is already running elsewhere (free plan allows one session)"
            return err.splitlines()[0][:200] if err else None
        return None
    low = text.lower()
    if "failed to" in low and ("quick tunnel" in low or "request" in low) and "error" in low:
        return text.strip()[:200]
    return None


DNS_WAIT = 30.0


def _doh_resolves(host: str) -> bool:
    """True once ``host`` has an A/AAAA record at 1.1.1.1 (DNS over HTTPS)."""
    import urllib.request

    if not host:
        return True
    req = urllib.request.Request(
        f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
        headers={"Accept": "application/dns-json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("Status") == 0 and bool(data.get("Answer"))
    except Exception:
        return False


def _kill_tree(pid: int) -> None:
    """Windows: end ``pid`` and its children (a wrapper script's tunnel app too)."""
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False, **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def public_link(public_url: str, token: str) -> str:
    return f"{public_url.rstrip('/')}/?token={token}"


@dataclass
class Tunnel:
    """One tunnel process. ``status``: starting → live | error; stopped after stop()."""

    provider: str
    port: int
    on_change: Callable[["Tunnel"], None] | None = None
    keep_awake: bool = True
    status: str = "starting"
    url: str = ""
    error: str = ""
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _stopped: bool = field(default=False, repr=False)
    _awake: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def awake(self) -> bool:
        """True while this tunnel is holding the Mac awake."""
        return self._awake is not None and self._awake.poll() is None

    def _hold_awake(self) -> None:
        cmd = keep_awake_command() if self.keep_awake else None
        if cmd is None:
            return
        # Windows: no console window for the helper; POSIX: its own session.
        detach = hidden_subprocess_kwargs() if IS_WINDOWS else {"start_new_session": True}
        try:
            self._awake = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **detach,
            )
        except OSError:
            self._awake = None  # best effort — the tunnel works without it

    def _release_awake(self) -> None:
        proc, self._awake = self._awake, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def start(self) -> None:
        if IS_WINDOWS:
            # No console of its own (no window flashing up, no Ctrl+C from ours).
            detach = {"creationflags": hidden_subprocess_kwargs().get("creationflags", 0)
                      | CREATE_NEW_PROCESS_GROUP}
        else:
            detach = {"start_new_session": True}
        try:
            self._proc = subprocess.Popen(
                command_for(self.provider, self.port),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=dict(os.environ),
                **detach,
            )
        except OSError as exc:
            self._fail(f"could not start {_BINARIES[self.provider]}: {exc}")
            return
        self._hold_awake()
        # Never leave a public tunnel running after Jarvis exits.
        atexit.register(self.stop)
        threading.Thread(target=self._read, daemon=True, name=f"tunnel-{self.provider}").start()
        threading.Thread(target=self._deadline, daemon=True, name="tunnel-deadline").start()

    def _read(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        last_lines: list[str] = []
        for line in proc.stdout:
            if self._stopped:
                break
            last_lines = (last_lines + [line.strip()])[-6:]
            if self.status == "starting" and not self.url:
                url = parse_url(self.provider, line)
                if url:
                    self.url = url
                    if self.provider == "cloudflare":
                        # A fresh quick-tunnel hostname takes a few seconds to
                        # exist in DNS; showing the QR before that makes phones
                        # (and the router's DNS) cache "not found" for minutes.
                        threading.Thread(target=self._go_live_when_resolvable, daemon=True,
                                         name="tunnel-dns").start()
                    else:
                        self._go_live()
                    continue
                err = parse_error(self.provider, line)
                if err:
                    self._fail(err)
        code = proc.wait()
        if not self._stopped and self.status != "error":
            tail = next((ln for ln in reversed(last_lines) if ln), "")
            self._fail(f"{_BINARIES[self.provider]} exited ({code}){': ' + tail[:160] if tail else ''}")

    def _go_live(self) -> None:
        if self._stopped or self.status != "starting":
            return
        self.status = "live"
        self._changed()

    def _go_live_when_resolvable(self) -> None:
        """Wait until Cloudflare's own resolver knows the host (DNS-over-HTTPS,
        so no local cache ever sees an early "not found"), then go live."""
        from urllib.parse import urlparse

        host = urlparse(self.url).hostname or ""
        end = time.monotonic() + DNS_WAIT
        while time.monotonic() < end and not self._stopped:
            if _doh_resolves(host):
                break
            time.sleep(1.0)
        self._go_live()

    def _deadline(self) -> None:
        end = time.monotonic() + START_TIMEOUT + (DNS_WAIT if self.provider == "cloudflare" else 0)
        while time.monotonic() < end:
            if self.status != "starting" or self._stopped:
                return
            time.sleep(0.25)
        self._fail(f"{_BINARIES[self.provider]} did not report a public URL in {int(START_TIMEOUT)}s")
        self.stop(keep_status=True)

    def _fail(self, message: str) -> None:
        if self._stopped or self.status == "error":
            return
        self.status = "error"
        self.error = message
        self._release_awake()
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:
                pass

    def stop(self, *, keep_status: bool = False) -> None:
        self._stopped = True
        self._release_awake()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if IS_WINDOWS:
                    _kill_tree(proc.pid)
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if not keep_status:
            self.status = "stopped"
