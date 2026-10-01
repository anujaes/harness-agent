"""Shared test isolation."""
import os
import shutil
import tempfile

import pytest

# Point the home directory at a scratch folder BEFORE any jarvis module is
# imported: jarvis.constants.paths resolves ~/.config/harness-agent and
# ~/.harness at import time, and many tests save sessions, settings, the
# provider choice … through those constants. Without this the suite writes
# into the developer's real Jarvis config (and a first launch afterwards finds
# a provider the tests picked). HOME covers POSIX; USERPROFILE is what
# Path.home() reads on Windows.
_REAL_HOME = os.path.expanduser("~")
_TEST_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TEST_HOME
os.environ["USERPROFILE"] = _TEST_HOME
# git reads its global identity from the (now empty) home; tests that commit need one.
for _var, _val in (("GIT_AUTHOR_NAME", "Jarvis Tests"), ("GIT_AUTHOR_EMAIL", "tests@example.invalid"),
                   ("GIT_COMMITTER_NAME", "Jarvis Tests"), ("GIT_COMMITTER_EMAIL", "tests@example.invalid")):
    os.environ.setdefault(_var, _val)


def pytest_unconfigure(config):
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_pet(tmp_path, monkeypatch):
    """Jarvis the pet saves to ~/.config/harness-agent/pet.json on every
    reaction (turn done, tool finished, …). Keep every test — including TUI
    tests that finish turns — away from the real file."""
    import jarvis.pet as pet_pkg
    import jarvis.pet.model as pet_model

    import jarvis.pet.session as pet_session

    monkeypatch.setattr(pet_model, "PET_FILE", tmp_path / "pet.json")
    pet_model.reset_cache()
    pet_session.reset()
    yield
    pet_model.reset_cache()
    pet_pkg.set_reaction_hook(None)
    pet_pkg.set_action_hook(None)


@pytest.fixture(autouse=True)
def _no_macos_permission_prompts(monkeypatch):
    """Never let a test pop the macOS Screen Recording prompt or open System
    Settings on the developer's machine."""
    import importlib

    shot = importlib.import_module("jarvis.tools.screenshot")
    monkeypatch.setattr(shot, "request_screen_recording", lambda: None)
    monkeypatch.setattr(shot, "_permission_requested", False)


@pytest.fixture()
def ext_env(tmp_path, monkeypatch):
    """Skills / MCP installs against a scratch HOME + project folder.

    Nothing reaches the real ``~/.config/harness-agent``, ``~/.harness``,
    ``~/.claude`` … or the current project, and no config is saved to settings.
    """
    import pathlib
    import types

    import jarvis.mcp.auth as mcp_auth
    import jarvis.mcp.config as mcp_config
    import jarvis.mcp.secrets as mcp_secrets
    import jarvis.storage.skill_install as skill_install
    import jarvis.storage.skills as skills
    from jarvis import state

    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(mcp_config, "MCP_GLOBAL_CONFIG_FILE", home / ".config" / "harness-agent" / "mcp.json")
    monkeypatch.setattr(
        mcp_config, "_global_sources",
        lambda: [("jarvis", mcp_config.MCP_GLOBAL_CONFIG_FILE, "")],
    )
    monkeypatch.setattr(mcp_secrets, "SECRETS_FILE", home / ".config" / "harness-agent" / "mcp_secrets.json")
    monkeypatch.setattr(mcp_auth, "AUTH_DIR", home / ".config" / "harness-agent" / "mcp-auth")
    monkeypatch.setattr(skill_install, "HARNESS_SKILLS_DIR", home / ".harness" / "skills")
    monkeypatch.setattr(skills, "HARNESS_SKILLS_DIR", home / ".harness" / "skills")
    monkeypatch.setattr(skills, "CONFIG_DIR", home / ".config" / "harness-agent")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(state, "global_mcp", False)
    monkeypatch.setattr(state, "global_skills", False)
    monkeypatch.setattr(state, "save_mcp_config", lambda: None)
    monkeypatch.setattr(state, "save_skills_config", lambda: None)
    monkeypatch.setattr(mcp_config, "_config", None)
    skills.invalidate_cache()
    yield types.SimpleNamespace(home=home, proj=proj)
    monkeypatch.setattr(mcp_config, "_config", None)
    skills.invalidate_cache()

@pytest.fixture()
def owner_only():
    """``owner_only(path)`` -> True when only the current user can open ``path``.

    POSIX: mode 600. Windows: no inherited ACEs and a single grant, to the
    current user (what ``jarvis.utils.io.restrict_to_owner`` sets via icacls).
    """
    import os
    import stat
    import subprocess
    import sys

    def check(path) -> bool:
        if sys.platform != "win32":
            return stat.S_IMODE(os.stat(path).st_mode) == 0o600
        out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
        # "<path> DOMAIN\user:(F)" then "Successfully processed ..." — the ACEs are
        # the "principal:(rights)" chunks; the path prefix only shows on line one.
        body = out.replace(str(path), "", 1)
        aces = [line.strip() for line in body.splitlines() if ":(" in line]
        from jarvis.utils.io import current_user_sid

        # icacls prints the account name, or "*S-1-…" when the SID has no name.
        mine = {os.environ.get("USERNAME", "").lower(), f"*{(current_user_sid() or '').lower()}"}
        return (
            len(aces) == 1
            and "(I)" not in aces[0]
            and aces[0].split(":(")[0].lower().split("\\")[-1] in mine
        )

    return check

@pytest.fixture()
def fake_program(tmp_path):
    """``make(name, source)`` -> path of a runnable fake CLI whose body is Python ``source``.

    POSIX: an executable script with a shebang for this interpreter. Windows
    can't run shebang scripts, so there it is ``<name>.py`` plus a ``<name>.cmd``
    launcher (what ``subprocess`` can start, like a real ``cloudflared.exe``).
    """
    import stat
    import sys
    import textwrap

    def make(name: str, source: str) -> str:
        body = textwrap.dedent(source).lstrip()
        if sys.platform == "win32":
            script = tmp_path / f"{name}.py"
            script.write_text(body, encoding="utf-8")
            launcher = tmp_path / f"{name}.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
            return str(launcher)
        script = tmp_path / name
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    return make

@pytest.fixture(autouse=True)
def _restore_project_root():
    """Tests that call ``set_cwd(tmp)`` must not leave every later test (and the
    shell tools) pointing at a temp folder that pytest deletes afterwards —
    Windows then refuses to start processes there (WinError 267)."""
    import os

    from jarvis.constants import paths

    root, cwd = paths.CWD, os.getcwd()
    yield
    if paths.CWD != root:
        paths.set_cwd(root)
    if os.getcwd() != cwd and os.path.isdir(cwd):
        os.chdir(cwd)
