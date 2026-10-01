"""Splitting user-typed command lines the way the host OS would read them.

``shlex.split`` in POSIX mode treats ``\\`` as an escape, which turns a pasted
Windows path such as ``C:\\Users\\me\\notes`` into ``C:Usersmenotes``. On
Windows the non-POSIX lexer is used instead (backslashes kept), and the quotes
it leaves on a token are removed so ``"C:\\Program Files\\x"`` reads as one
unquoted word — the same result POSIX mode gives for ``'/opt/my app/x'``.
"""
from __future__ import annotations

import re
import shlex

from .osinfo import IS_WINDOWS

_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}\\)")


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def split_command(text: str) -> list[str]:
    """``shlex.split`` that keeps Windows paths intact. Raises ``ValueError`` like shlex."""
    if not IS_WINDOWS:
        return shlex.split(text)
    return [_unquote(tok) for tok in shlex.split(text, posix=False)]


def looks_like_path(token: str) -> bool:
    """True for ``/x``, ``./x``, ``~/x`` and, on any OS, ``C:\\x`` / ``.\\x`` / ``\\\\host\\x``."""
    return token.startswith(("./", "../", "/", "~")) or bool(_WINDOWS_ABS_RE.match(token))
