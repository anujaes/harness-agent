"""Windows desktop-control tools (jarvis.tools.windows).

Pure logic (key specs, app matching, role aliases, menu labels, speech rate,
task discovery) is tested against fakes; nothing here types, clicks, focuses or
closes a real window. A few read-only checks touch the live desktop.
"""
import sys
from unittest import mock

import pytest

from jarvis.tools import FUNC, TOOL_GROUPS
from jarvis.tools.router import select_tools

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop tools")


# ── registry + routing (every platform) ─────────────────────────────────────

def test_every_desktop_schema_has_a_handler():
    names = {t["name"] for t in TOOL_GROUPS["desktop"]}
    assert names and names <= set(FUNC)


def test_desktop_group_matches_the_platform():
    names = {t["name"] for t in TOOL_GROUPS["desktop"]}
    if sys.platform == "win32":
        assert {"powershell", "task_run", "system_control"} <= names
        assert not names & {"applescript", "shortcut_run", "mac_control"}
    else:
        assert {"applescript", "shortcut_run", "mac_control"} <= names


def test_state_changing_desktop_tools_run_serially():
    from jarvis.repl.render import _SERIAL_TOOLS

    mutating = {"click_at", "click_element", "click_menu", "key_press", "type_text",
                "launch_app", "focus_app", "quit_app", "clipboard_set", "speck"}
    names = {t["name"] for t in TOOL_GROUPS["desktop"]}
    platform_specific = names & {"powershell", "task_run", "system_control",
                                 "applescript", "shortcut_run", "mac_control"}
    assert (mutating | platform_specific) <= _SERIAL_TOOLS


def _routed(prompt: str) -> set[str]:
    with mock.patch("jarvis.tools.router.skill_count", return_value=0):
        return {t["name"] for t in select_tools([{"role": "user", "content": prompt}])}


@pytest.mark.parametrize("prompt", [
    "click the Save button",
    "launch WhatsApp",
    "use frontmost_app and list_apps",
])
def test_desktop_group_is_routed_for_gui_requests(prompt):
    assert "click_element" in _routed(prompt)


@win_only
@pytest.mark.parametrize("prompt", [
    "open notepad and type hello",
    "mute the volume",
    "turn wi-fi off",
    "switch to dark mode",
    "which app is in front?",
])
def test_windows_vocabulary_routes_the_desktop_group(prompt):
    assert "click_element" in _routed(prompt)


@pytest.mark.parametrize("prompt", [
    "refactor the parser module",
    "wait for the tests to finish",
    "run the server in the foreground",
    "fix the explorer component",
])
def test_desktop_group_stays_off_for_plain_coding(prompt):
    assert "click_element" not in _routed(prompt)


def test_desktop_group_stays_active_mid_tool_loop():
    msgs = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [{"type": "tool_use", "name": "read_ui", "input": {}}]},
    ]
    with mock.patch("jarvis.tools.router.skill_count", return_value=0):
        names = {t["name"] for t in select_tools(msgs)}
    assert "click_element" in names


def test_system_prompt_names_the_host_os():
    from jarvis.constants.system_prompt import build_base_system

    prompt = build_base_system()
    if sys.platform == "win32":
        assert "Windows agent" in prompt and "system_control" in prompt
        assert "macOS" not in prompt and "applescript" not in prompt
    else:
        assert "macOS agent" in prompt


# ── app matching ────────────────────────────────────────────────────────────

def _win(title, exe, desc="", hwnd=1):
    from jarvis.tools.windows import _win32

    info = _win32.WindowInfo(hwnd=hwnd, title=title, pid=hwnd, exe_path=exe, rect=(0, 0, 100, 100))
    return info, desc


@pytest.fixture()
def fake_windows(monkeypatch):
    from jarvis.tools.windows import _win32

    rows = [
        _win("main.py - proj - Visual Studio Code", r"C:\Apps\Microsoft VS Code\Code.exe", "Visual Studio Code", 1),
        _win("Inbox - Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Google Chrome", 2),
        _win("notes.txt - Notepad", r"C:\WindowsApps\Notepad\Notepad.exe", "Notepad.exe", 3),
        _win("Calculator", r"C:\WindowsApps\CalculatorApp.exe", "", 4),
    ]
    descs = {info.exe_path: desc for info, desc in rows}
    monkeypatch.setattr(_win32, "file_description", lambda path: descs.get(path, ""))
    return [info for info, _ in rows]


@win_only
@pytest.mark.parametrize("query, expected", [
    ("code", 1), ("Visual Studio Code", 1), ("vscode", 1), ("vs code", 1),
    ("chrome", 2), ("Google Chrome", 2), ("chrome.exe", 2),
    ("notepad", 3), ("Notepad", 3),
    ("calculator", 4),
    ("inbox", 2),  # window title
])
def test_match_windows(fake_windows, query, expected):
    from jarvis.tools.windows import _win32

    hits = _win32.match_windows(query, fake_windows)
    assert hits and hits[0].hwnd == expected


@win_only
def test_match_windows_prefers_exact_names_and_ignores_unknown(fake_windows):
    from jarvis.tools.windows import _win32

    assert _win32.match_windows("slack", fake_windows) == []
    assert _win32.match_windows("", fake_windows) == []


@win_only
def test_app_name_uses_product_name_without_exe_suffix(fake_windows):
    by_hwnd = {w.hwnd: w for w in fake_windows}
    assert by_hwnd[1].app_name == "Visual Studio Code"
    assert by_hwnd[3].app_name == "Notepad"
    assert by_hwnd[4].app_name == "CalculatorApp"


# ── keyboard ────────────────────────────────────────────────────────────────

@pytest.fixture()
def recorded_chords(monkeypatch):
    from jarvis.tools.windows import _win32

    calls = []
    monkeypatch.setattr(_win32, "press_chord", lambda mods, vk: calls.append((list(mods), vk)) or 2)
    monkeypatch.setattr(_win32, "type_unicode", lambda text: calls.append(("text", text)) or 2)
    return calls


@win_only
@pytest.mark.parametrize("spec, mods, vk", [
    ("enter", [], 0x0D),
    ("ctrl+s", [0x11], None),
    ("cmd+shift+t", [0x11, 0x10], None),  # cmd is ctrl on Windows
    ("alt+f4", [0x12], 0x73),
    ("win+d", [0x5B], None),
    ("down", [], 0x28),
    ("delete", [], 0x2E),
    ("backspace", [], 0x08),
    ("page_down", [], 0x22),
])
def test_key_press_parses_chords(recorded_chords, spec, mods, vk):
    from jarvis.tools.windows.input import key_press

    assert key_press(spec) == "OK"
    got_mods, got_vk = recorded_chords[-1]
    assert got_mods[:len(mods)] == mods
    if vk is not None:
        assert got_vk == vk


@win_only
def test_key_press_rejects_bad_specs(recorded_chords):
    from jarvis.tools.windows.input import key_press

    assert key_press("").startswith("ERROR")
    assert key_press("ctrl+shift").startswith("ERROR")
    assert key_press("ctrl+nosuchkey").startswith("ERROR")
    assert recorded_chords == []


@win_only
def test_long_text_is_pasted_and_short_text_typed(monkeypatch, recorded_chords):
    from jarvis.tools.windows import input as win_input

    monkeypatch.setattr(win_input, "_paste", lambda text: True)
    assert win_input.type_text("x" * (win_input.PASTE_THRESHOLD + 1)) == "OK (pasted)"
    assert win_input.type_text("hello") == "OK"
    assert recorded_chords == [("text", "hello")]


@win_only
def test_paste_never_replaces_non_text_clipboard(monkeypatch):
    from jarvis.tools.windows import input as win_input
    from jarvis.utils import win_clipboard

    monkeypatch.setattr(win_clipboard, "holds_non_text", lambda: True)
    monkeypatch.setattr(win_clipboard, "set_text", mock.Mock(side_effect=AssertionError("must not write")))
    assert win_input._paste("y" * 500) is False


@win_only
def test_unicode_events_cover_surrogate_pairs():
    from jarvis.tools.windows import _win32

    events = _win32._char_events("🚀")
    assert len(events) == 4  # two UTF-16 code units, down + up each
    assert all(e.ki.dwFlags & _win32.KEYEVENTF_UNICODE for e in events)
    assert _win32._char_events("\n")[0].ki.wVk == _win32.VK_RETURN


@win_only
def test_input_struct_matches_the_win32_abi():
    import ctypes

    from jarvis.tools.windows import _win32

    assert ctypes.sizeof(_win32.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


@win_only
def test_click_at_refuses_off_screen(monkeypatch):
    from jarvis.tools.windows import _win32, input as win_input

    monkeypatch.setattr(_win32, "virtual_screen", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(_win32, "click", mock.Mock(side_effect=AssertionError("must not click")))
    assert win_input.click_at(5000, 10).startswith("ERROR")


# ── UI tree helpers ─────────────────────────────────────────────────────────

class _Ctrl:
    def __init__(self, control_type):
        self.ControlTypeName = control_type


@win_only
@pytest.mark.parametrize("role, control_type, ok", [
    ("button", "ButtonControl", True),
    ("edit", "DocumentControl", True),   # Windows 11 Notepad's editor
    ("textfield", "EditControl", True),
    ("row", "ListItemControl", True),
    ("link", "HyperlinkControl", True),
    ("button", "EditControl", False),
    ("", "AnythingControl", True),
])
def test_role_filter_understands_mac_and_uia_names(role, control_type, ok):
    from jarvis.tools.windows.ui import _role_matches

    assert _role_matches(_Ctrl(control_type), role) is ok


@win_only
@pytest.mark.parametrize("raw, norm", [
    ("&File", "file"), ("Save &As...", "save as"), ("Time/Date\tF5", "time/date"), ("Open…", "open"),
])
def test_menu_labels_are_normalised(raw, norm):
    from jarvis.tools.windows.input import _menu_label

    assert _menu_label(raw) == norm


# ── system tools ────────────────────────────────────────────────────────────

@win_only
@pytest.mark.parametrize("wpm, sapi", [(0, 0), (180, 0), (360, 10), (90, -5), (1000, 10), (1, -10)])
def test_speech_rate_maps_words_per_minute(wpm, sapi):
    from jarvis.tools.windows.system import _sapi_rate

    assert _sapi_rate(wpm) == sapi


@win_only
def test_speck_validates_before_speaking(monkeypatch):
    from jarvis.tools.windows import system

    monkeypatch.setattr(system, "run_ps", mock.Mock(side_effect=AssertionError("must not speak")))
    assert system.speck("").startswith("ERROR")
    assert system.speck("x" * 10_000).startswith("ERROR")


@win_only
def test_task_run_runs_a_script_from_the_tasks_folder(tmp_path, monkeypatch):
    from jarvis import state
    from jarvis.tools.windows import system

    monkeypatch.setattr(state, "auto_approve", True)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "greet.ps1").write_text(
        '$in = [Console]::In.ReadToEnd(); "hi $in / $env:JARVIS_INPUT"', encoding="utf-8")
    monkeypatch.setattr(system, "_task_dirs", lambda: [tasks])
    assert system.task_run("greet", "Ada").strip() == "hi Ada / Ada"
    assert "greet" in system.task_run("?")


@win_only
def test_task_run_asks_before_running(tmp_path, monkeypatch):
    from jarvis.tools.windows import system

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "greet.ps1").write_text('"ran"', encoding="utf-8")
    monkeypatch.setattr(system, "_task_dirs", lambda: [tasks])
    monkeypatch.setattr(system, "approve", lambda what: "USER DENIED")
    monkeypatch.setattr(system, "run_argv", mock.Mock(side_effect=AssertionError("must not run")))
    assert system.task_run("greet") == "USER DENIED"


@win_only
@pytest.mark.parametrize("name", [r"..\evil", r"C:\Temp\evil.ps1", "sub/evil", ".."])
def test_task_run_only_resolves_names_inside_the_tasks_folders(tmp_path, monkeypatch, name):
    from jarvis.tools.windows import system

    (tmp_path / "evil.ps1").write_text('"pwned"', encoding="utf-8")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    monkeypatch.setattr(system, "_task_dirs", lambda: [tasks])
    assert system._find_script(name) is None


@win_only
def test_task_run_reports_unknown_tasks(tmp_path, monkeypatch):
    from jarvis import state
    from jarvis.tools.windows import system

    monkeypatch.setattr(state, "auto_approve", True)
    monkeypatch.setattr(system, "_task_dirs", lambda: [tmp_path])
    assert system.task_run("definitely-not-a-task-xyz").startswith("ERROR")


@win_only
def test_system_control_rejects_unknown_actions_and_bad_values():
    from jarvis.tools.windows.system import system_control

    assert system_control("explode").startswith("ERROR: unknown action")
    assert system_control("volume", "loud").startswith("ERROR: bad value")


@win_only
def test_open_url_requires_a_target():
    from jarvis.tools.windows.system import open_url

    assert open_url("").startswith("ERROR")


@win_only
def test_powershell_tool_asks_for_approval(monkeypatch):
    import importlib

    from jarvis import state

    ps_mod = importlib.import_module("jarvis.tools.windows.powershell")  # the package re-exports the function
    monkeypatch.setattr(state, "auto_approve", False)
    with mock.patch("jarvis.tools.shell.ask_approval", return_value="USER DENIED") as ask, \
         mock.patch.object(ps_mod, "ps", side_effect=AssertionError("must not run")):
        assert ps_mod.powershell("Get-Date") == "USER DENIED"
    ask.assert_called_once()


@win_only
def test_powershell_round_trips_unicode(monkeypatch):
    from jarvis import state
    from jarvis.tools.windows.powershell import powershell

    monkeypatch.setattr(state, "auto_approve", True)
    assert powershell("'héllo — 日本'") == "héllo — 日本"
    assert powershell("exit 3").startswith("ERROR (exit 3)")


@win_only
@pytest.mark.parametrize("code, exit_code, message", [
    ("throw 'bad'", 1, "bad"),
    ("Write-Error 'boom'; 'after'", 1, "boom"),
    ("Get-Item C:\\definitely\\missing\\x", 1, "Cannot find path"),
    ("cmd /c exit 4", 4, ""),
])
def test_powershell_tool_reports_errors_as_plain_text(monkeypatch, code, exit_code, message):
    from jarvis import state
    from jarvis.tools.windows.powershell import powershell

    monkeypatch.setattr(state, "auto_approve", True)
    out = powershell(code)
    assert out.startswith(f"ERROR (exit {exit_code})")
    assert message in out and "CLIXML" not in out


@win_only
@pytest.mark.parametrize("code, expected", [
    ('cmd /c "echo warn 1>&2 & exit 0"; \'done\'', "done"),     # native stderr ≠ failure
    ("Write-Warning 'careful'; 'x'", "WARNING: careful"),
    ("using namespace System.Text\n[StringBuilder]::new('sb ok').ToString()", "sb ok"),
    ('param($a = 5) "a=$a"', "a=5"),
    ("begin { 'b' } process { 'p' } end { 'e' }", "b\np\ne"),
])
def test_powershell_tool_handles_script_shapes(monkeypatch, code, expected):
    from jarvis import state
    from jarvis.tools.windows.powershell import powershell

    monkeypatch.setattr(state, "auto_approve", True)
    out = powershell(code)
    assert not out.startswith("ERROR") and expected in out


@win_only
def test_powershell_tool_reports_parse_errors_as_text(monkeypatch):
    from jarvis import state
    from jarvis.tools.windows.powershell import powershell

    monkeypatch.setattr(state, "auto_approve", True)
    out = powershell("<# unterminated")
    assert out.startswith("ERROR (exit 1)") and "CLIXML" not in out


@win_only
def test_powershell_tool_never_takes_the_read_only_shortcut(monkeypatch):
    """`cat`/`echo`/`dir` are PowerShell aliases — a "read-only looking" script still asks."""
    import importlib

    from jarvis import state

    ps_mod = importlib.import_module("jarvis.tools.windows.powershell")
    monkeypatch.setattr(state, "auto_approve", False)
    with mock.patch("jarvis.tools.shell.ask_approval", return_value="USER DENIED") as ask:
        assert ps_mod.powershell("echo hi; Remove-Item x") == "USER DENIED"
    assert ask.call_args.kwargs.get("allow_readonly_shortcut") is False


@win_only
@pytest.mark.parametrize("value", ["plain", "it's", "smart ‘quotes’ ‚low‛", "$env:PATH `n", ""])
def test_ps_quote_survives_any_quote_character(value):
    from jarvis.tools.windows.powershell import ps, ps_quote

    assert ps(f"Write-Output {ps_quote(value)}").replace("OK", "") == value


@win_only
def test_long_scripts_go_through_a_temp_file():
    from jarvis.tools.windows.powershell import _MAX_ENCODED_SCRIPT, ps

    script = "$x = 1\n" + ("# padding\n" * (_MAX_ENCODED_SCRIPT // 10 + 10)) + "'long ok'"
    assert ps(script) == "long ok"


# ── known folders (OneDrive-relocated Desktop / Documents …) ────────────────

def _fake_known_folders(monkeypatch, tmp_path):
    from jarvis.utils import osinfo

    home = tmp_path / "home"
    real_desktop = home / "OneDrive" / "Desktop"
    (real_desktop / "notes").mkdir(parents=True)
    (real_desktop / "notes" / "todo.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(osinfo, "known_folders", lambda: {"Desktop": real_desktop, "Downloads": home / "Downloads"})
    monkeypatch.setattr(osinfo.Path, "home", classmethod(lambda cls: home))
    return home, real_desktop


def test_home_desktop_paths_follow_a_relocated_desktop(monkeypatch, tmp_path):
    from jarvis.utils.osinfo import redirect_known_folder

    home, real = _fake_known_folders(monkeypatch, tmp_path)
    assert redirect_known_folder(home / "Desktop") == real
    assert redirect_known_folder(home / "desktop" / "notes" / "todo.txt") == real / "notes" / "todo.txt"
    # not relocated / not a known folder / outside home → unchanged
    assert redirect_known_folder(home / "Downloads" / "a.zip") == home / "Downloads" / "a.zip"
    assert redirect_known_folder(home / "Projects") == home / "Projects"
    assert redirect_known_folder(tmp_path / "elsewhere" / "Desktop") == tmp_path / "elsewhere" / "Desktop"


def test_robust_resolve_finds_files_on_a_relocated_desktop(monkeypatch, tmp_path):
    import jarvis.path_resolve as path_resolve

    home, real = _fake_known_folders(monkeypatch, tmp_path)
    monkeypatch.setattr(path_resolve, "redirect_known_folder",
                        __import__("jarvis.utils.osinfo", fromlist=["x"]).redirect_known_folder)
    got = path_resolve.robust_resolve(str(home / "Desktop" / "notes" / "todo.txt"))
    assert got == (real / "notes" / "todo.txt").resolve()


def test_known_folders_are_empty_off_windows():
    from jarvis.utils import osinfo

    if sys.platform != "win32":
        assert osinfo.known_folders() == {}


@win_only
def test_known_folders_match_the_shell_on_this_machine():
    import os

    from jarvis.utils.osinfo import known_folders

    # The suite runs with a scratch USERPROFILE (conftest), so folders that
    # would live under it (Downloads …) may not resolve here; the Desktop does.
    folders = known_folders()
    assert "Desktop" in folders
    assert all(os.path.isabs(p) for p in folders.values())


# ── process hygiene ─────────────────────────────────────────────────────────

@win_only
def test_current_directory_is_never_searched_for_programs():
    """A repo shipping its own rg.exe / git.exe must not get it run by our tools."""
    import os

    import jarvis.utils.osinfo  # noqa: F401 — sets the switch at import

    assert os.environ.get("NoDefaultCurrentDirectoryInExePath") == "1"


@win_only
@pytest.mark.parametrize("cmd", [r"type /\host\s\f", r"type \/host/s/f", r"gc -Path:/\host\s", r"type \\host\s\f"])
def test_unc_paths_with_any_separators_need_approval(cmd):
    from jarvis.tools.shell import _is_safe_readonly_command

    assert _is_safe_readonly_command(cmd) is False


# ── read-only live checks ───────────────────────────────────────────────────

@win_only
def test_live_window_listing_and_battery_are_read_only_safe():
    from jarvis.tools.windows import _win32
    from jarvis.tools.windows.system import system_control

    wins = _win32.list_windows()
    assert all(w.hwnd and isinstance(w.title, str) for w in wins)
    out = system_control("battery")
    assert out.startswith(("Battery", "No battery"))


@win_only
def test_live_check_permissions_reports_ui_automation():
    from jarvis.tools.windows.ui import check_permissions

    assert "UI Automation" in check_permissions()
