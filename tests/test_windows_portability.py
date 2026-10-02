"""Cross-platform helpers behind the Windows port: command lines, editors, key hints, private files."""
from __future__ import annotations

import sys

import pytest

from jarvis.tui import keys
from jarvis.utils import cmdline, editor, io


# ─── Command lines ──────────────────────────────────────────────────────


def test_split_command_keeps_windows_paths(monkeypatch):
    monkeypatch.setattr(cmdline, "IS_WINDOWS", True)
    assert cmdline.split_command(r'npx -y server C:\Users\me\notes') == ["npx", "-y", "server", r"C:\Users\me\notes"]
    assert cmdline.split_command(r'"C:\Program Files\Tool\tool.exe" --flag "two words"') == [
        r"C:\Program Files\Tool\tool.exe", "--flag", "two words",
    ]


def test_split_command_is_shlex_on_posix(monkeypatch):
    monkeypatch.setattr(cmdline, "IS_WINDOWS", False)
    assert cmdline.split_command("code --wait 'my file.txt'") == ["code", "--wait", "my file.txt"]


def test_looks_like_path_knows_both_styles():
    for p in ("/usr/bin/x", "./x", "../x", "~/x", r"C:\tools\x.exe", "C:/tools/x.exe", r".\x.exe", r"\\host\share\x"):
        assert cmdline.looks_like_path(p), p
    for p in ("npx", "linear", "https://mcp.example.com"):
        assert not cmdline.looks_like_path(p), p


def test_mcp_launcher_pasted_with_windows_paths(monkeypatch):
    from jarvis.mcp import install

    monkeypatch.setattr(cmdline, "IS_WINDOWS", True)
    (spec,) = install.parse_source(r'"C:\tools\my server\srv.exe" --root C:\data')
    assert spec["entry"]["command"] == r"C:\tools\my server\srv.exe"
    assert spec["entry"]["args"] == ["--root", r"C:\data"]


# ─── $EDITOR ────────────────────────────────────────────────────────────


def test_editor_value_with_arguments_is_split(monkeypatch):
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.delenv("VISUAL", raising=False)
    argv = editor.editor_argv()
    assert argv[1:] == ["--wait"] and argv[0].lower().replace("\\", "/").split("/")[-1].startswith("code")


def test_editor_that_is_one_path_with_spaces_is_not_split(monkeypatch, tmp_path):
    exe = tmp_path / "My Editor" / "edit tool"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("EDITOR", str(exe))
    monkeypatch.delenv("VISUAL", raising=False)
    assert editor.editor_argv() == [str(exe)]


def test_editor_falls_back_per_os(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    assert editor.editor_argv() is None
    monkeypatch.setattr(editor, "IS_WINDOWS", True)
    assert editor.default_editor() == "notepad"
    monkeypatch.setattr(editor, "IS_WINDOWS", False)
    assert editor.default_editor() == "nano"
    assert editor.editor_argv(fallback=True) == ["nano"]


def test_open_in_editor_launches_argv_plus_path(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(editor.subprocess, "Popen", lambda cmd: launched.append(cmd))
    monkeypatch.setattr(editor, "editor_argv", lambda fallback=False: ["subl", "-w"])
    target = tmp_path / "a.md"
    assert editor.open_in_editor(target) is True
    assert launched == [["subl", "-w", str(target)]]
    monkeypatch.setattr(editor, "editor_argv", lambda fallback=False: None)
    assert editor.open_in_editor(target) is False


# ─── Key hints ──────────────────────────────────────────────────────────


def test_windows_key_labels():
    assert keys.windows_key_label("⌃G undo · ⇧⇥ plan · ⌥↑ · ↵ send · ⌃⇧U") == \
        "Ctrl+G undo · Shift+Tab plan · Alt+↑ · Enter send · Ctrl+Shift+U"


def test_windows_key_table_keeps_the_columns_aligned():
    text = "[bold]Chat[/]\n  ⌃C             quit\n  ⇧↵  ⌃J         new line\n                 more text\n  • a tip line here"
    out = keys.windows_key_table(text).splitlines()
    assert out[0] == "[bold]Chat[/]"
    assert out[1].startswith("  Ctrl+C ") and out[2].startswith("  Shift+Enter  Ctrl+J ")
    col = out[1].index("quit")
    assert out[2].index("new line") == col and out[3].index("more text") == col
    assert out[4] == "  • a tip line here"


@pytest.mark.parametrize("windows, expected", [(True, "Shift+Enter newline"), (False, "⇧↵ newline")])
def test_welcome_hints_spell_keys_for_the_os(monkeypatch, windows, expected):
    """The welcome block's key row (``⇧↵ newline``) reads ``Shift+Enter`` on Windows."""
    from jarvis.tui.transcript import WelcomeBlock

    monkeypatch.setattr(keys, "IS_WINDOWS", windows)
    text = WelcomeBlock({"version": "0", "cwd": "C:/x"}).render().plain
    assert expected in text
    if windows:
        assert "⇧" not in text


def test_key_label_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(keys, "IS_WINDOWS", False)
    assert keys.key_label("⌃G") == "⌃G" and keys.key_table("  ⌃C             quit") == "  ⌃C             quit"


# ─── Private files ──────────────────────────────────────────────────────


def test_secure_write_is_owner_only(tmp_path, monkeypatch, owner_only):
    monkeypatch.setattr(io, "CONFIG_DIR", tmp_path)
    secret = tmp_path / "key.txt"
    io._secure_write(secret, "sk-test-ünïcode")
    assert secret.read_text(encoding="utf-8") == "sk-test-ünïcode"
    assert owner_only(secret)
    io._secure_write(secret, "sk-rotated")  # rewrite in place keeps it private
    assert owner_only(secret)


@pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
def test_restrict_to_owner_never_raises_for_a_missing_file(tmp_path):
    assert io.restrict_to_owner(tmp_path / "missing.json") is False


@pytest.mark.skipif(sys.platform != "win32", reason="SIDs are Windows-only")
def test_current_user_sid_matches_whoami():
    sid = io.current_user_sid()
    assert sid and sid.startswith("S-1-5-")
    assert io._sid_from_whoami() == sid


def test_failed_restriction_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(io, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(io, "IS_WINDOWS", True)
    calls = []
    monkeypatch.setattr(io, "restrict_to_owner", lambda p: calls.append(p) or False)
    target = tmp_path / "token.json"
    io._secure_write(target, "a")
    io._secure_write(target, "b")  # the first lock-down failed, so it is tried again
    assert len(calls) == 2 and str(target) not in io._restricted


def test_unresolvable_user_means_no_icacls_and_no_success(tmp_path, monkeypatch):
    monkeypatch.setattr(io, "current_user_sid", lambda: None)
    ran = []
    monkeypatch.setattr(io.subprocess, "run", lambda *a, **k: ran.append(a))
    assert io._windows_owner_only(tmp_path / "x") is False and ran == []
