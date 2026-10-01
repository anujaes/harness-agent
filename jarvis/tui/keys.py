"""Key names in UI hints, spelled the way the host OS spells them.

Hints are written with the compact Mac-style glyphs (``⌃G``, ``⌥↑``, ``⇧⇥``).
Those read naturally on macOS (and in most Linux terminals), but on Windows
people look for ``Ctrl+G`` / ``Alt+↑`` / ``Shift+Tab``, so :func:`key_label`
rewrites them there and leaves every other platform unchanged.
"""
from __future__ import annotations

from ..utils.osinfo import IS_WINDOWS

# Order matters: modifiers first, so "⇧⇥" becomes "Shift+Tab" not "⇧Tab".
_WINDOWS_NAMES = (
    ("⌃", "Ctrl+"),
    ("⌥", "Alt+"),
    ("⇧", "Shift+"),
    ("⇥", "Tab"),
    ("↵", "Enter"),
)


def windows_key_label(text: str) -> str:
    """``⌃⇧U copy`` → ``Ctrl+Shift+U copy`` (platform-independent, for tests)."""
    for glyph, name in _WINDOWS_NAMES:
        text = text.replace(glyph, name)
    return text


def key_label(text: str) -> str:
    """Rewrite key glyphs in ``text`` for this OS (Windows only; no-op elsewhere)."""
    return windows_key_label(text) if IS_WINDOWS else text


_KEY_COL = 17  # "  " indent + a 15-wide key column in two-column help text


def windows_key_table(text: str) -> str:
    """:func:`windows_key_label` for a two-column ``  keys      description`` text.

    The key column grows (``⌃C`` → ``Ctrl+C``), so rows are re-padded to the
    widest key and description continuation lines move with them.
    """
    rows: list[tuple[str, str, str]] = []  # (kind, key, rest); kind: row | cont | raw
    for line in text.splitlines():
        is_row = (len(line) > _KEY_COL and line.startswith("  ") and line[2] != " "
                  and line[_KEY_COL - 2:_KEY_COL] == "  ")
        if is_row:
            rows.append(("row", windows_key_label(line[2:_KEY_COL].rstrip()),
                         windows_key_label(line[_KEY_COL:])))
        elif line.startswith(" " * _KEY_COL) and line.strip():
            rows.append(("cont", "", windows_key_label(line[_KEY_COL:])))
        else:
            rows.append(("raw", "", windows_key_label(line)))
    width = max((len(key) for kind, key, _ in rows if kind == "row"), default=_KEY_COL - 4) + 2
    return "\n".join(rest if kind == "raw" else "  " + key.ljust(width) + rest
                     for kind, key, rest in rows)


def key_table(text: str) -> str:
    """:func:`windows_key_table` on Windows; ``text`` unchanged elsewhere."""
    return windows_key_table(text) if IS_WINDOWS else text
