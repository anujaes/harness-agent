"""Opening a file in the user's editor (``$EDITOR`` / ``$VISUAL``).

Editor variables often carry arguments (``code --wait``, ``subl -w``), so the
value is split into an argv rather than used as a single program name. On
Windows the split keeps backslashes (``C:\\Tools\\vim.exe``) and the program is
resolved through ``PATHEXT`` so ``code`` finds ``code.cmd``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .cmdline import split_command
from .osinfo import IS_WINDOWS


def default_editor() -> str:
    """Editor used when neither ``$EDITOR`` nor ``$VISUAL`` is set."""
    return "notepad" if IS_WINDOWS else "nano"


def editor_argv(*, fallback: bool = False) -> list[str] | None:
    """The configured editor as an argv prefix, or None when none is set.

    With ``fallback`` an unset editor means :func:`default_editor` instead.
    """
    raw = (os.environ.get("EDITOR") or os.environ.get("VISUAL") or "").strip()
    if not raw:
        if not fallback:
            return None
        raw = default_editor()
    if os.path.isfile(raw) or shutil.which(raw):
        # The whole value is one program, e.g. an unquoted
        # "/Applications/My Editor.app/Contents/MacOS/editor" — don't split it.
        argv = [raw]
    else:
        try:
            argv = split_command(raw)
        except ValueError:  # unbalanced quotes: treat the whole value as the program
            argv = [raw]
    if not argv:
        return None
    if IS_WINDOWS:
        argv[0] = shutil.which(argv[0]) or argv[0]
    return argv


def editor_label(*, fallback: bool = False) -> str:
    """What to call the editor in messages (``$EDITOR (code --wait)``)."""
    raw = (os.environ.get("EDITOR") or os.environ.get("VISUAL") or "").strip()
    return raw or (default_editor() if fallback else "")


def open_in_editor(path: str | Path, *, wait: bool = False, fallback: bool = False) -> bool:
    """Open ``path`` in the editor. False when no editor is configured.

    ``wait`` blocks until the editor exits (for terminal editors such as nano).
    Launch errors (``FileNotFoundError`` / ``OSError``) propagate to the caller.
    """
    argv = editor_argv(fallback=fallback)
    if not argv:
        return False
    cmd = [*argv, str(path)]
    if wait:
        subprocess.run(cmd, check=False)
    else:
        subprocess.Popen(cmd)
    return True
