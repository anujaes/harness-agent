"""Read-only paths decode UTF-8 on every OS (Windows defaults to cp1252).

Without ``encoding="utf-8"`` a UTF-8 file reads as mojibake on Windows
(``grüße`` → ``grÃ¼ÃŸe``), and a strict read can crash outright on bytes
cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D).
"""
from __future__ import annotations

from jarvis.constants import set_cwd

# "ŝ" is C5 9D in UTF-8; 0x9D is undefined in cp1252, so a strict read raises.
TEXT = "grüße ŝ ✓ 日本"


def test_rank_files_snippet_is_utf8(tmp_path):
    from jarvis.tools.dirs import rank_files

    set_cwd(tmp_path)
    (tmp_path / "notes.md").write_text(f"# Greeting\n{TEXT}\n", encoding="utf-8")
    out = rank_files("greeting", include_snippets=True)
    assert TEXT in out


def test_context_graph_reads_source_as_utf8(tmp_path):
    from jarvis.tools.context.extract import _parse_file_for_graph

    set_cwd(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text(f'"""{TEXT}"""\n\n\ndef grüße():\n    return "ŝ"\n', encoding="utf-8")
    _rel, _imports, symbols, _types = _parse_file_for_graph(f, {}, {})
    assert "grüße" in symbols


def test_notes_command_shows_utf8_notes(tmp_path, monkeypatch):
    from jarvis.commands import context

    notes = tmp_path / "notes.md"
    notes.write_text(f"- {TEXT}\n", encoding="utf-8")  # how /note writes it
    monkeypatch.setattr(context, "NOTES_FILE", notes)
    printed = []
    monkeypatch.setattr(context.console, "print", lambda *a, **k: printed.extend(a))
    handled, _ = context.handle_context("/notes", "")
    assert handled
    panel = printed[0]
    assert TEXT in panel.renderable.markup
