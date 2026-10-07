"""File tools read and write UTF-8 on every OS.

Windows' default text encoding is cp1252: without ``encoding="utf-8"`` a
UTF-8 file reads back as mojibake (``café`` → ``cafÃ©``), an edit writes that
mojibake back into the file, and characters cp1252 lacks (``✓``) can't be
written at all.
"""
from __future__ import annotations

import pytest

from jarvis.constants import set_cwd
from jarvis.tools import files

TEXT = "café ✓ — naïve 日本\n"


@pytest.fixture
def proj(tmp_path):
    set_cwd(tmp_path)
    return tmp_path


def test_read_file_decodes_utf8(proj):
    (proj / "u.txt").write_text(TEXT, encoding="utf-8")
    assert files.read_file("u.txt") == TEXT
    assert "café ✓" in files.read_file_tool("u.txt")


def test_write_file_writes_utf8(proj):
    out = files.write_file("w.txt", TEXT)
    assert not out.startswith("ERROR"), out
    assert (proj / "w.txt").read_text(encoding="utf-8") == TEXT


def test_edit_file_keeps_the_rest_of_the_file_intact(proj):
    (proj / "e.txt").write_text(TEXT, encoding="utf-8")
    files.read_file("e.txt")
    out = files.edit_file("e.txt", "naïve", "naive ✓")
    assert not out.startswith("ERROR"), out
    assert (proj / "e.txt").read_text(encoding="utf-8") == "café ✓ — naive ✓ 日本\n"


def test_multi_edit_keeps_utf8(proj):
    (proj / "m.txt").write_text(TEXT, encoding="utf-8")
    files.read_file("m.txt")
    out = files.multi_edit([{"path": "m.txt", "old_str": "café", "new_str": "thé"}])
    assert "ERROR" not in out, out
    assert (proj / "m.txt").read_text(encoding="utf-8") == "thé ✓ — naïve 日本\n"
