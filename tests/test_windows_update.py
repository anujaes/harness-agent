"""Updating Jarvis on Windows while it is running.

The managed install used to start ``jarvis.exe``. Its Python reads that exe
as its script and keeps it open, so ``pip install -e .`` (which rewrites it)
failed with WinError 32 for as long as any Jarvis ran, ``/upgrade`` stopped
there without restarting, and every attempt left a ``~…dist-info`` behind.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

import jarvis.install_sync as sync

MARKER = "rem Jarvis launcher (scripts/install.ps1)"


# ─── The launcher shim ───────────────────────────────────────────────────


def _old_shim(bin_dir: pathlib.Path) -> pathlib.Path:
    shim = bin_dir / "jarvis.cmd"
    shim.write_text(
        f'@echo off\r\n{MARKER}\r\n"%LOCALAPPDATA%\\harness-agent\\.venv\\Scripts\\jarvis.exe" %*\r\n',
        encoding="ascii",
    )
    return shim


def test_old_shim_is_rewritten_to_run_python_m_jarvis(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    shim = _old_shim(tmp_path)
    assert sync.ensure_python_launcher(tmp_path) is True
    text = shim.read_text(encoding="ascii")
    assert MARKER in text
    assert '"%LOCALAPPDATA%\\harness-agent\\.venv\\Scripts\\python.exe" -m jarvis %*' in text
    assert "jarvis.exe" not in text
    assert sync.ensure_python_launcher(tmp_path) is False  # already current: untouched


def test_a_shim_the_installer_did_not_write_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    shim = tmp_path / "jarvis.cmd"
    shim.write_text('@echo off\r\n"C:\\mine\\jarvis.exe" %*\r\n', encoding="ascii")
    assert sync.ensure_python_launcher(tmp_path) is False
    assert "C:\\mine\\jarvis.exe" in shim.read_text(encoding="ascii")


def test_launcher_rewrite_is_windows_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "IS_WINDOWS", False)
    shim = _old_shim(tmp_path)
    assert sync.ensure_python_launcher(tmp_path) is False
    assert "jarvis.exe" in shim.read_text(encoding="ascii")


def test_installer_writes_the_python_m_jarvis_shim():
    ps1 = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
    text = ps1.read_text(encoding="utf-8")
    assert "Scripts\\python.exe" in text and "-m jarvis" in text
    assert "$Target = Join-Path $VenvDir 'Scripts\\jarvis.exe'" not in text


# ─── Only reinstall when packaging changed ───────────────────────────────


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one")
    return root


def _head(root) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


def test_code_only_update_needs_no_pip(repo):
    old = _head(repo)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "code")
    assert sync.packaging_changed(repo, old) is False


def test_dependency_change_needs_pip(repo):
    old = _head(repo)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['y']\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "deps")
    assert sync.packaging_changed(repo, old) is True


def test_unknown_old_head_means_reinstall_to_be_safe(repo):
    assert sync.packaging_changed(repo, "") is True
    assert sync.packaging_changed(repo, "0" * 40) is True


# ─── After a sync: install, skip or defer ────────────────────────────────


@pytest.fixture
def pending(tmp_path, monkeypatch):
    path = tmp_path / "pip_pending"
    monkeypatch.setattr(sync, "_pip_pending_file", lambda: path)
    monkeypatch.setattr(sync, "ensure_python_launcher", lambda *a, **k: False)
    monkeypatch.setattr(sync, "clean_broken_dists", lambda *a, **k: [])
    return path


def test_skips_pip_when_packaging_is_unchanged(pending, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "packaging_changed", lambda root, old: False)
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: pytest.fail("pip should not run"))
    assert sync.finish_update(tmp_path, "old") == "skipped"
    assert not pending.exists()


def test_installs_when_packaging_changed(pending, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "packaging_changed", lambda root, old: True)
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: True)
    assert sync.finish_update(tmp_path, "old") == "installed"
    assert not pending.exists()


def test_failed_pip_is_deferred_to_the_next_launch(pending, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "packaging_changed", lambda root, old: True)
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: False)
    assert sync.finish_update(tmp_path, "old") == "deferred"
    assert pending.read_text(encoding="utf-8").strip() == str(tmp_path)


def test_a_pending_install_also_forces_pip(pending, monkeypatch, tmp_path):
    pending.write_text(str(tmp_path), encoding="utf-8")
    monkeypatch.setattr(sync, "packaging_changed", lambda root, old: False)
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: True)
    assert sync.finish_update(tmp_path, "old") == "installed"
    assert not pending.exists()


def test_finish_update_migrates_the_shim_and_cleans_up(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "_pip_pending_file", lambda: tmp_path / "pip_pending")
    monkeypatch.setattr(sync, "ensure_python_launcher", lambda *a, **k: calls.append("shim") or True)
    monkeypatch.setattr(sync, "clean_broken_dists", lambda *a, **k: calls.append("clean") or [])
    monkeypatch.setattr(sync, "packaging_changed", lambda root, old: False)
    sync.finish_update(tmp_path, "old")
    assert calls == ["shim", "clean"]


def test_pending_install_runs_at_launch_and_clears(pending, monkeypatch, tmp_path):
    assert sync.run_pending_install() is None  # nothing pending
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    pending.write_text(str(tmp_path), encoding="utf-8")
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: False)
    assert sync.run_pending_install() is False
    assert pending.exists()  # still locked: try again next launch
    monkeypatch.setattr(sync, "pip_install_repo", lambda *a, **k: True)
    assert sync.run_pending_install() is True
    assert not pending.exists()


def test_pending_install_for_a_folder_that_is_gone_is_dropped(pending, tmp_path):
    pending.write_text(str(tmp_path / "gone"), encoding="utf-8")
    assert sync.run_pending_install() is None
    assert not pending.exists()


# ─── Leftovers of failed installs ────────────────────────────────────────


def test_broken_dist_info_from_failed_installs_is_removed(tmp_path):
    for name in ("~-rness_jarvis-0.2.5.dist-info", "~.rness_jarvis-0.2.5.dist-info",
                 "~arness_jarvis-0.2.5.dist-info", "~~rness_jarvis-0.2.5.dist-info"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "METADATA").write_text("Name: harness-jarvis\n", encoding="utf-8")
    keep = ["harness_jarvis-0.3.1.dist-info", "~umpy-1.0.dist-info", "requests"]
    for name in keep:
        (tmp_path / name).mkdir()
    removed = sync.clean_broken_dists(tmp_path)
    assert len(removed) == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(keep)  # other packages untouched


# ─── Restarting into the new code ────────────────────────────────────────


def test_windows_restart_runs_python_m_jarvis(monkeypatch):
    """Never the locked jarvis.exe — and a ``-m`` launch has no runnable argv[0]."""
    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    monkeypatch.setattr(sys, "argv", [r"C:\x\.venv\Scripts\jarvis.exe", "--web"])
    assert sync._restart_argv() == [sys.executable, "-m", "jarvis", "--web"]
    monkeypatch.setattr(sys, "argv", [r"C:\x\jarvis\__main__.py"])
    assert sync._restart_argv() == [sys.executable, "-m", "jarvis"]


# ─── /upgrade never gets stuck on pip ────────────────────────────────────


def _upgrade_env(monkeypatch, tmp_path, outcome):
    from types import SimpleNamespace

    from jarvis.commands import upgrade as up

    (tmp_path / ".git").mkdir()
    printed: list[str] = []
    restarted: list[int] = []
    monkeypatch.setattr(up, "find_install_root", lambda: tmp_path)
    monkeypatch.setattr(up, "console", SimpleNamespace(print=lambda m, *a, **k: printed.append(str(m))))
    monkeypatch.setattr(up, "_run", lambda cmd, cwd, timeout=120: (0, "old", ""))
    monkeypatch.setattr(up, "sync_repo_to_remote", lambda repo, **kw: sync.SyncResult(ok=True, branch="b", method="pull"))
    monkeypatch.setattr(up, "finish_update", lambda root, old: outcome)
    monkeypatch.setattr(up, "reexec_jarvis", lambda **kw: restarted.append(1))
    return up, printed, restarted


@pytest.mark.parametrize("outcome", ["skipped", "installed", "deferred"])
def test_upgrade_always_restarts_into_the_new_code(monkeypatch, tmp_path, outcome):
    up, printed, restarted = _upgrade_env(monkeypatch, tmp_path, outcome)
    assert up.cmd_upgrade("") is True
    assert restarted == [1]
    text = "\n".join(printed)
    assert "Upgrade complete" in text
    if outcome == "deferred":
        assert "next launch" in text


# ─── jarvis update / launch ──────────────────────────────────────────────


def test_jarvis_update_with_a_deferred_install_is_not_an_error(monkeypatch, capsys):
    from jarvis import cli

    monkeypatch.setattr("jarvis.updater.force_update", lambda: {
        "status": "updated", "version": "0.3.1", "count": 1, "commits": ["abc fix"],
        "old_head": "a", "new_head": "b", "pip": "deferred", "pip_ok": False})
    assert cli.run_update_cli([]) == 0
    err = capsys.readouterr().err
    assert "next launch" in err


def test_force_update_finishes_a_pending_install_when_up_to_date(monkeypatch, tmp_path):
    from jarvis import updater

    monkeypatch.setattr(updater, "find_install_root", lambda: tmp_path)
    monkeypatch.setattr(updater, "_git", lambda *a, cwd, timeout=30: (0, "same") if a[0] != "rev-list" else (0, "0"))
    monkeypatch.setattr(updater, "run_pending_install", lambda: True)
    res = updater.force_update()
    assert res["status"] == "up_to_date" and res["pip"] == "installed"


def test_launch_finishes_a_pending_install_first(monkeypatch, capsys):
    from jarvis import cli

    monkeypatch.setattr("jarvis.install_sync.run_pending_install", lambda: True)
    cli._finish_pending_install()
    assert "Finished installing" in capsys.readouterr().err
    monkeypatch.setattr("jarvis.install_sync.run_pending_install", lambda: None)
    cli._finish_pending_install()
    assert capsys.readouterr().err == ""


def test_launch_migrates_an_old_shim(monkeypatch):
    """Installs updated by the old code never ran finish_update's migration."""
    from jarvis import cli

    calls = []
    monkeypatch.setattr("jarvis.install_sync.run_pending_install", lambda: None)
    monkeypatch.setattr("jarvis.install_sync.ensure_python_launcher", lambda *a, **k: calls.append(1) or False)
    cli._finish_pending_install()
    assert calls == [1]
