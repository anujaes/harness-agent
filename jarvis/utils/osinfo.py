"""Host-platform facts shared across the package.

One place to ask "which OS am I on?" and "which shell runs ``run_bash``?", so
tools, prompts and install hints stay consistent instead of each module
re-deriving ``sys.platform`` checks of its own.
"""
from __future__ import annotations

import functools
import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    # Windows looks for programs in the current directory before PATH — in
    # shutil.which, CreateProcess and cmd.exe alike — so a repo that ships its
    # own rg.exe / git.exe would have it run by search_code / git tools and
    # auto-approved shell commands. This documented switch turns that off for
    # Jarvis and every child process it starts.
    os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")


def os_label() -> str:
    """Human-facing OS name for prompts and messages."""
    if IS_MACOS:
        return "macOS"
    if IS_WINDOWS:
        return "Windows"
    if IS_LINUX:
        return "Linux"
    return sys.platform


# Windows "known folders" users and models refer to by their ~\Name form.
_KNOWN_FOLDER_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


def _known_folder_path(folder_id: str) -> str | None:
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(folder_id)
    guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return None
        return out.value
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)


@functools.lru_cache(maxsize=1)
def known_folders() -> dict[str, Path]:
    """Where Desktop, Documents, Downloads … really live (Windows only; ``{}`` elsewhere).

    OneDrive folder backup and user relocation move them away from
    ``%USERPROFILE%\\Desktop`` etc., e.g. to ``%USERPROFILE%\\OneDrive\\Desktop``.
    """
    if not IS_WINDOWS:
        return {}
    found: dict[str, Path] = {}
    for name, folder_id in _KNOWN_FOLDER_IDS.items():
        try:
            path = _known_folder_path(folder_id)
        except OSError:
            path = None
        if path:
            found[name] = Path(path)
    return found


def redirect_known_folder(path: Path) -> Path:
    """Map ``~\\Desktop\\x`` to the real Desktop when Windows keeps it elsewhere.

    Only applies when the literal path doesn't exist and its first component
    under the home folder names a relocated known folder; otherwise ``path``
    is returned unchanged (always, off Windows).
    """
    folders = known_folders()
    if not folders or path.exists():
        return path
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return path
    if not rel.parts:
        return path
    head = rel.parts[0].casefold()
    for name, real in folders.items():
        if name.casefold() == head and real != Path.home() / name:
            return real.joinpath(*rel.parts[1:])
    return path


def _git_bash_candidates() -> list[Path]:
    """Git for Windows' bash.exe locations, most specific first.

    ``<Git>\\usr\\bin\\bash.exe`` (the real MSYS bash) comes before the
    ``<Git>\\bin\\bash.exe`` launcher: it starts one process fewer, and some
    Application Control policies block the launcher but not the real shell.
    :func:`shell_env` supplies the PATH / MSYSTEM the launcher would have set.

    ``C:\\Windows\\System32\\bash.exe`` is the WSL launcher, which runs in a
    different filesystem namespace — it is deliberately never a candidate.
    """
    roots: list[Path] = []
    git = shutil.which("git")
    if git:
        # <Git>\cmd\git.exe or <Git>\bin\git.exe or <Git>\mingw64\bin\git.exe
        for parent in Path(git).resolve().parents:
            if (parent / "usr" / "bin" / "bash.exe").is_file() or (parent / "bin" / "bash.exe").is_file():
                roots.append(parent)
                break
    for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(key)
        if base:
            roots += [Path(base) / "Git", Path(base) / "Programs" / "Git"]
    out: list[Path] = []
    for root in roots:
        out += [root / "usr" / "bin" / "bash.exe", root / "bin" / "bash.exe"]
    return out


def _runs(exe: Path) -> bool:
    """True when ``exe`` actually starts (not blocked by policy, not broken)."""
    import subprocess

    try:
        r = subprocess.run(
            [str(exe), "-c", "exit 0"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3, **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


@functools.lru_cache(maxsize=1)
def find_git_bash() -> str | None:
    """Path to a runnable Git Bash on Windows, or None when there is none."""
    if not IS_WINDOWS:
        return None
    seen: set[str] = set()
    for cand in _git_bash_candidates():
        key = str(cand).lower()
        if key in seen or "system32" in key:
            continue
        seen.add(key)
        if cand.is_file() and _runs(cand):
            return str(cand)
    return None


def _git_root_of(bash: str) -> Path | None:
    p = Path(bash)
    if p.parent.name.lower() == "bin" and p.parent.parent.name.lower() == "usr":
        return p.parents[2]
    if p.parent.name.lower() == "bin":
        return p.parents[1]
    return None


@functools.lru_cache(maxsize=1)
def find_powershell() -> str:
    """PowerShell 7 (``pwsh``) when installed, else Windows PowerShell 5.1."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


@functools.lru_cache(maxsize=1)
def shell_kind() -> str:
    """Which shell ``run_bash`` uses: ``bash``, ``powershell`` or ``cmd``.

    POSIX hosts always use ``/bin/sh``-compatible bash semantics. On Windows,
    ``HARNESS_SHELL`` (``bash`` | ``powershell`` | ``cmd``) wins; otherwise Git
    Bash is preferred because models write POSIX shell far more reliably, with
    PowerShell as the fallback.
    """
    if not IS_WINDOWS:
        return "bash"
    pref = (os.environ.get("HARNESS_SHELL") or "").strip().lower()
    if pref in ("pwsh", "powershell"):
        return "powershell"
    if pref == "cmd":
        return "cmd"
    if pref == "bash" and find_git_bash():
        return "bash"
    return "bash" if find_git_bash() else "powershell"


def shell_label() -> str:
    """Name of the ``run_bash`` shell for the system prompt."""
    kind = shell_kind()
    if not IS_WINDOWS:
        return "bash"
    if kind == "bash":
        return "Git Bash (bash on Windows; POSIX paths like /c/Users/... also work)"
    if kind == "powershell":
        return "PowerShell"
    return "cmd.exe"


# Prepended to PowerShell commands so their output (and what they pipe to
# native programs) is UTF-8 without a BOM, whatever the console code page is.
_PS_UTF8_PREFIX = (
    "$ProgressPreference = 'SilentlyContinue'; "
    "$__jv_utf8 = New-Object System.Text.UTF8Encoding $false; "
    "[Console]::OutputEncoding = $__jv_utf8; $OutputEncoding = $__jv_utf8\n"
)  # own line: error excerpts ("At line:2 char:1") then show only the command
# Appended on a line of its own: `-Command` exits 1 for any failure, but the
# model needs a failing native program's own exit code, as bash reports it.
_PS_EXIT_SUFFIX = "\nif (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }"


def shell_argv(cmd: str) -> list[str] | str:
    """The argv (or shell string) that runs ``cmd`` in :func:`shell_kind`.

    A plain string means "pass with ``shell=True``" (POSIX and cmd.exe).
    Run it with :func:`shell_env` as the environment.

    Git Bash runs non-login (``-c``), like ``/bin/sh -c`` on POSIX — a login
    shell costs ~1s per command for ``/etc/profile``; :func:`shell_env` puts
    the MSYS tools on PATH instead.
    """
    if not IS_WINDOWS:
        return cmd
    kind = shell_kind()
    if kind == "bash":
        return [find_git_bash() or "bash", "-c", cmd]
    if kind == "powershell":
        return [find_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", _PS_UTF8_PREFIX + cmd + _PS_EXIT_SUFFIX]
    # cmd.exe fixes the code page of its built-ins when it parses the line, so
    # a leading `chcp 65001` can't help; callers decode non-UTF-8 output lines
    # with the OEM code page instead.
    return cmd


def shell_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a ``run_bash`` / ``run_bg`` shell.

    Pagers are disabled everywhere. On Windows, Python children print UTF-8
    (what the output is decoded as) and Git Bash gets what its launcher would
    set up: ``MSYSTEM``, ``CHERE_INVOKING`` (stay in the cwd) and the MSYS /
    MinGW tool directories first on PATH. ``MSYS_NO_PATHCONV`` is deliberately
    left alone: MSYS path conversion is what makes ``/c/Users/...`` arguments
    work for native programs, and disabling it would break them.
    """
    env = dict(os.environ if base is None else base)
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("PAGER", "cat")
    if not IS_WINDOWS:
        return env
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if shell_kind() == "bash":
        bash = find_git_bash()
        root = _git_root_of(bash) if bash else None
        env.setdefault("MSYSTEM", "MINGW64")
        env.setdefault("CHERE_INVOKING", "1")
        if root is not None:
            path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
            current = env.get(path_key, "")
            have = {p.rstrip("\\/").lower() for p in current.split(os.pathsep) if p}
            extra = [str(root / d) for d in ("mingw64/bin", "usr/bin")
                     if str(root / d).rstrip("\\/").lower() not in have and (root / d).is_dir()]
            if extra:
                env[path_key] = os.pathsep.join(extra + ([current] if current else []))
    return env


def package_manager_hint(package: str, *, brew: str = "", winget: str = "", apt: str = "") -> str:
    """Install instruction for ``package`` on the current OS."""
    if IS_WINDOWS:
        return f"winget install -e --id {winget}" if winget else f"install {package}"
    if IS_MACOS:
        return f"brew install {brew or package}"
    return f"sudo apt install {apt or package}"


# Subprocess flags that keep console windows from flashing up when a GUI-less
# helper (PowerShell, taskkill, …) is spawned from the TUI.
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WINDOWS else 0


def hidden_subprocess_kwargs() -> dict:
    """``subprocess`` kwargs that suppress helper console windows on Windows."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}
