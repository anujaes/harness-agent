"""Code search via ripgrep, grep, or a built-in fallback.

Every backend prints ``path:line:text`` rows (paths joined onto the searched
path with ``/``, like ripgrep on macOS / Linux), wrapped in the same
``$ cmd`` / ``exit=N`` header ``run_bash`` uses. Programs run as argv lists,
never through a shell, so a pattern needs no quoting on any platform.
"""
import os
import re
import shlex
import subprocess
import time

from ..constants import MAX_TOOL_OUTPUT, SEARCH_MATCH_CAP
from ..path_resolve import project_scope_error, robust_resolve
from ..utils import osinfo
from .shell import run_argv, which_exe

SEARCH_TIMEOUT = 20

_SKIP_GLOBS = [
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]
_SKIP_SET = set(_SKIP_GLOBS)
_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_LINE_CHARS = 1000


def search_code(pattern: str, path: str = ".", allow_outside_project: bool = False) -> str:
    root = robust_resolve(path)
    if not allow_outside_project:
        scope_err = project_scope_error(root, "search_code", "allow_outside_project=true")
        if scope_err:
            return scope_err
    try:
        cmd_path = str(root.relative_to(robust_resolve("."))) or "."
    except ValueError:
        cmd_path = str(root)
    if os.name == "nt":
        cmd_path = cmd_path.replace("\\", "/")
    # Real executables only: a .cmd/.bat shim would hand the pattern to cmd.exe.
    if which_exe("rg"):
        # ripgrep respects .gitignore by default; add explicit globs for safety.
        argv = ["rg", "-n", "--max-count", str(SEARCH_MATCH_CAP)]
        for d in _SKIP_GLOBS:
            argv += ["-g", f"!{d}"]
        if os.name == "nt":
            argv.append("--path-separator=/")  # same rows as on macOS / Linux
        return run_argv(argv + ["-e", pattern, "--", cmd_path], SEARCH_TIMEOUT)
    if which_exe("grep"):
        argv = ["grep", "-rn", f"--max-count={SEARCH_MATCH_CAP}"]
        argv += [f"--exclude-dir={d}" for d in _SKIP_GLOBS]
        return run_argv(argv + ["-e", pattern, "--", cmd_path], SEARCH_TIMEOUT)
    return _python_search(pattern, root, cmd_path)


# ── built-in fallback (no rg / grep on PATH — common on Windows) ────────────


def _compile(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile(re.escape(pattern))  # not a valid regex: search it literally


def _skipped(rel_parts: tuple[str, ...]) -> bool:
    # Like ripgrep: no hidden files / folders, no vendored or build output.
    return any(p in _SKIP_SET or (p.startswith(".") and p not in (".", "..")) for p in rel_parts)


def _git_listing(root) -> list[str] | None:
    """Files under ``root`` git would show (tracked + untracked, not ignored)
    — honours .gitignore like ripgrep. None outside a work tree."""
    git = which_exe("git")
    if not git:
        return None
    try:
        r = subprocess.run(
            [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root), capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
            **osinfo.hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return sorted({p for p in r.stdout.decode("utf-8", "replace").split("\0") if p})


def _walk(root) -> list[str]:
    out: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not _skipped((d,)))
        rel_base = os.path.relpath(base, root)
        for n in sorted(names):
            if not _skipped((n,)):
                out.append(n if rel_base == "." else f"{rel_base}/{n}".replace("\\", "/"))
    return out


def _file_rows(path: str, rx: re.Pattern, label: str) -> list[str]:
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            return []
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    if b"\0" in data[:8192]:
        return []  # binary
    rows: list[str] = []
    for no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        if rx.search(line):
            if len(line) > _MAX_LINE_CHARS:
                line = line[:_MAX_LINE_CHARS] + "…"
            rows.append(f"{label}{no}:{line}")
            if len(rows) >= SEARCH_MATCH_CAP:
                break
    return rows


def _python_search(pattern: str, root, cmd_path: str) -> str:
    display = f"(built-in search: no rg or grep on PATH) {shlex.quote(pattern)} {shlex.quote(cmd_path)}"
    if not root.exists():
        return f"$ {display}\nexit=2\n[stderr]\n{cmd_path}: No such file or directory"
    rx = _compile(pattern)
    deadline = time.monotonic() + SEARCH_TIMEOUT
    if root.is_file():
        rows = _file_rows(str(root), rx, "")
        return f"$ {display}\nexit={0 if rows else 1}\n" + "".join(r + "\n" for r in rows)

    files = _git_listing(root)
    files = _walk(root) if files is None else [f for f in files if not _skipped(tuple(f.split("/")))]
    prefix = cmd_path.rstrip("/") + "/" if cmd_path not in ("", ".") else "./"
    rows: list[str] = []
    size = 0
    note = ""
    for rel in files:
        if time.monotonic() > deadline:
            note = f"\n[stderr]\nsearch stopped after {SEARCH_TIMEOUT}s — narrow the path"
            break
        found = _file_rows(os.path.join(root, rel), rx, f"{prefix}{rel}:")
        rows += found
        size += sum(len(r) + 1 for r in found)
        if size > MAX_TOOL_OUTPUT:
            note = "\n[stderr]\nmore matches not shown — narrow the pattern or path"
            break
    body = "".join(r + "\n" for r in rows)
    if len(body) > MAX_TOOL_OUTPUT:
        body = body[:MAX_TOOL_OUTPUT].rsplit("\n", 1)[0] + "\n"
    return f"$ {display}\nexit={0 if rows else 1}\n{body}{note}"
