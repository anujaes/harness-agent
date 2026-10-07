"""Shell execution tool with approval prompt.

Commands run in the platform shell (:func:`jarvis.utils.osinfo.shell_kind`):
``/bin/sh`` on macOS / Linux; Git Bash, PowerShell or cmd.exe on Windows.
On Windows every command runs in a Job Object so a timeout (or ``bg_kill``)
takes down the whole process tree, not just the shell — ``subprocess``'s own
timeout only kills the direct child.
"""
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from typing import NamedTuple

from rich.markup import escape

from ..console import console
from ..constants import CWD, MAX_TOOL_OUTPUT, DEFAULT_BASH_TIMEOUT
from ..utils import osinfo
from .. import file_changes, state

IS_WINDOWS = osinfo.IS_WINDOWS

_bash_lock = threading.Lock()

# How often a running command checks whether its turn was stopped (Esc / web
# Stop / New chat). Short enough to feel instant, long enough to cost nothing.
_CANCEL_POLL = 0.2

CANCELLED_RESULT = "CANCELLED: the command was stopped because the turn was interrupted"


class _Cancelled(Exception):
    """The turn running this command was cancelled; the command is stopped."""


def _descendants(root: int) -> list[int]:
    """Every process started (directly or not) by ``root``, via ``ps``."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop ``proc`` and everything it started: TERM, a moment, then KILL.

    The shell (``sh -c …``) is rarely the process doing the work; killing only
    it would leave e.g. ``pytest`` running with the output pipes open.
    Children are collected first — once the shell dies they're reparented
    and can't be found from it any more.
    """
    pids = _descendants(proc.pid) if os.name == "posix" else []
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        try:
            proc.send_signal(sig)
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
            if sig == signal.SIGTERM and not any(_pid_alive(p) for p in pids):
                return
        except subprocess.TimeoutExpired:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=2)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return "", ""
    return out or "", err or ""


def _run_process(cmd: str, timeout: int, env: dict) -> tuple[int, str, str]:
    """Run ``cmd`` like ``subprocess.run(shell=True, capture_output=True)``,
    but stop it as soon as the turn is cancelled.

    Raises ``subprocess.TimeoutExpired`` on timeout and ``_Cancelled`` on a
    cancelled turn — in both cases after the whole process tree is gone, so
    the shell lock is never held by a command nobody is waiting for.

    On Windows the command runs in the configured shell (Git Bash /
    PowerShell / cmd, see :func:`_execute`) inside a Job Object instead:
    ``shell=True`` would always mean cmd.exe there, and ``_kill_tree`` needs
    ``ps``, which Windows doesn't have.
    """
    if IS_WINDOWS:
        return tuple(_execute(cmd, timeout, env=env, cancellable=True))
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(CWD),
        env=env,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                # Repeated communicate() calls never lose output.
                out, err = proc.communicate(timeout=_CANCEL_POLL)
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired:
                pass
            if state.turn_cancelled():
                _kill_tree(proc)
                _drain(proc)
                raise _Cancelled()
            if time.monotonic() >= deadline:
                _kill_tree(proc)
                _drain(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:
        # KeyboardInterrupt injected by Esc, or anything else: never leave the
        # command running behind us.
        if proc.poll() is None:
            _kill_tree(proc)
            _drain(proc)
        raise

# Read-only agent tools (search_code, git_status, …) must not block on approval.
_SAFE_READONLY = re.compile(
    r"^(?:"
    r"rg\b|grep\b|"
    r"git(?:\s+--no-pager)?\s+(?:status|log|diff|show|rev-parse|branch|remote)\b|"
    r"which\b|file\b|wc\b|head\b|tail\b|cat\b|pwd\b|echo\b|test\b|\["
    r")",
    re.IGNORECASE,
)

# Windows read-only commands (PowerShell, cmd.exe and Git Bash all run here).
# Deliberately an allowlist that is obviously safe rather than a clever one —
# anything it is unsure about asks for approval:
# * no character that can chain, redirect, group, splat, expand or escape:
#   ; & | (except plain pipes) < > ` ^ { } ( ) @ $ % , and no line breaks;
# * the first word is a known read-only command and its arguments are plain
#   words — no UNC paths (\\host\share would leak NTLM credentials), no
#   output options (sort /o, -o, --output, rg --pre);
# * a pipe stage is a bare formatter with at most simple options.
_WIN_UNSAFE_CHARS = re.compile(r"[;&<>`^{}()@$%,\r\n]")
_WIN_READONLY_HEADS = {
    # PowerShell
    "get-childitem", "gci", "dir", "ls", "get-content", "gc", "type", "cat", "get-item", "gi",
    "get-itemproperty", "gp", "get-location", "gl", "pwd", "select-string", "sls", "test-path",
    "resolve-path", "rvpa", "get-command", "gcm", "get-process", "gps", "get-date", "get-filehash",
    "split-path", "join-path", "write-output", "echo",
    # cmd.exe / Windows programs
    "where", "where.exe", "findstr", "findstr.exe", "hostname", "whoami", "ver",
    # POSIX tools (Git Bash; also PowerShell aliases)
    "rg", "rg.exe", "grep", "which", "wc", "head", "tail", "test",
}
_WIN_GIT_READONLY = {"status", "log", "diff", "show", "rev-parse"}
_WIN_GIT_LISTING = {"branch": {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all"},
                    "remote": {"-v", "--verbose"}}
_WIN_PIPE_STAGES = {
    "select-object", "select", "sort-object", "sort", "measure-object", "measure",
    "format-table", "ft", "format-list", "fl", "format-wide", "fw", "out-string",
    "group-object", "group", "more", "findstr", "select-string", "sls", "head", "tail", "wc",
}
_WIN_PIPE_OPTION = re.compile(
    r"^(?:-(?:first|last|skip|head|tail|n|c|l|w)\s+\d+|-\d+|-(?:descending|unique|autosize|wrap|stream|"
    r"simplematch|casesensitive|quiet)|(?:-property\s+)?[a-z_][\w.]*|/[ivn])$",
    re.IGNORECASE,
)
_WIN_FORBIDDEN_ARG = re.compile(r"^(?:/o|-o|--output(?:=.*)?|--pre(?:=.*)?|-outfile|-outvariable|-ov)$",
                                re.IGNORECASE)


def _split_words(text: str) -> list[str] | None:
    try:
        lex = shlex.shlex(text, posix=True)
        lex.whitespace_split = True
        lex.escape = ""  # backslashes are path separators here
        return list(lex)
    except ValueError:
        return None


def _plain_args(args: list[str]) -> bool:
    for a in args:
        # UNC anywhere, also as `-Path:\\host\x` (PowerShell's colon syntax) and
        # with mixed separators (`/\host`, `\/host`), which Windows reads as UNC too.
        if _UNC_RE.search(a) or _WIN_FORBIDDEN_ARG.match(a):
            return False
    return True


_UNC_RE = re.compile(r"[\\/]{2}")


def _windows_head_ok(words: list[str]) -> bool:
    head, args = words[0].lower(), words[1:]
    if not _plain_args(args):
        return False
    if head in ("git", "git.exe"):
        rest = [a for a in args if a.lower() != "--no-pager"]
        if not rest:
            return False
        sub, sub_args = rest[0].lower(), rest[1:]
        if sub in _WIN_GIT_READONLY:
            return True
        allowed = _WIN_GIT_LISTING.get(sub)
        return allowed is not None and all(a.lower() in allowed for a in sub_args)
    return head in _WIN_READONLY_HEADS


def _windows_pipe_ok(stage: str) -> bool:
    words = _split_words(stage)
    if not words or words[0].lower() not in _WIN_PIPE_STAGES or not _plain_args(words[1:]):
        return False
    # Options pair up ("-First 3", "-Property Name"); anything else needs approval.
    rest = " ".join(words[1:])
    tokens = re.findall(r"-(?:first|last|skip|head|tail|n|c|l|w|property)\s+\S+|\S+", rest, re.IGNORECASE)
    return all(_WIN_PIPE_OPTION.match(t) for t in tokens)


def _is_safe_windows_readonly(cmd: str) -> bool:
    cmd = (cmd or "").strip()
    if not cmd or _WIN_UNSAFE_CHARS.search(cmd) or "||" in cmd:
        return False
    first, *rest = [part.strip() for part in cmd.split("|")]
    words = _split_words(first)
    if not words or not _windows_head_ok(words):
        return False
    return all(part and _windows_pipe_ok(part) for part in rest)


def _is_safe_readonly_command(cmd: str) -> bool:
    if IS_WINDOWS:
        # cat / echo / head are PowerShell aliases and `;` `>` chain or write
        # there too: only the strict check counts, never the POSIX prefix list.
        return _is_safe_windows_readonly(cmd)
    return bool(_SAFE_READONLY.match((cmd or "").strip()))


_DANGEROUS = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero"]

# A drive root, the Windows directory or a user profile as a whole-word
# argument: C:  C:\  "C:/"  C:\*  C:\Windows  C:\Windows\System32  C:\Users
# C:\Users\<name>  $env:SystemRoot  %SystemRoot%  $HOME  $env:USERPROFILE
# %USERPROFILE%  ~  — never a folder inside them (C:\Users\me\proj\build).
_WIN_ROOT = (
    r"""["']?(?:[a-z]:[\\/]*|[a-z]:[\\/]+windows(?:[\\/]+system32)?[\\/]*"""
    r"""|[a-z]:[\\/]+users(?:[\\/]+[^\\/\s"';&|)]+)?[\\/]*"""
    r"""|\$env:(?:systemroot|windir|systemdrive|userprofile|homepath|home)[\\/]*"""
    r"""|%(?:systemroot|windir|systemdrive|userprofile|homepath)%[\\/]*"""
    r"""|\$home[\\/]*|~[\\/]*)"""
    r"""\*?(?:\.\*)?["']?(?=\s|$|[;&|)])"""
)
# Git Bash's view of a drive root: /c  /c/  /c/*  (only for `rm` — `/s` is a cmd flag)
_MSYS_ROOT = r"""["']?/[a-z]/?\*?["']?(?=\s|$|[;&|)])"""
_DANGEROUS_WINDOWS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bformat(?:\.com)?\s+[a-z]:",
        r"\bdiskpart(?:\.exe)?\b",
        r"\b(?:format-volume|clear-disk|initialize-disk|remove-partition)\b",
        r"\bvssadmin(?:\.exe)?\s+delete\s+shadows\b",
        r"\bbcdedit(?:\.exe)?\b.*\s/delete\b",
        # rd /s /q C:\ · del /s /q C:\* · Remove-Item -Recurse -Force C:\ · rm -rf /c
        r"(?:^|[\s;&|(])(?:rm|rd|rmdir|del|erase|remove-item|ri)(?:\.exe)?\s(?:[^;&|\n]*\s)?" + _WIN_ROOT,
        r"(?:^|[\s;&|(])rm(?:\.exe)?\s(?:[^;&|\n]*\s)?" + _MSYS_ROOT,
        # Get-ChildItem C:\ -Recurse | Remove-Item
        r"\b(?:get-childitem|gci|ls|dir)\s+(?:[^;&|\n]*\s)?" + _WIN_ROOT + r"[^;&\n]*\|\s*(?:remove-item|ri|rm|del)\b",
    )
]


def is_dangerous(cmd: str) -> bool:
    if any(d in cmd for d in _DANGEROUS):
        return True
    return IS_WINDOWS and any(p.search(cmd) for p in _DANGEROUS_WINDOWS)


def ask_approval(cmd: str, *, allow_readonly_shortcut: bool = True) -> str | None:
    """Ask the user before running ``cmd`` (unless auto-approved / read-only).

    Returns ``"USER DENIED"`` when refused, else None. Call with
    ``_bash_lock`` held so approval prompts never overlap. Callers whose
    ``cmd`` isn't a ``run_bash`` command line (a PowerShell script, a task)
    pass ``allow_readonly_shortcut=False`` so the read-only allowlist never
    approves it.
    """
    if state.auto_approve or (allow_readonly_shortcut and _is_safe_readonly_command(cmd)):
        return None
    # The command is model-written text: a `[x for x in y]` in it must print as
    # text, not be read as a Rich style tag (the TUI crashes on an unknown one).
    from ..subagents.context import current as _subagent

    sub = _subagent()
    who = ""
    if sub is not None:
        who = f"[bold]{escape(sub[1].name)}[/] (agent) "
        sub[0].set_activity(sub[1], "Waiting for your approval")
    console.print(f"[yellow]→ {who}run:[/] [cyan]{escape(cmd)}[/]")
    try:
        approve = getattr(console, "prompt_shell_approval", None)
        if approve is not None:
            ok = approve(cmd).strip().lower()
        else:
            ok = console.input(
                "[dim]approve? [Y/n/a=always] [/]"
            ).strip().lower()
    except (RuntimeError, EOFError):
        ok = ""
    if ok == "a":
        state.auto_approve = True
    elif ok == "n" or ok == "":
        return "USER DENIED"
    if state.turn_cancelled():
        raise KeyboardInterrupt()
    return None


_CMD_ECHO_MAX = 1500


def _format_result(cmd: str, code: int, out: str) -> str:
    """``$ cmd`` / ``exit=N`` / output, kept within MAX_TOOL_OUTPUT. Long output
    keeps its start and its end (first errors *and* the final summary) with a
    note in between, instead of silently keeping only the tail."""
    if len(cmd) > _CMD_ECHO_MAX:
        cmd = cmd[:1000] + f" … [command truncated, {len(cmd):,} chars]"
    header = f"$ {cmd}\nexit={code}\n"
    budget = max(2000, MAX_TOOL_OUTPUT - len(header))
    if len(out) > budget:
        note_room = 120
        head_n = (budget - note_room) // 3
        tail_n = budget - note_room - head_n
        omitted = len(out) - head_n - tail_n
        out = (
            f"{out[:head_n]}\n… [{omitted:,} chars of output omitted — rerun "
            f"piped through grep / head / tail to see them] …\n{out[-tail_n:]}"
        )
    return header + out


# ── process trees ──────────────────────────────────────────────────────────

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _is_msys_program(exe: str) -> bool:
    """Git Bash's own tools (``usr\\bin\\bash.exe``, grep, …) link msys-2.0.dll."""
    return os.path.isfile(os.path.join(os.path.dirname(exe), "msys-2.0.dll"))


def _quote_windows_arg(arg: str, msys: bool = False) -> str:
    """Always-quoted argument for a Windows command line.

    ``subprocess.list2cmdline`` leaves space-free arguments bare, and MSYS
    programs glob-expand bare arguments, so every argument is quoted. MSYS
    also unescapes ``\\\\`` inside quotes (MSVC keeps it unless a ``"``
    follows), so its arguments escape every backslash.
    """
    if msys:
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
    out, backslashes = ['"'], 0
    for ch in arg:
        if ch == "\\":
            backslashes += 1
            continue
        if ch == '"':
            out.append("\\" * (2 * backslashes + 1) + '"')
        else:
            out.append("\\" * backslashes + ch)
        backslashes = 0
    out.append("\\" * (2 * backslashes) + '"')
    return "".join(out)


def windows_cmdline(argv: list[str]) -> str:
    if not argv:
        return ""
    msys = _is_msys_program(argv[0])
    # The program name is parsed by CreateProcess itself, never by MSYS.
    return " ".join([_quote_windows_arg(argv[0])] + [_quote_windows_arg(a, msys) for a in argv[1:]])


class ProcessTree:
    """A started process plus everything it spawns, killable as one.

    Windows: the process is put in a Job Object (children join it
    automatically) and :meth:`kill` terminates the job; if the job can't be
    created, ``taskkill /T /F`` walks the tree instead. The job does not kill
    on close, so programs a command deliberately detaches (``Start-Process``)
    outlive it — like POSIX. POSIX: :meth:`kill` kills the direct child only
    (callers that start a session kill its group themselves).
    """

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._job = None
        self._lock = threading.Lock()
        if IS_WINDOWS:
            # The process is assigned right after CreateProcess returns, so a
            # child it spawns in that first instant is not in the job. Shells
            # take far longer than that to start anything, and kill() walks
            # the PID tree with taskkill first as well, which catches it.
            job = _k32.CreateJobObjectW(None, None)
            if job:
                if _k32.AssignProcessToJobObject(job, int(proc._handle)):
                    self._job = job
                else:
                    _k32.CloseHandle(job)

    def kill(self) -> None:
        with self._lock:
            alive = self.proc.poll() is None
            if not IS_WINDOWS:
                if alive:
                    try:
                        self.proc.kill()
                    except OSError:
                        pass
                return
            if alive:
                # Popen holds the process handle, so this PID can't have been
                # reused; the tree walk needs the root alive, so it goes first.
                try:
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=15, **osinfo.hidden_subprocess_kwargs(),
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
            if self._job:
                # Also ends descendants whose parent already exited (taskkill can't see those).
                _k32.TerminateJobObject(self._job, 1)
            if self.proc.poll() is None:
                try:
                    self.proc.kill()
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            job, self._job = self._job, None
        if job:
            _k32.CloseHandle(job)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _legacy_codepage() -> str | None:
    """The OEM code page Windows console programs fall back to (cp437, cp850, \u2026)."""
    if not IS_WINDOWS:
        return None
    try:
        name = f"cp{_k32.GetOEMCP()}"
        "".encode(name)
        return name
    except (LookupError, OSError, AttributeError):
        return None


def decode_output(data: bytes | None) -> str:
    """Process output as text: UTF-8, BOM dropped, universal newlines.

    On Windows a line that isn't valid UTF-8 (cmd.exe built-ins, localized
    ``ping`` / ``ipconfig`` output) is decoded with the OEM code page instead
    of turning into replacement characters.
    """
    if not data:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        legacy = _legacy_codepage()
        if legacy is None:
            text = data.decode("utf-8", errors="replace")
        else:
            parts = []
            for line in data.splitlines(keepends=True):
                try:
                    parts.append(line.decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(line.decode(legacy, errors="replace"))
            text = "".join(parts)
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def popen_kwargs() -> dict:
    """Platform extras for a captured shell child.

    Windows: a hidden console of its own (so ``chcp`` / console-encoding changes
    and console reads never touch the TUI's console) and no inherited stdin.
    """
    if IS_WINDOWS:
        return {"creationflags": osinfo.CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL}
    return {}


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _reap(proc: subprocess.Popen) -> None:
    """Collect a killed process's leftover output so its pipes close."""
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        # Something that escaped the tree still holds the pipes.
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except (OSError, AttributeError):
                pass


def run_process(args: list[str] | str, *, timeout: float, shell: bool = False,
                env: dict | None = None, cwd: str | None = None,
                cancellable: bool = False) -> Result:
    """Run ``args`` capturing text output; on timeout kill its whole tree and
    re-raise :class:`subprocess.TimeoutExpired`.

    With ``cancellable`` the run also watches the current turn: once it is
    cancelled (Esc / web Stop / New chat) the whole tree is killed and
    :class:`_Cancelled` is raised, like :func:`_run_process` does on POSIX.
    """
    if IS_WINDOWS and isinstance(args, list) and args and args[0].lower().endswith(_BATCH_SUFFIXES):
        raise OSError(f"refusing to run a batch file with an argument list: {args[0]}")
    popen_args = windows_cmdline(args) if IS_WINDOWS and isinstance(args, list) else args
    proc = subprocess.Popen(
        popen_args, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd or str(CWD), env=env if env is not None else osinfo.shell_env(), **popen_kwargs(),
    )
    tree = ProcessTree(proc)
    try:
        if not cancellable:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                tree.kill()
                _reap(proc)
                raise
            return Result(proc.returncode, decode_output(out), decode_output(err))
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    # Repeated communicate() calls never lose output.
                    out, err = proc.communicate(timeout=_CANCEL_POLL)
                    return Result(proc.returncode, decode_output(out), decode_output(err))
                except subprocess.TimeoutExpired:
                    pass
                if state.turn_cancelled():
                    tree.kill()
                    _reap(proc)
                    raise _Cancelled()
                if time.monotonic() >= deadline:
                    tree.kill()
                    _reap(proc)
                    raise subprocess.TimeoutExpired(popen_args, timeout)
        except BaseException:
            # KeyboardInterrupt injected by Esc, or anything else: never leave
            # the command running behind us.
            if proc.poll() is None:
                tree.kill()
                _reap(proc)
            raise
    finally:
        tree.close()


def _execute(cmd: str, timeout: float, *, env: dict | None = None, cancellable: bool = False) -> Result:
    """Run ``cmd`` in the configured shell (see :func:`run_process`)."""
    args = osinfo.shell_argv(cmd)
    return run_process(args, timeout=timeout, shell=isinstance(args, str), env=env, cancellable=cancellable)


def which(name: str) -> str | None:
    """``shutil.which`` over the PATH shell commands see (on Windows that
    includes Git's MSYS tools when Git Bash is the shell)."""
    return shutil.which(name, path=osinfo.shell_env().get("PATH") or os.environ.get("PATH"))


_BATCH_SUFFIXES = (".bat", ".cmd")


def which_exe(name: str) -> str | None:
    """Like :func:`which`, but on Windows only a real ``.exe``.

    A ``.cmd`` / ``.bat`` shim runs through cmd.exe, which ignores the
    argument quoting :func:`windows_cmdline` produces — a model-written
    argument like ``x" & calc & "`` would execute. Such programs are never
    run with model-controlled arguments.
    """
    if not IS_WINDOWS:
        return which(name)
    if os.path.splitext(name)[1].lower() not in ("", ".exe"):
        return None
    found = which(name if name.lower().endswith(".exe") else name + ".exe")
    return found if found and found.lower().endswith(".exe") else None


def _format(display: str, r: Result) -> str:
    out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
    return _format_result(display, r.returncode, out)


def run_argv(argv: list[str], timeout: int = DEFAULT_BASH_TIMEOUT) -> str:
    """Run a read-only program directly (no shell, no approval); output is
    formatted like :func:`run_bash`'s."""
    display = " ".join(shlex.quote(a) for a in argv)
    exe = which_exe(argv[0])
    if exe is None:
        if IS_WINDOWS and (which(argv[0]) or "").lower().endswith(_BATCH_SUFFIXES):
            why = f"{argv[0]} is a .cmd/.bat shim; only real .exe programs are run with tool arguments"
        else:
            why = f"{argv[0]}: command not found"
        return f"$ {display}\nexit=127\n[stderr]\n{why}"
    try:
        r = run_process([exe, *argv[1:]], timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except OSError as e:
        return f"$ {display}\nexit=127\n[stderr]\n{e}"
    return _format(display, r)


def run_bash(cmd: str, timeout: int = DEFAULT_BASH_TIMEOUT) -> str:
    if is_dangerous(cmd):
        return "BLOCKED: dangerous command"

    with _bash_lock:
        denied = ask_approval(cmd)
        if denied:
            return denied
        try:
            # Files the command deletes / renames / edits in place show up in
            # the web Changes panel (nothing is recorded if nothing changed).
            settle_changes = file_changes.watch_shell(cmd)
            try:
                code, stdout, stderr = _run_process(cmd, timeout, osinfo.shell_env())
            finally:
                settle_changes()
            return _format(cmd, Result(code, stdout, stderr))
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout}s"
        except _Cancelled:
            return CANCELLED_RESULT
        except OSError as e:
            return f"$ {cmd}\nexit=127\n[stderr]\ncould not start the shell: {e}"
