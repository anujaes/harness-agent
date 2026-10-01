"""Keep the running jarvis install in sync with its git checkout."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from .utils.osinfo import IS_WINDOWS


def _managed_install_dir() -> pathlib.Path:
    """Where the official installer puts its checkout (scripts/install, install.ps1)."""
    if IS_WINDOWS:
        custom = os.environ.get("JARVIS_INSTALL_DIR", "").strip()
        if custom:
            return pathlib.Path(custom).expanduser()
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
        return pathlib.Path(base) / "harness-agent"
    return pathlib.Path("~/.local/share/harness-agent").expanduser()


MANAGED_INSTALL_DIR = _managed_install_dir()

_INSTALLER_BASE = "https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts"


def install_command() -> str:
    """One-line (re)install / update command for this OS."""
    if IS_WINDOWS:
        return f'powershell -ExecutionPolicy ByPass -c "irm {_INSTALLER_BASE}/install.ps1 | iex"'
    return f"curl -fsSL {_INSTALLER_BASE}/install | bash"


def manual_pip_command(repo_root: pathlib.Path) -> str:
    """Shell line that reinstalls the checkout into the running venv."""
    if IS_WINDOWS:  # PowerShell 5.1 has no `&&`
        return f'cd "{repo_root}"; & "{sys.executable}" -m pip install -e .'
    return f"cd {repo_root} && {sys.executable} -m pip install -e ."


def find_install_root() -> pathlib.Path | None:
    """Locate the harness-agent git checkout backing this install."""
    candidates: list[pathlib.Path] = []

    # Editable install: jarvis/*.py live directly under the repo.
    here = pathlib.Path(__file__).resolve().parent
    for parent in [here, *here.parents[:14]]:
        if (parent / ".git").is_dir() and (parent / "pyproject.toml").is_file():
            candidates.append(parent)

    # Standard install: `jarvis` symlink → ~/.local/share/harness-agent/.venv/bin/jarvis
    # (Windows: a .cmd shim, which never resolves to the repo — but the install
    # is editable, so the __file__ walk above already found it.)
    try:
        jarvis_bin = shutil.which("jarvis")
        if jarvis_bin:
            resolved = pathlib.Path(jarvis_bin).resolve()
            for repo in (resolved.parent.parent, resolved.parent.parent.parent):
                if (repo / ".git").is_dir() and (repo / "pyproject.toml").is_file():
                    candidates.insert(0, repo)
    except Exception:
        pass

    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "jarvis" / "cli.py").is_file():
            return candidate
    return None


def is_managed_install(repo_root: pathlib.Path) -> bool:
    """True for the standard installer checkout (safe to hard-reset on upgrade)."""
    try:
        return repo_root.resolve() == MANAGED_INSTALL_DIR.resolve()
    except Exception:
        return False


def _git_run(
    repo_root: pathlib.Path,
    args: list[str],
    *,
    timeout: int = 120,
) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", f"Command not found: {e}"
    except Exception as e:
        return -1, "", str(e)


def git_dirty_files(repo_root: pathlib.Path) -> list[str]:
    rc, out, _ = _git_run(repo_root, ["status", "--porcelain"], timeout=30)
    if rc != 0 or not out:
        return []
    return [line[3:] for line in out.splitlines() if len(line) > 3]


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    error: str = ""
    branch: str = "main"
    discarded_local: tuple[str, ...] = ()
    method: str = ""  # "pull" | "reset"


def sync_repo_to_remote(
    repo_root: pathlib.Path,
    *,
    branch: str | None = None,
    fetch_timeout: int = 120,
    sync_timeout: int = 120,
) -> SyncResult:
    """Fetch and fast-forward *repo_root* to ``origin/<branch>``.

    Managed installs (``~/.local/share/harness-agent``) always hard-reset to
    match the remote — local edits are never preserved. Dev clones abort when
    the working tree is dirty.
    """
    if not (repo_root / ".git").is_dir():
        return SyncResult(ok=False, error="not a git repository")

    rc, _, err = _git_run(repo_root, ["fetch", "origin"], timeout=fetch_timeout)
    if rc != 0:
        return SyncResult(ok=False, error=err or "git fetch failed")

    if branch is None:
        rc, out, _ = _git_run(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        branch = out.strip() if rc == 0 and out else "main"

    remote_ref = f"origin/{branch}"
    dirty = git_dirty_files(repo_root)

    if is_managed_install(repo_root):
        rc, _, err = _git_run(
            repo_root,
            ["reset", "--hard", remote_ref],
            timeout=sync_timeout,
        )
        if rc != 0:
            return SyncResult(ok=False, branch=branch, error=err or "git reset failed")
        return SyncResult(
            ok=True,
            branch=branch,
            discarded_local=tuple(dirty),
            method="reset",
        )

    if dirty:
        preview = ", ".join(dirty[:4])
        if len(dirty) > 4:
            preview += f", … (+{len(dirty) - 4} more)"
        return SyncResult(
            ok=False,
            branch=branch,
            error=f"uncommitted local changes: {preview}",
        )

    rc, _, err = _git_run(
        repo_root,
        ["pull", "--ff-only", "origin", branch],
        timeout=sync_timeout,
    )
    if rc != 0:
        return SyncResult(ok=False, branch=branch, error=err or "git pull failed")
    return SyncResult(ok=True, branch=branch, method="pull")


def running_python() -> str:
    """Python interpreter actually executing jarvis right now."""
    return sys.executable


_ASIDE_SUFFIX = ".old"


def _move_aside_locked_launchers() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Windows: rename the venv's ``jarvis*.exe`` launchers out of pip's way.

    A running ``jarvis.exe`` can't be overwritten, so ``pip install -e .`` would
    fail while Jarvis itself is open — but Windows does allow renaming it. The
    old copies are deleted on a later run, once nothing has them open.
    """
    scripts = pathlib.Path(running_python()).parent
    for stale in scripts.glob(f"jarvis*.exe.*{_ASIDE_SUFFIX}"):
        try:
            stale.unlink()
        except OSError:
            pass  # still running
    moved: list[tuple[pathlib.Path, pathlib.Path]] = []
    for exe in scripts.glob("jarvis*.exe"):
        aside = exe.with_name(f"{exe.name}.{os.getpid()}{_ASIDE_SUFFIX}")
        try:
            exe.rename(aside)
        except OSError:
            continue
        moved.append((exe, aside))
    return moved


def _restore_launchers(moved: list[tuple[pathlib.Path, pathlib.Path]]) -> None:
    """Put launchers back when pip failed before writing new ones."""
    for exe, aside in moved:
        if not exe.exists():
            try:
                aside.rename(exe)
            except OSError:
                pass


def pip_install_repo(repo_root: pathlib.Path, *, timeout: int = 180) -> bool:
    """Editable install into the *running* venv so git pull = live code after restart."""
    moved = _move_aside_locked_launchers() if IS_WINDOWS else []
    ok = False
    try:
        r = subprocess.run(
            [running_python(), "-m", "pip", "install", "-e", "."],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        ok = r.returncode == 0
    except Exception:
        ok = False
    finally:
        if moved:
            _restore_launchers(moved)
    return ok


def harness_agent_models_available() -> bool:
    """True when this process can list free Harness Agent models."""
    try:
        from .constants.providers import harness_agent_models_for_picker
        return len(harness_agent_models_for_picker()) >= 3
    except Exception:
        return False


# Windows restart plumbing. ``os.execv`` there spawns a new process and exits
# the old one, so the shell takes the console back while the new Jarvis runs.
# Instead the new process runs as a child and this one waits for it; when the
# request comes from a worker thread while a UI owns the console, the UI is
# asked to close first and its main thread finishes the restart.
_restart_handler: Callable[[], None] | None = None
_restart_pending = False


def set_restart_handler(handler: Callable[[], None] | None) -> None:
    """Register how to close the running UI (thread-safe) before a restart."""
    global _restart_handler
    _restart_handler = handler


def _restart_in_child() -> None:
    try:
        rc = subprocess.run([sys.executable, *sys.argv], check=False).returncode
    except KeyboardInterrupt:
        rc = 130
    raise SystemExit(rc)


def finish_pending_restart() -> None:
    """Main thread, after the UI closed: run the restart it was asked for."""
    global _restart_pending
    if _restart_pending:
        _restart_pending = False
        _restart_in_child()


def reexec_jarvis(*, update_banner: dict | None = None) -> None:
    """Replace this process so Python reloads modules from disk."""
    global _restart_pending
    if update_banner:
        import json
        os.environ["HARNESS_UPDATE_RESULT"] = json.dumps(update_banner)
    os.environ["HARNESS_UPDATED_REEXEC"] = "1"
    if not IS_WINDOWS:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    if threading.current_thread() is threading.main_thread():
        _restart_in_child()
    if _restart_handler is None:
        # A UI we can't close owns the console (legacy REPL): the update is on
        # disk and loads next launch — never run two UIs in one console.
        os.environ.pop("HARNESS_UPDATED_REEXEC", None)
        os.environ.pop("HARNESS_UPDATE_RESULT", None)
        return
    _restart_pending = True
    _restart_handler()
