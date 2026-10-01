"""search_code / git_* / fast_find — argv-based, no shell, portable."""
import os
import pathlib
import shutil
import subprocess

import pytest

from jarvis.constants import SEARCH_MATCH_CAP
from jarvis.tools import dirs, search
from jarvis.tools import shell as shell_mod


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def alpha():\n    return 'needle'\n", encoding="utf-8")
    (root / "pkg" / "b.txt").write_text("no match here\nneedle again — ünïcode\n", encoding="utf-8")
    for skipped in ("node_modules/x.js", ".git/config", ".hidden/c.py", "build/out.py"):
        (root / skipped).parent.mkdir(parents=True, exist_ok=True)
        (root / skipped).write_text("needle\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"needle\x00\x01\x02")
    return root


@pytest.fixture
def builtin_only(monkeypatch):
    """No rg / grep on PATH: search_code must fall back to its own search."""
    real = search.which_exe
    monkeypatch.setattr(search, "which_exe", lambda name: None if name in ("rg", "grep") else real(name))


def _rows(out: str) -> list[str]:
    head, code, *rows = out.split("\n")
    assert head.startswith("$ ") and code.startswith("exit=")
    return [r for r in rows if r]


def test_builtin_search_rows_match_ripgrep_format(tree, builtin_only):
    out = search.search_code("needle", str(tree), allow_outside_project=True)
    assert "\nexit=0\n" in out
    base = str(tree).replace("\\", "/")
    assert sorted(_rows(out)) == [
        f"{base}/pkg/a.py:2:    return 'needle'",
        f"{base}/pkg/b.txt:2:needle again — ünïcode",
    ]  # no node_modules / .git / hidden / build / binary files


def test_builtin_search_single_file_has_no_path_prefix(tree, builtin_only):
    out = search.search_code("alpha", str(tree / "pkg" / "a.py"), allow_outside_project=True)
    assert _rows(out) == ["1:def alpha():"]


def test_builtin_search_no_match_and_bad_regex(tree, builtin_only):
    assert "\nexit=1\n" in search.search_code("zzz_nothing", str(tree), allow_outside_project=True)
    out = search.search_code("return '(", str(tree), allow_outside_project=True)  # invalid regex → literal
    assert "\nexit=1\n" in out
    out = search.search_code("alpha(", str(tree), allow_outside_project=True)
    assert _rows(out)[0].endswith(":1:def alpha():")


def test_builtin_search_caps_matches_per_file(tree, builtin_only):
    (tree / "many.txt").write_text("hit\n" * (SEARCH_MATCH_CAP + 30), encoding="utf-8")
    rows = _rows(search.search_code("hit", str(tree), allow_outside_project=True))
    assert len(rows) == SEARCH_MATCH_CAP


def test_builtin_search_honours_gitignore(tree, builtin_only):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / ".gitignore").write_text("pkg/b.txt\n", encoding="utf-8")
    rows = _rows(search.search_code("needle", str(tree), allow_outside_project=True))
    assert [r.split(":")[-2] for r in rows] == ["2"] and all("a.py" in r for r in rows)


def test_patterns_are_never_shell_parsed(tree):
    """A pattern full of shell syntax is passed as one argv element."""
    (tree / "q.txt").write_text("a $(x) `y` \"z\" 'w' | & ; *\n", encoding="utf-8")
    out = search.search_code("`y` \"z\" 'w'", str(tree), allow_outside_project=True)
    assert "q.txt:1:" in out


def test_grep_and_builtin_agree(tree, monkeypatch):
    if not shell_mod.which("grep") or shell_mod.which("rg"):
        pytest.skip("needs grep (and no rg) on PATH")
    via_grep = sorted(_rows(search.search_code("needle", str(tree / "pkg"), allow_outside_project=True)))
    real = search.which_exe
    monkeypatch.setattr(search, "which_exe", lambda name: None if name in ("rg", "grep") else real(name))
    via_builtin = sorted(_rows(search.search_code("needle", str(tree / "pkg"), allow_outside_project=True)))
    assert via_grep == via_builtin


# ── git ─────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (root / "odd name's `$x` (1).txt").write_text("one\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first")
    monkeypatch.setattr(shell_mod, "CWD", root)
    return root


def test_git_tools_run_without_a_shell(repo):
    from jarvis.tools.git import git_diff, git_log, git_status

    target = repo / "odd name's `$x` (1).txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    status = git_status()
    assert status.startswith("$ git --no-pager status -sb\nexit=0\n")
    diff = git_diff(target.name)
    assert "\nexit=0\n" in diff and "+two" in diff
    assert git_diff(target.name, staged=True).endswith("exit=0\n")
    log = git_log(1)
    assert "\nexit=0\n" in log and "first" in log


# ── fast_find ───────────────────────────────────────────────────────────


@pytest.fixture
def files(tmp_path):
    root = tmp_path / "home"
    for rel in ("Desktop/qr-code.png", "Desktop/QR notes.txt", "Docs/deep/more/qr_scan.PNG",
                "node_modules/qr.png", "Docs/qr-folder/keep.txt"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x", encoding="utf-8")
    return root


def test_walk_find_names_kinds_and_extensions(files):
    found = dirs._walk_find("qr", str(files), "any", set(), 50)
    names = sorted(pathlib.Path(p).name for p in found)
    assert names == ["QR notes.txt", "qr-code.png", "qr-folder", "qr_scan.PNG"]  # node_modules skipped
    assert all(os.path.isdir(p) for p in dirs._walk_find("qr", str(files), "folder", set(), 50))
    pngs = dirs._walk_find("qr", str(files), "file", {".png"}, 50)
    assert sorted(pathlib.Path(p).name for p in pngs) == ["qr-code.png", "qr_scan.PNG"]
    assert len(dirs._walk_find("qr", str(files), "any", set(), 2)) == 2
    # breadth-first: shallow matches come first
    assert pathlib.Path(found[0]).parent.name in ("Desktop", "Docs")


@pytest.mark.skipif(os.name != "nt", reason="Windows fast_find chain")
def test_fast_find_on_windows_falls_back_to_the_walk(files, monkeypatch):
    monkeypatch.setattr(dirs, "_everything_find", lambda *a, **k: [])
    monkeypatch.setattr(dirs, "_windows_search_find", lambda *a, **k: [])
    monkeypatch.setattr(dirs.shutil, "which", lambda *a, **k: None)
    out = dirs.fast_find("qr", path=str(files), kind="file", ext="png")
    lines = out.splitlines()
    assert lines[0] == f"Found 2 match(es) for 'qr' in {files}"
    assert sorted(pathlib.Path(p).name for p in lines[1:]) == ["qr-code.png", "qr_scan.PNG"]
    assert dirs.fast_find("zzz_none", path=str(files)) == f"No matches for 'zzz_none' in {files}"


@pytest.mark.skipif(os.name != "nt", reason="Windows fast_find chain")
def test_fast_find_prefers_everything_and_tops_up_the_index(files, monkeypatch):
    hit = str(files / "Desktop" / "qr-code.png")
    monkeypatch.setattr(dirs, "_everything_find", lambda *a, **k: [hit])
    walked = []
    monkeypatch.setattr(dirs, "_walk_find", lambda *a, **k: walked.append(a) or [])
    assert dirs.fast_find("qr", path=str(files)).splitlines()[1:] == [hit]
    assert walked == []  # Everything indexes the whole disk: no walk needed

    monkeypatch.setattr(dirs, "_everything_find", lambda *a, **k: [])
    monkeypatch.setattr(dirs, "_windows_search_find", lambda *a, **k: [hit])
    other = str(files / "Docs" / "qr-folder")
    monkeypatch.setattr(dirs, "_walk_find", lambda *a, **k: [hit.upper(), other])
    assert dirs.fast_find("qr", path=str(files)).splitlines()[1:] == [hit, other]  # deduped


def test_search_index_query_text_is_escaped():
    assert dirs._sql_like("50%_[x]'s") == "50[%][_][[]x]''s"



@pytest.mark.skipif(os.name != "nt", reason="Windows shims")
def test_cmd_shims_are_never_run_with_tool_arguments(tmp_path, monkeypatch):
    """rg.cmd / git.cmd on PATH would hand a model-written argument to
    cmd.exe (`x" & calc & "`): such shims are refused and search_code falls
    back to its built-in search."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "pwned.txt"
    for name in ("rg", "grep", "gitx"):
        (bin_dir / f"{name}.cmd").write_text(f"@echo off\r\necho ran> \"{marker}\"\r\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    assert shell_mod.which("rg").lower().endswith(".cmd")
    assert shell_mod.which_exe("rg") is None and shell_mod.which_exe("rg.cmd") is None

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.txt").write_text('x" & calc & "\n', encoding="utf-8")
    out = search.search_code('x" & calc & "', str(proj), allow_outside_project=True)
    assert out.startswith(("$ (built-in search", "$ grep")) and "a.txt:1:" in out  # never rg.cmd
    refused = shell_mod.run_argv(["gitx", "status"])
    assert "exit=127" in refused and ".cmd/.bat shim" in refused
    with pytest.raises(OSError):
        shell_mod.run_process([str(bin_dir / "rg.cmd"), "x"], timeout=5)
    assert not marker.exists()


def test_search_index_uses_the_shared_powershell_quoting(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows Search")
    import importlib

    powershell = importlib.import_module("jarvis.tools.windows.powershell")

    seen = []
    monkeypatch.setattr(powershell, "run_ps", lambda script, timeout=0: seen.append(script)
                        or subprocess.CompletedProcess([], 0, "C:\\x\\it's.txt\n", ""))
    assert dirs._windows_search_find("it's ‘q’", "", "any", 5) == ["C:\\x\\it's.txt"]
    sql = "SELECT TOP 5 System.ItemPathDisplay FROM SYSTEMINDEX WHERE System.FileName LIKE '%it''s ‘q’%' AND SCOPE='file:'"
    assert powershell.ps_quote(sql) in seen[0]


def test_relative_labels_use_forward_slashes(tmp_path, monkeypatch):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(dirs, "CWD", tmp_path)
    assert dirs.glob_files("**/*.py", str(tmp_path), allow_outside_project=True) == "a/b/c.py"
    assert dirs._safe_rel(tmp_path / "a" / "b" / "c.py") == "a/b/c.py"
