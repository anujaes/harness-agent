"""PowerShell helper + the ``powershell`` tool (the Windows twin of ``applescript``)."""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile

from ...constants import MAX_TOOL_OUTPUT
from ...utils.osinfo import find_powershell, hidden_subprocess_kwargs

# Prepended to every script: UTF-8 in/out (PowerShell 5.1 defaults to the OEM
# code page) and no progress bars on stderr.
_PRELUDE = (
    "$ProgressPreference='SilentlyContinue';"
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$OutputEncoding=[System.Text.Encoding]::UTF8;"
    "[Console]::InputEncoding=[System.Text.Encoding]::UTF8;"
)

# Wraps model-written scripts (the ``powershell`` tool). Without it, Windows
# PowerShell serialises errors and warnings to stderr as CLIXML when it isn't
# attached to a console, non-terminating errors (Write-Error, a failed cmdlet)
# exit 0, and a failing native command's exit code is lost. The script travels
# base64-encoded and is compiled inside the ``try`` (so parse errors are
# reported too, and ``using`` / ``param`` stay legal at its top); errors and
# warnings go to stderr as plain text. A native program writing to stderr
# (git/npm progress) is not a failure — its exit code decides.
_TOOL_WRAPPER = """$global:LASTEXITCODE = 0
$__jarvis_failed = $false
try {{
  $__jarvis_src = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64}'))
  & ([scriptblock]::Create($__jarvis_src)) 2>&1 3>&1 | ForEach-Object {{
    if ($_ -is [System.Management.Automation.ErrorRecord]) {{
      if ($_.FullyQualifiedErrorId -notlike 'NativeCommandError*') {{ $__jarvis_failed = $true }}
      [Console]::Error.WriteLine($_.ToString())
    }} elseif ($_ -is [System.Management.Automation.WarningRecord]) {{
      [Console]::Error.WriteLine('WARNING: ' + $_.Message)
    }} else {{ $_ }}
  }} | Out-String -Stream -Width 4096
}} catch {{ [Console]::Error.WriteLine($_.ToString()); exit 1 }}
if ($LASTEXITCODE) {{ exit $LASTEXITCODE }}
if ($__jarvis_failed) {{ exit 1 }}
"""

# -EncodedCommand is limited by the 32K command line (UTF-16 → base64 ≈ ×2.7);
# longer scripts go through a temporary .ps1 file instead.
_MAX_ENCODED_SCRIPT = 8000


def _base_argv() -> list[str]:
    return [find_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]


def _kill_tree(proc: subprocess.Popen) -> None:
    """End ``proc`` and everything it started (a plain kill leaves children running)."""
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       **hidden_subprocess_kwargs())
    if proc.poll() is None:
        proc.kill()


def run_argv(argv: list[str], timeout: float, stdin: str | None = None,
             **popen_kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` for PowerShell/helper processes, killing the whole tree on timeout.

    Raises ``subprocess.TimeoutExpired`` / ``OSError`` like ``subprocess.run``.
    """
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        **{**hidden_subprocess_kwargs(), **popen_kwargs},
    )
    try:
        out, err = proc.communicate(stdin if stdin is not None else "", timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def run_ps(script: str, timeout: float = 30, stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run a PowerShell script and return the completed process (UTF-8 decoded).

    Raises ``subprocess.TimeoutExpired`` / ``OSError`` like ``subprocess.run``.
    """
    body = _PRELUDE + "\n" + script
    tmp_path = None
    if len(body) <= _MAX_ENCODED_SCRIPT:
        encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
        argv = _base_argv() + ["-EncodedCommand", encoded]
    else:
        # utf-8-sig: Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI.
        fd, tmp_path = tempfile.mkstemp(suffix=".ps1", prefix="jarvis-")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(body)
        argv = _base_argv() + ["-File", tmp_path]
    try:
        return run_argv(argv, timeout=timeout, stdin=stdin)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def ps(script: str, timeout: float = 30, stdin: str | None = None) -> str:
    """Run a script and format the result the way the macOS ``_osa`` helper does."""
    try:
        r = run_ps(script, timeout=timeout, stdin=stdin)
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except OSError as e:
        return f"ERROR: could not start PowerShell: {e}"
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"ERROR (exit {r.returncode}): {(r.stderr or '').strip()}\n{out}".rstrip()
    return out or "OK"


# PowerShell treats the typographic single quotes as quote characters too.
_SINGLE_QUOTES = "'‘’‚‛"


def ps_quote(value: str) -> str:
    """Quote ``value`` as a PowerShell single-quoted string literal (doubling every quote char)."""
    return "'" + "".join(ch * 2 if ch in _SINGLE_QUOTES else ch for ch in (value or "")) + "'"


def approve(command: str) -> str | None:
    """Ask the user before running model-written code; ``"USER DENIED"`` when refused.

    Always asks (unless auto-approve is on): PowerShell aliases (``cat``,
    ``echo``, ``dir`` …) make "looks read-only" checks unreliable here.
    """
    from ..shell import _bash_lock, ask_approval

    with _bash_lock:
        return ask_approval(command, allow_readonly_shortcut=False)


def powershell(code: str, timeout: int = 60) -> str:
    """Run arbitrary PowerShell (after the same approval as ``run_bash``)."""
    from ..shell import is_dangerous

    if not (code or "").strip():
        return "ERROR: code is empty"
    if is_dangerous(code):
        return "BLOCKED: dangerous command"
    denied = approve(code)
    if denied:
        return denied
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    try:
        # Long scripts automatically go through a temporary .ps1 (see run_ps).
        r = run_ps(_TOOL_WRAPPER.format(b64=b64), timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except OSError as e:
        return f"ERROR: could not start PowerShell: {e}"
    out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
    if r.returncode != 0:
        text = f"ERROR (exit {r.returncode}): {err}\n{out}".rstrip()
    else:
        text = (out + (f"\n[stderr]\n{err}" if err else "")).strip() or "OK"
    return text[:MAX_TOOL_OUTPUT]
