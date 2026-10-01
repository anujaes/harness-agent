"""Tests for jarvis.utils.clipboard — clean-copy helpers used by ⌃Y and /copy —
plus the Windows clipboard backends (text and /paste images)."""
import sys

import pytest

from jarvis.utils.clipboard import (
    conversation_plain_text,
    extract_last_code_block,
    normalize_copy_text,
)


# ─── normalize_copy_text ──────────────────────────────────────────────────

def test_normalize_strips_trailing_spaces_per_line():
    text = "hello   \nworld  \t \nend"
    assert normalize_copy_text(text) == "hello\nworld\nend"


def test_normalize_collapses_blank_line_runs():
    text = "a\n\n\n\n\nb"
    assert normalize_copy_text(text) == "a\n\nb"


def test_normalize_unifies_crlf():
    assert normalize_copy_text("a\r\nb\rc") == "a\nb\nc"


def test_normalize_strips_outer_newlines():
    assert normalize_copy_text("\n\ntext\n\n") == "text"


def test_normalize_empty():
    assert normalize_copy_text("") == ""
    assert normalize_copy_text(None or "") == ""


# ─── extract_last_code_block ──────────────────────────────────────────────

def test_extract_no_block_returns_none():
    assert extract_last_code_block("no code here") is None
    assert extract_last_code_block("") is None
    assert extract_last_code_block(None or "") is None


def test_extract_single_block_strips_fences():
    text = "before\n```python\nprint('hi')\n```\nafter"
    assert extract_last_code_block(text) == "print('hi')"


def test_extract_takes_last_of_multiple_blocks():
    text = "```\nfirst\n```\nmiddle\n```js\nsecond()\n```"
    assert extract_last_code_block(text) == "second()"


def test_extract_multiline_block():
    text = "```sh\nline1\nline2\n```"
    assert extract_last_code_block(text) == "line1\nline2"


def test_extract_block_without_language():
    text = "```\nplain\n```"
    assert extract_last_code_block(text) == "plain"


# ─── conversation_plain_text ──────────────────────────────────────────────

def test_conversation_basic_roles():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello!"},
    ]
    out = conversation_plain_text(messages)
    assert "## You\n\nhi" in out
    assert "## Assistant\n\nhello!" in out


def test_conversation_skips_tool_noise():
    messages = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "tool_use", "name": "read_file", "input": {"path": "x"}},
                {"type": "text", "text": "the answer"},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "1", "content": "raw"}],
        },
    ]
    out = conversation_plain_text(messages)
    assert "hidden reasoning" not in out
    assert "read_file" not in out
    assert "raw" not in out
    assert "the answer" in out
    # tool_result-only user message contributes no section
    assert out.count("## You") == 1


def test_conversation_empty():
    assert conversation_plain_text([]) == ""
    assert conversation_plain_text(None or []) == ""


# ─── /copy command arg routing ────────────────────────────────────────────

@pytest.fixture()
def _copies(monkeypatch):
    """Capture what /copy puts on the clipboard; silence console output."""
    import jarvis.commands.context as ctx
    from jarvis import state

    copied: list[str] = []
    monkeypatch.setattr(ctx, "_copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(ctx.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(state, "last_assistant_text", "reply\n```py\ncode()\n```", raising=False)
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "q"}], raising=False)
    return copied


def test_copy_default_copies_last_reply(_copies):
    from jarvis.commands.context import handle_context

    handled, new_inp = handle_context("/copy", "")
    assert handled and new_inp is None
    assert _copies and "reply" in _copies[0]


def test_copy_code_copies_last_code_block(_copies):
    from jarvis.commands.context import handle_context

    handle_context("/copy", "code")
    assert _copies == ["code()"]


def test_copy_all_copies_conversation(_copies):
    from jarvis.commands.context import handle_context

    handle_context("/copy", "all")
    assert _copies and "## You" in _copies[0]


def test_copy_nothing_to_copy(monkeypatch):
    import jarvis.commands.context as ctx
    from jarvis import state

    copied: list[str] = []
    monkeypatch.setattr(ctx, "_copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(ctx.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(state, "last_assistant_text", "", raising=False)
    handled, _ = ctx.handle_context("/copy", "")
    assert handled
    assert copied == []


# ─── Windows clipboard (Win32 CF_UNICODETEXT) ─────────────────────────────

_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Win32 clipboard")
_UNICODE_SAMPLE = "héllo — 日本 🐍\nsecond line"


@pytest.fixture()
def _saved_clipboard():
    """Put the user's clipboard text back after a real-clipboard test."""
    from jarvis.utils import win_clipboard

    before = win_clipboard.get_text()
    yield win_clipboard
    if before:
        win_clipboard.set_text(before)


def test_win_clipboard_is_inert_off_windows(monkeypatch):
    from jarvis.utils import win_clipboard

    monkeypatch.setattr(win_clipboard.sys, "platform", "linux")
    assert win_clipboard.get_text() is None
    assert win_clipboard.set_text("x") is False


@_WINDOWS_ONLY
def test_win_clipboard_round_trips_unicode(_saved_clipboard):
    wc = _saved_clipboard
    assert wc.set_text(_UNICODE_SAMPLE) is True
    assert wc.get_text() == _UNICODE_SAMPLE  # CRLF on the clipboard, \n back out
    assert wc.set_text("") is True
    assert wc.get_text() == ""


@_WINDOWS_ONLY
def test_copy_text_to_clipboard_keeps_non_ascii_on_windows(_saved_clipboard, monkeypatch):
    import jarvis.utils.clipboard as clip

    monkeypatch.setitem(sys.modules, "pyperclip", None)  # force the native path
    assert clip.copy_text_to_clipboard(_UNICODE_SAMPLE) is True
    assert _saved_clipboard.get_text() == _UNICODE_SAMPLE


def test_copy_text_falls_back_to_clip_with_utf16(monkeypatch):
    import jarvis.utils.clipboard as clip
    import jarvis.utils.win_clipboard as win_clipboard

    monkeypatch.setitem(sys.modules, "pyperclip", None)
    monkeypatch.setattr(clip.platform, "system", lambda: "Windows")
    monkeypatch.setattr(win_clipboard, "set_text", lambda text: False)
    runs = []
    monkeypatch.setattr(clip.subprocess, "run", lambda cmd, **k: runs.append((cmd, k["input"])))
    assert clip.copy_text_to_clipboard("héllo") is True
    cmd, data = runs[0]
    assert cmd == ["clip"]
    assert data == "﻿héllo".encode("utf-16-le")  # BOM tells clip.exe it's UTF-16


# ─── clipboard images (/paste) ────────────────────────────────────────────


def test_windows_clipboard_bitmap_becomes_png(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    ImageGrab = pytest.importorskip("PIL.ImageGrab")
    from jarvis.tools import image_input

    monkeypatch.setattr(image_input.sys, "platform", "win32")
    monkeypatch.setattr(image_input.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: Image.new("RGB", (8, 6), "red"), raising=False)
    out = image_input.clipboard_image_to_file()
    assert out == tmp_path / "jarvis_clipboard.png"
    assert Image.open(out).size == (8, 6)


def test_windows_clipboard_copied_file_is_used_directly(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import ImageGrab

    from jarvis.tools import image_input

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    monkeypatch.setattr(image_input.sys, "platform", "win32")
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: [str(notes), str(photo)], raising=False)
    assert image_input.clipboard_image_to_file() == photo
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: [str(notes)], raising=False)
    assert image_input.clipboard_image_to_file() is None
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: None, raising=False)
    assert image_input.clipboard_image_to_file() is None
