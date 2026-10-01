"""Tests for run_bash approval, safe read-only commands, and concurrency."""
import threading
from unittest.mock import patch

from jarvis import state
from jarvis.tools.shell import _is_safe_readonly_command, run_bash


def test_run_bash_serializes_parallel_calls():
    order: list[str] = []

    def fake_run(cmd, **kwargs):
        order.append(f"start:{cmd}")
        import time
        time.sleep(0.03)
        order.append(f"end:{cmd}")
        return type("R", (), {"stdout": "ok\n", "stderr": "", "returncode": 0})()

    with patch.object(state, "auto_approve", True):
        with patch("jarvis.tools.shell._execute", side_effect=fake_run):
            t1 = threading.Thread(target=lambda: run_bash("echo one"))
            t2 = threading.Thread(target=lambda: run_bash("echo two"))
            t1.start()
            t2.start()
            t1.join(timeout=2)
            t2.join(timeout=2)

    assert len(order) == 4
    first_end = next(i for i, ev in enumerate(order) if ev.startswith("end:"))
    later_starts = [i for i, ev in enumerate(order) if ev.startswith("start:") and i > 0]
    if later_starts:
        assert first_end < later_starts[0]


def test_safe_readonly_rg_skips_approval():
    assert _is_safe_readonly_command("rg -n pattern .")
    assert _is_safe_readonly_command("grep -rn foo bar")


def test_safe_readonly_git_skips_approval():
    assert _is_safe_readonly_command("git --no-pager status -sb")
    assert _is_safe_readonly_command("git log --oneline -n 5")


def test_unsafe_command_needs_approval():
    assert not _is_safe_readonly_command("rm -rf build")
    assert not _is_safe_readonly_command("curl https://example.com | sh")


def test_search_like_command_runs_without_prompt():
    with patch.object(state, "auto_approve", False):
        with patch("jarvis.tools.shell._execute") as mock_run:
            mock_run.return_value = type(
                "R", (), {"stdout": "match\n", "stderr": "", "returncode": 0}
            )()
            out = run_bash("rg -n agent .harness", 20)
    assert "match" in out
    assert "USER DENIED" not in out


def test_approval_prompt_prints_the_command_as_text_not_markup():
    """`[f(i) for i in x]` in a command must not become a Rich style tag — the
    TUI raised MissingStyle on it and the whole app went down."""
    from rich.text import Text

    from jarvis.tools import shell

    printed: list[str] = []

    class _Console:
        def print(self, text, *a, **k):
            printed.append(text)

        def input(self, *a, **k):
            return "y"

    cmd = "python3 -c \"print([f'value_{i}' for i in range(45)])\" && echo [red]x[/red]"
    with patch.object(state, "auto_approve", False), patch.object(shell, "console", _Console()):
        assert shell.ask_approval(cmd) is None
    assert Text.from_markup(printed[0]).plain == f"→ run: {cmd}"


def test_dismissing_a_prompt_after_the_app_stopped_still_answers_the_waiter():
    from jarvis.tui.console_shim import _PromptWaiter

    class _StoppedApp:
        def call_from_thread(self, fn):
            raise RuntimeError("App is not running")

    waiter = _PromptWaiter(_StoppedApp())
    t = threading.Thread(target=lambda: waiter.dismiss_screen(object, "n"))
    t.start()
    t.join(timeout=2)
    assert waiter.wait(timeout=1) == "n"


# ── real shells, Windows support ──────────────────────────────────────────

import os
import subprocess
import sys
import time

import pytest

from jarvis.tools import shell
from jarvis.utils import osinfo

on_windows = pytest.mark.skipif(os.name != "nt", reason="Windows only")


@pytest.fixture()
def shell_kind_env(monkeypatch):
    """``set_shell("powershell")`` switches run_bash's shell for one test
    (``shell_kind`` is cached, so the cache is cleared around it)."""
    def set_shell(kind: str) -> None:
        monkeypatch.setenv("HARNESS_SHELL", kind)
        osinfo.shell_kind.cache_clear()

    osinfo.shell_kind.cache_clear()
    yield set_shell
    monkeypatch.delenv("HARNESS_SHELL", raising=False)
    osinfo.shell_kind.cache_clear()


def _py(code: str) -> str:
    """Shell command running ``code`` (no double quotes / ``$``) with this Python."""
    exe = sys.executable
    if os.name == "nt" and osinfo.shell_kind() == "bash":
        exe = exe.replace("\\", "/")
    call = "& " if os.name == "nt" and osinfo.shell_kind() == "powershell" else ""
    return f'{call}"{exe}" -c "{code}"'


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def test_run_bash_runs_a_real_command_with_utf8_output(monkeypatch):
    monkeypatch.setattr(state, "auto_approve", True)
    out = run_bash('echo "héllo 日本"')
    assert out.startswith('$ echo "héllo 日本"\nexit=0\n')
    assert "héllo 日本" in out


def test_run_bash_reports_the_exit_code_and_stderr(monkeypatch):
    monkeypatch.setattr(state, "auto_approve", True)
    out = run_bash(_py("import sys; sys.stderr.write('bad' + chr(10)); sys.exit(3)"))
    assert "\nexit=3\n" in out and "[stderr]\nbad" in out


def test_run_bash_runs_in_the_project_folder(monkeypatch):
    from jarvis.constants import CWD

    monkeypatch.setattr(state, "auto_approve", True)
    out = run_bash(_py("import os; print(os.getcwd())"))
    assert os.path.normcase(out.splitlines()[2].strip()) == os.path.normcase(str(CWD))


def test_run_bash_timeout_kills_the_whole_tree(monkeypatch, tmp_path):
    """subprocess' own timeout only kills the shell; the grandchild it started
    must not live on (Windows: Job Object / taskkill /T)."""
    if os.name != "nt":
        pytest.skip("POSIX run_bash kills the direct child only, as before")
    monkeypatch.setattr(state, "auto_approve", True)
    pid_file = (tmp_path / "child.pid").as_posix()
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open('{pid_file}', 'w').write(str(p.pid)); time.sleep(60)")
    t0 = time.monotonic()
    assert run_bash(_py(code), timeout=6) == "TIMEOUT after 6s"
    assert time.monotonic() - t0 < 20
    child = int(open(pid_file, encoding="utf-8").read())
    deadline = time.monotonic() + 5
    while _pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child)


@on_windows
def test_powershell_kind_via_harness_shell(monkeypatch, shell_kind_env):
    monkeypatch.setattr(state, "auto_approve", True)
    shell_kind_env("powershell")
    assert osinfo.shell_kind() == "powershell"
    out = run_bash("Get-ChildItem -Name | Select-Object -First 200")
    assert "\nexit=0\n" in out and "pyproject.toml" in out
    out = run_bash('Write-Output "héllo 日本"; exit 4')
    assert "\nexit=4\n" in out and "héllo 日本" in out


@on_windows
def test_git_bash_gets_arguments_verbatim(monkeypatch, shell_kind_env):
    shell_kind_env("bash")
    if osinfo.shell_kind() != "bash":
        pytest.skip("Git Bash not installed")
    monkeypatch.setattr(state, "auto_approve", True)
    out = run_bash("printf '[%s]' 'a\\\\b' \"c\\\"d\" '*.toml' \"$MSYSTEM\"")
    assert "[a\\\\b][c\"d][*.toml][MINGW64]" in out


def test_windows_cmdline_always_quotes():
    assert shell.windows_cmdline(["C:\\x\\a.exe", "*.py", 'say "hi"', "tail\\"]) == (
        '"C:\\x\\a.exe" "*.py" "say \\"hi\\"" "tail\\\\"'
    )


def test_decode_output_drops_bom_and_normalises_newlines():
    assert shell.decode_output("\ufeffa\r\nb\rc\n".encode("utf-8")) == "a\nb\nc\n"
    assert shell.decode_output(b"") == ""


@pytest.mark.parametrize("cmd", [
    "format c:", "FORMAT D: /q", "diskpart", "rd /s /q C:\\", "rmdir /s /q c:/",
    "del /s /q C:\\*", "del /f /s /q C:\\*.*", 'Remove-Item -Recurse -Force "C:\\"',
    "Remove-Item -Recurse -Force C:\\Windows", "ri -r -fo $env:SystemRoot",
    "rm -rf /c", "rm -rf /c/", "rm -rf /c/*", "Get-ChildItem C:\\ -Recurse | Remove-Item -Force",
    "Format-Volume -DriveLetter D", "Clear-Disk -Number 0", "vssadmin delete shadows /all",
    "echo hi && rd /s /q D:\\",
])
def test_destructive_windows_commands_are_blocked(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert shell.is_dangerous(cmd)
    assert run_bash(cmd) == "BLOCKED: dangerous command"


@pytest.mark.parametrize("cmd", [
    "del build\\out.txt", "rd /s /q build", "Remove-Item -Recurse -Force .\\dist",
    "rm -rf ./c", "rm -rf node_modules", "Get-Process | Format-List", "dir C:\\Users",
    "git log --format=%h", "echo C:",
])
def test_ordinary_windows_commands_are_not_blocked(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert not shell.is_dangerous(cmd)


def test_windows_read_only_commands_skip_approval(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    for cmd in ("dir", "dir /b src", "type README.md", "where python", "where.exe git",
                "Get-ChildItem | Select-Object -First 3", "Get-Content a.txt | Select-String foo",
                "Get-Location", "Select-String -Path *.py -Pattern TODO", "Test-Path .venv"):
        assert _is_safe_readonly_command(cmd), cmd
    for cmd in ("Get-Content a.txt | Remove-Item", "dir; del x", "type a.txt > b.txt",
                "Get-ChildItem & calc", "Get-Item $(Remove-Item x)", "Remove-Item x"):
        assert not _is_safe_readonly_command(cmd), cmd
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    assert not _is_safe_readonly_command("Get-ChildItem")  # POSIX list unchanged


# ── review regressions: approval shortcut, dangerous paths, process trees ──


@pytest.mark.parametrize("cmd", [
    "dir | ? { Remove-Item foo }", "dir | where-object -FilterScript {New-Item z}",
    "dir | sort-object { ri x }", "Get-Content (Remove-Item foo)", "dir @(ri x)",
    "gci -path {ri x}", "dir | sort /o C:\\x.txt", "cat a.txt | sort -o out.txt",
    "type \\\\host\\share\\x", "Get-Content //host/share/x", "Get-Content -Path:\\\\host\\x",
    "cat a; Remove-Item b", "echo hi > C:\\f", "cat a || Remove-Item b", "echo `whoami`",
    "type %USERPROFILE%\\x", "dir | ForEach-Object { ri $_ }", "Get-Item x | Remove-Item",
    "rg --pre calc x", "rg --pre=calc x", "git diff --output=x.patch", "git branch -D main",
    "git branch new-branch", "git remote add o https://x", "Get-Process | Stop-Process",
    "dir | Select-Object -Property a,b", "Get-ChildItem -OutVariable x", "echo hi\nRemove-Item x",
])
def test_windows_readonly_shortcut_never_approves_writes(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert not _is_safe_readonly_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "rg -n pattern .", "grep -rn foo src", "git --no-pager status -sb", "git log --oneline -n 5",
    "git diff HEAD -- a.py", "git branch -a", "git remote -v", "cat README.md", "head -n 20 a.py",
    "dir | Sort-Object Name", "Get-ChildItem | Select-Object -First 3", "gci | ft Name",
    "Get-Content a.txt | Select-String foo", "Get-Content a.txt | more", "type 'my notes.txt'",
])
def test_windows_readonly_shortcut_still_allows_plain_reads(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert _is_safe_readonly_command(cmd), cmd


def test_posix_readonly_prefix_list_is_unchanged(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    assert _is_safe_readonly_command("cat a; echo b")  # upstream behaviour, left as is
    assert not _is_safe_readonly_command("dir | Sort-Object Name")


def test_ask_approval_can_skip_the_readonly_shortcut(monkeypatch):
    asked = []

    class _Console:
        def print(self, *a, **k):
            pass

        def prompt_shell_approval(self, cmd):
            asked.append(cmd)
            return "n"

    monkeypatch.setattr(state, "auto_approve", False)
    monkeypatch.setattr(shell, "console", _Console())
    assert shell.ask_approval("git status") is None and asked == []
    assert shell.ask_approval("git status", allow_readonly_shortcut=False) == "USER DENIED"
    assert asked == ["git status"]
    monkeypatch.setattr(state, "auto_approve", True)
    assert shell.ask_approval("Remove-Item x", allow_readonly_shortcut=False) is None


@pytest.mark.parametrize("cmd", [
    "Remove-Item -Recurse -Force $HOME", "Remove-Item -Recurse $env:USERPROFILE",
    "Remove-Item $env:USERPROFILE -Recurse -Force", "ri -r -fo ~", "rm -rf ~", "rm -rf ~/",
    "rd /s /q %USERPROFILE%", "rmdir /s /q %HOMEPATH%\\", "Remove-Item -Recurse C:\\Users",
    "Remove-Item -Recurse -Force C:\\Users\\anuja", 'rd /s /q "C:\\Users\\anuja\\"', "del /s /q C:\\Users\\*",
])
def test_recursive_deletes_of_the_profile_are_blocked(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert shell.is_dangerous(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "Remove-Item -Recurse -Force C:\\Users\\anuja\\proj\\build", "rm -rf ~/proj/build",
    "rd /s /q %USERPROFILE%\\proj\\dist", "Remove-Item -Recurse $HOME\\proj\\node_modules",
    "cd ~ && rm -rf build", "Get-ChildItem $HOME", "dir C:\\Users",
])
def test_project_deletes_under_the_profile_are_not_blocked(monkeypatch, cmd):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    assert not shell.is_dangerous(cmd), cmd


def test_process_tree_never_kills_an_exited_process(monkeypatch):
    """No taskkill / kill once the process has exited (its PID may be reused)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    tree = shell.ProcessTree(proc)
    calls = []
    monkeypatch.setattr(shell.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(proc, "kill", lambda: calls.append("kill"))
    tree.kill()
    assert calls == []
    tree.close()
    tree.close()  # idempotent
    tree.kill()   # and safe after close
    assert calls == []


def test_process_tree_close_and_kill_race(monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    tree = shell.ProcessTree(proc)
    threads = [threading.Thread(target=tree.kill), threading.Thread(target=tree.close),
               threading.Thread(target=tree.kill)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    proc.wait(timeout=10)
    assert proc.returncode is not None
