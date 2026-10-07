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

# macOS/Linux install from upstream main; the Windows edition lives on the
# windows-support branch of the fork (it is not merged into main).
_INSTALLER_BASE = "https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts"
_WINDOWS_INSTALLER_BASE = "https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts"


def install_command() -> str:
    """One-line (re)install / update command for this OS."""
    if IS_WINDOWS:
        return f'powershell -ExecutionPolicy ByPass -c "irm {_WINDOWS_INSTALLER_BASE}/install.ps1 | iex"'
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


# Files whose change means the venv must be reinstalled (deps, entry points).
_PACKAGING_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
# The comment line scripts/install.ps1 writes into the jarvis.cmd it owns.
_SHIM_MARKER = "rem Jarvis launcher (scripts/install.ps1)"


def packaging_changed(repo_root: pathlib.Path, old_head: str) -> bool:
    """Whether the update from ``old_head`` to HEAD touched packaging files.

    An editable install already runs the pulled code, so ``pip install -e .``
    only matters when dependencies or entry points changed. Unknown history
    (no ``old_head``, git error) counts as changed — reinstalling is the safe side.
    """
    if not old_head:
        return True
    rc, out, _err = _git_run(repo_root, ["diff", "--name-only", old_head, "HEAD", "--", *_PACKAGING_FILES],
                             timeout=20)
    return rc != 0 or bool(out.strip())


def _pip_pending_file() -> pathlib.Path:
    """Marker naming a checkout whose ``pip install`` must be retried at launch."""
    from .constants import CONFIG_DIR

    return CONFIG_DIR / "pip_pending"


def _mark_pip_pending(repo_root: pathlib.Path) -> None:
    try:
        path = _pip_pending_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(repo_root), encoding="utf-8")
    except OSError:
        pass


def _clear_pip_pending() -> None:
    try:
        _pip_pending_file().unlink()
    except OSError:
        pass


def ensure_python_launcher(bin_dir: pathlib.Path | None = None) -> bool:
    """Windows: point the installer's ``jarvis.cmd`` at ``python.exe -m jarvis``.

    Older installs ran ``Scripts\\jarvis.exe``. Its Python reads that exe as
    its script and keeps it open, so pip can neither overwrite nor rename it
    while any Jarvis runs and every update's ``pip install`` failed. Only a
    shim carrying the installer's marker is rewritten. True when it changed.
    """
    if not IS_WINDOWS:
        return False
    if bin_dir is None:
        bin_dir = pathlib.Path(os.environ.get("JARVIS_BIN_DIR") or pathlib.Path.home() / ".local" / "bin")
    shim = pathlib.Path(bin_dir) / "jarvis.cmd"
    try:
        text = shim.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    if _SHIM_MARKER not in text:
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().endswith("\\scripts\\jarvis.exe\" %*"):
            target = stripped[: -len(" %*")].strip('"')
            python = target[: -len("jarvis.exe")] + "python.exe"
            lines[i] = f'"{python}" -m jarvis %*'
            try:
                shim.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
            except (OSError, UnicodeEncodeError):
                return False
            return True
    return False


def clean_broken_dists(site_packages: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Remove ``~?rness_jarvis-*`` folders a failed pip run left in site-packages.

    pip renames a package's dist-info to ``~`` + its name minus the first
    letter while replacing it; a failure in the middle leaves it behind, and
    ``importlib.metadata`` then reports old versions next to the real one.
    Only this package's leftovers are touched.
    """
    import re
    import sysconfig

    root = pathlib.Path(site_packages or sysconfig.get_paths()["purelib"])
    pattern = re.compile(r"^~.rness_jarvis-.*\.dist-info$", re.IGNORECASE)
    removed: list[pathlib.Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if entry.is_dir() and pattern.match(entry.name):
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry)
    return removed


def finish_update(repo_root: pathlib.Path, old_head: str) -> str:
    """After a successful git sync: ``"skipped"``, ``"installed"`` or ``"deferred"``.

    The pulled code is already live for an editable install, so pip runs only
    when packaging changed (or an earlier install is still pending). When it
    fails — on Windows usually because a running Jarvis holds a file — the
    install is retried at the next launch instead of blocking the update.
    """
    ensure_python_launcher()
    clean_broken_dists()
    pending = _pip_pending_file().exists()
    if not pending and not packaging_changed(repo_root, old_head):
        return "skipped"
    if pip_install_repo(repo_root, timeout=240):
        _clear_pip_pending()
        return "installed"
    _mark_pip_pending(repo_root)
    return "deferred"


def run_pending_install() -> bool | None:
    """At launch, finish a ``pip install`` an update had to defer.

    None when nothing is pending, else whether it worked (a failure keeps the
    marker for the next launch).
    """
    path = _pip_pending_file()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    root = pathlib.Path(raw) if raw else None
    if root is None or not (root / "pyproject.toml").is_file():
        _clear_pip_pending()
        return None
    clean_broken_dists()
    if pip_install_repo(root, timeout=240):
        _clear_pip_pending()
        return True
    return False


def harness_agent_models_available() -> bool:
    """True when this process can list free Harness Agent models."""
    try:
        from .constants.providers import harness_agent_models_for_picker
        return bool(harness_agent_models_for_picker())
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


def _restart_argv() -> list[str]:
    """Command line for the restarted Jarvis.

    Windows always uses ``python -m jarvis``: never the ``jarvis.exe`` the
    next update has to replace, and a ``-m`` launch's ``argv[0]`` (the
    package's ``__main__.py``) can't be run as a plain script.
    """
    if IS_WINDOWS:
        return [sys.executable, "-m", "jarvis", *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def _restart_in_child() -> None:
    try:
        rc = subprocess.run(_restart_argv(), check=False).returncode
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
