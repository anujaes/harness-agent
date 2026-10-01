"""Clipboard get/set on Windows (native Unicode clipboard, no code-page mangling)."""
from __future__ import annotations

from ...constants import MAX_TOOL_OUTPUT
from ...utils import win_clipboard


def clipboard_get() -> str:
    text = win_clipboard.get_text()
    if text is None:
        return "ERROR: clipboard is busy or holds no text"
    return text[:MAX_TOOL_OUTPUT]


def clipboard_set(text: str) -> str:
    if not win_clipboard.set_text(text or ""):
        return "ERROR: could not open the clipboard (another app is holding it) — try again"
    return f"clipboard set ({len(text or '')} chars)"
