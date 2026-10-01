"""Install sync helpers."""
import pathlib

from jarvis.install_sync import (
    MANAGED_INSTALL_DIR,
    find_install_root,
    harness_agent_models_available,
    is_managed_install,
    sync_repo_to_remote,
)


def test_find_install_root_from_dev_tree():
    root = find_install_root()
    assert root is not None
    assert (root / "jarvis" / "cli.py").is_file()
    assert (root / "pyproject.toml").is_file()


def test_harness_agent_models_available():
    assert harness_agent_models_available() is True


def test_is_managed_install():
    assert is_managed_install(MANAGED_INSTALL_DIR) is True
    assert is_managed_install(pathlib.Path("/tmp/harness-dev")) is False


def test_sync_repo_to_remote_blocks_dirty_dev_clone(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "jarvis.install_sync.is_managed_install",
        lambda _root: False,
    )
    monkeypatch.setattr(
        "jarvis.install_sync._git_run",
        lambda root, args, **kwargs: (
            (0, "", "")
            if args[:2] == ["fetch", "origin"]
            else (0, "main", "")
            if args[:2] == ["rev-parse", "--abbrev-ref"]
            else (0, "", "")
        ),
    )
    monkeypatch.setattr(
        "jarvis.install_sync.git_dirty_files",
        lambda _root: ["jarvis/constants/providers.py"],
    )

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is False
    assert "uncommitted local changes" in result.error


def test_sync_repo_to_remote_resets_dirty_managed_install(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:2] == ["reset", "--hard"]:
            return 0, "HEAD is now at cf7508f", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: True)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr(
        "jarvis.install_sync.git_dirty_files",
        lambda _root: ["jarvis/constants/providers.py", "tests/test_oauth.py"],
    )

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "reset"
    assert result.discarded_local == (
        "jarvis/constants/providers.py",
        "tests/test_oauth.py",
    )
    assert any(args[:2] == ["reset", "--hard"] for args in calls)


def test_sync_repo_to_remote_managed_install_always_resets(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:2] == ["reset", "--hard"]:
            return 0, "HEAD is now at cf7508f", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: True)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr("jarvis.install_sync.git_dirty_files", lambda _root: [])

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "reset"
    assert result.discarded_local == ()
    assert not any(args[:3] == ["pull", "--ff-only", "origin"] for args in calls)


def test_sync_repo_to_remote_pulls_clean_tree(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:3] == ["pull", "--ff-only", "origin"]:
            return 0, "Already up to date.", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: False)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr("jarvis.install_sync.git_dirty_files", lambda _root: [])

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "pull"
    assert result.discarded_local == ()
    assert any(args[:3] == ["pull", "--ff-only", "origin"] for args in calls)

def test_managed_install_dir_matches_the_installer(monkeypatch):
    import jarvis.install_sync as sync

    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    monkeypatch.delenv("JARVIS_INSTALL_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert sync._managed_install_dir() == pathlib.Path(r"C:\Users\me\AppData\Local") / "harness-agent"
    monkeypatch.setenv("JARVIS_INSTALL_DIR", r"D:\tools\jarvis")
    assert sync._managed_install_dir() == pathlib.Path(r"D:\tools\jarvis")
    assert "install.ps1 | iex" in sync.install_command()

    monkeypatch.setattr(sync, "IS_WINDOWS", False)
    assert sync._managed_install_dir() == pathlib.Path("~/.local/share/harness-agent").expanduser()
    assert sync.install_command().endswith("/scripts/install | bash")


def test_pip_install_moves_a_locked_launcher_aside_and_restores_it_on_failure(monkeypatch, tmp_path):
    import subprocess

    import jarvis.install_sync as sync

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "jarvis.exe").write_bytes(b"old launcher")
    (scripts / "jarvis.exe.1.old").write_bytes(b"stale")
    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    monkeypatch.setattr(sync, "running_python", lambda: str(scripts / "python.exe"))
    seen = []

    def fake_pip(cmd, **_kw):
        seen.append(sorted(p.name for p in scripts.iterdir()))
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(sync.subprocess, "run", fake_pip)
    assert sync.pip_install_repo(tmp_path) is False
    # while pip ran the launcher was out of the way; the stale copy is gone
    assert seen[0] == [f"jarvis.exe.{sync.os.getpid()}.old"]
    # pip failed without writing a new one: the old launcher is back
    assert sorted(p.name for p in scripts.iterdir()) == ["jarvis.exe"]
    assert (scripts / "jarvis.exe").read_bytes() == b"old launcher"


def test_windows_restart_from_a_worker_asks_the_ui_to_close_first(monkeypatch):
    import threading

    import jarvis.install_sync as sync

    monkeypatch.setattr(sync, "IS_WINDOWS", True)
    monkeypatch.setattr(sync, "_restart_pending", False)
    monkeypatch.delenv("HARNESS_UPDATED_REEXEC", raising=False)
    closed, children = [], []
    monkeypatch.setattr(sync, "_restart_in_child", lambda: children.append(1))
    monkeypatch.setattr(sync.os, "execv", lambda *a: (_ for _ in ()).throw(AssertionError("execv on Windows")))
    sync.set_restart_handler(lambda: closed.append(1))
    try:
        worker = threading.Thread(target=sync.reexec_jarvis)
        worker.start()
        worker.join()
        assert closed == [1] and children == []  # the UI closes; nothing spawned yet
        sync.finish_pending_restart()  # main thread, after the UI is gone
        assert children == [1]
        sync.finish_pending_restart()
        assert children == [1]  # only once
    finally:
        sync.set_restart_handler(None)
        monkeypatch.delenv("HARNESS_UPDATED_REEXEC", raising=False)
