"""screenshot tool: capture modes, image plumbing into tool results, provider
conversion and context pruning."""
import base64
import importlib
import os
import pathlib
import shutil
import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

from jarvis import state

# ``jarvis.tools.screenshot`` is the tool function; this is its module.
shot = importlib.import_module("jarvis.tools.screenshot")
_REAL_SCREEN_RECORDING_ALLOWED = shot.screen_recording_allowed  # before the autouse patch
from jarvis.utils.tool_images import (
    image_marker, split_image_markers, split_tool_result, tool_result_content,
)


def _png(path: pathlib.Path, w: int, h: int) -> pathlib.Path:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x40\x80\xc0" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


@pytest.fixture(autouse=True)
def _shot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(shot, "SHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(state, "MODEL", "claude-opus-5-5")  # has vision
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: True)


def test_image_size_reads_png_and_gif_headers(tmp_path):
    assert shot.image_size(_png(tmp_path / "a.png", 37, 21)) == (37, 21)
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a" + struct.pack("<HH", 640, 480) + b"\x00" * 20)
    assert shot.image_size(gif) == (640, 480)


def test_marker_becomes_native_image_block(tmp_path):
    img = _png(tmp_path / "x.png", 4, 3)
    out = f"Screenshot of x — 4×3 px\nsaved: {img}\n{image_marker(img)}"
    text, paths = split_image_markers(out)
    assert paths == [str(img)] and "jarvis:image" not in text
    content = tool_result_content(out)
    assert [b["type"] for b in content] == ["text", "image"]
    assert base64.b64decode(content[1]["source"]["data"]) == img.read_bytes()
    assert content[1]["source"]["media_type"] == "image/png"
    assert tool_result_content("plain output") == "plain output"
    # A missing file degrades to a text note instead of a broken block.
    gone = tool_result_content(image_marker(tmp_path / "nope.png"))
    assert gone[0]["type"] == "text" and "could not be attached" in gone[0]["text"]


def test_path_mode_attaches_the_file(tmp_path):
    img = _png(tmp_path / "ui.png", 800, 600)
    out = shot.screenshot(path=str(img))
    assert out.startswith("Screenshot of image ui.png — 800×600 px")
    assert image_marker(img) in out  # small + native → sent as-is, not copied
    assert shot.screenshot(path=str(tmp_path / "missing.png")).startswith("ERROR")
    (tmp_path / "notes.txt").write_text("x")
    assert "not an image" in shot.screenshot(path=str(tmp_path / "notes.txt"))


def test_models_without_vision_get_ocr_text(tmp_path, monkeypatch):
    import jarvis.tools.ocr as ocr

    monkeypatch.setattr(state, "MODEL", "mimo-v2.5-free")
    monkeypatch.setattr(ocr, "read_image_text", lambda p: "Sign in\nForgot password?")
    out = shot.screenshot(path=str(_png(tmp_path / "ui.png", 10, 10)))
    assert "can't view images" in out and "Forgot password?" in out
    assert "jarvis:image" not in out


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sips"), reason="needs macOS sips")
def test_screen_capture_is_scaled_and_maps_to_click_coords(tmp_path, monkeypatch):
    monkeypatch.setattr(shot, "_run_jxa", lambda script, *a, **k: {
        "screen": [1728, 1117], "scale": 2,
        "windows": [{"id": 5, "owner": "Safari", "name": "Docs", "x": 10, "y": 40, "w": 1200, "h": 800}],
    })

    def fake_capture(args, out):
        _png(out, 3456, 2234)  # a Retina main display
        return None

    monkeypatch.setattr(shot, "_screencapture", fake_capture)
    out = shot.screenshot()
    first = out.splitlines()[0]
    assert first.startswith("Screenshot of the main display — 1568×")
    assert "px_x × 1.102" in out  # 1728 pt / 1568 px
    saved = pathlib.Path(out.split("saved: ", 1)[1].splitlines()[0])
    assert saved.exists() and max(shot.image_size(saved)) == 1568
    assert len(list((tmp_path / "shots").glob("shot-*"))) == 1  # the raw capture was replaced


def test_app_window_lookup_and_permission_errors(monkeypatch):
    monkeypatch.setattr(shot.sys, "platform", "darwin")
    windows = [
        {"id": 1, "owner": "Google Chrome", "name": "Docs", "x": 0, "y": 25, "w": 1400, "h": 900},
        {"id": 2, "owner": "Safari", "name": "", "x": 0, "y": 25, "w": 30, "h": 30},  # too small
        {"id": 3, "owner": "Safari", "name": "Apple", "x": 100, "y": 60, "w": 1000, "h": 700},
    ]
    assert [w["id"] for w in shot._match_windows(windows, "safari")] == [3]
    assert [w["id"] for w in shot._match_windows(windows, "chrome")] == [1]

    monkeypatch.setattr(shot, "_run_jxa", lambda *a, **k: {"screen": [1440, 900], "windows": windows})
    err = shot.screenshot(app="Figma")
    assert err.startswith("ERROR: no window found for app 'Figma'") and "Safari" in err

    calls = []

    class R:
        returncode, stdout, stderr = 1, "", "could not create image from window"

    monkeypatch.setattr(shot.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    err = shot.screenshot(app="Safari")
    assert "Screen Recording" in err
    assert calls[-1][:5] == ["/usr/sbin/screencapture", "-x", "-o", "-l", "3"]


def test_url_mode_normalizes_and_clamps(tmp_path, monkeypatch):
    seen = {}

    def fake_chrome(url, out, w, h, settle_ms):
        seen.update(url=url, w=w, h=h, settle=settle_ms)
        _png(out, w, h)
        return None

    monkeypatch.setattr(shot, "_chrome_capture", fake_chrome)
    out = shot.screenshot(url="localhost:5173", width=99999, height=10)
    assert seen == {"url": "http://localhost:5173", "w": 3840, "h": 240, "settle": 3000}
    assert "jarvis:image" in out
    page = tmp_path / "index.html"
    page.write_text("<h1>hi</h1>")
    assert shot.normalize_url(str(page)).startswith("file://")


def _use_home(monkeypatch, home: pathlib.Path) -> None:
    """Point the home directory at ``home``: HOME on POSIX; on Windows also
    USERPROFILE (what Path.home() reads) and LOCALAPPDATA (Playwright's cache)."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))


def _fake_exe(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/Linux cache layout; Windows twin: test_find_headless_shell_in_windows_playwright_and_puppeteer_caches")
def test_find_chrome_prefers_headless_shell(tmp_path, monkeypatch):
    # Full Chrome leaves a Dock icon per headless run on macOS; the shell doesn't.
    _use_home(monkeypatch, tmp_path)
    monkeypatch.delenv("HARNESS_CHROME", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(shot.shutil, "which", lambda name: None)
    cache = tmp_path / "Library" / "Caches" / "ms-playwright"
    _fake_exe(cache / "chromium_headless_shell-999" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    newest = _fake_exe(cache / "chromium_headless_shell-1217" / "chrome-headless-shell-mac-arm64"
                       / "chrome-headless-shell")
    assert shot.find_chrome() == str(newest)

    chrome = _fake_exe(tmp_path / "my-chrome")
    monkeypatch.setenv("HARNESS_CHROME", str(chrome))
    assert shot.find_chrome() == str(chrome)  # an explicit choice still wins


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/Linux cache layout; Windows twin: test_find_headless_shell_in_windows_playwright_and_puppeteer_caches")
def test_find_headless_shell_in_playwright_browsers_path_and_puppeteer(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(shot.shutil, "which", lambda name: None)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert shot.find_headless_shell() is None
    pup = _fake_exe(tmp_path / ".cache" / "puppeteer" / "chrome-headless-shell" / "mac_arm-131.0.6778.85"
                    / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    assert shot.find_headless_shell() == str(pup)
    custom = tmp_path / "browsers"
    old = _fake_exe(custom / "chromium_headless_shell-1148" / "chrome-linux" / "headless_shell")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(custom))
    assert shot.find_headless_shell() == str(old)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows browser-cache layout")
def test_find_headless_shell_in_windows_playwright_and_puppeteer_caches(tmp_path, monkeypatch):
    """On Windows Playwright keeps browsers in %LOCALAPPDATA%\\ms-playwright and
    every binary ends in .exe; the newest shell wins over full Chrome."""
    _use_home(monkeypatch, tmp_path)
    monkeypatch.setattr(shot.shutil, "which", lambda name: None)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("HARNESS_CHROME", raising=False)
    assert shot.find_headless_shell() is None
    pup = _fake_exe(tmp_path / ".cache" / "puppeteer" / "chrome-headless-shell" / "win64-131.0.6778.85"
                    / "chrome-headless-shell-win64" / "chrome-headless-shell.exe")
    assert shot.find_headless_shell() == str(pup)
    cache = tmp_path / "AppData" / "Local" / "ms-playwright"
    _fake_exe(cache / "chromium_headless_shell-1148" / "chrome-win" / "headless_shell.exe")
    newest = _fake_exe(cache / "chromium_headless_shell-1217" / "chrome-headless-shell-win64"
                       / "chrome-headless-shell.exe")
    assert shot.find_headless_shell() == str(newest)
    assert shot.find_chrome() == str(newest)


@pytest.mark.skipif(not shot.find_chrome(), reason="needs Chrome/Chromium")
def test_real_headless_chrome_render(tmp_path, monkeypatch):
    # Look for chrome-headless-shell in the real browser caches (read only), so a
    # test run doesn't leave a Chrome icon in the macOS Dock.
    real_home = os.environ.get("JARVIS_REAL_HOME")
    if real_home:
        _use_home(monkeypatch, pathlib.Path(real_home))
    page = tmp_path / "p.html"
    page.write_text("<body style='background:#123'><h1 style='color:white'>Hello</h1></body>")
    out = shot.screenshot(url=str(page), width=640, height=400)
    assert "— 640×400 px" in out.splitlines()[0], out


# ── providers ─────────────────────────────────────────────────────────────


def _conversation(tmp_path):
    img = _png(tmp_path / "s.png", 2, 2)
    return [
        {"role": "user", "content": "how does the page look?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "screenshot", "input": {"url": "localhost:3000"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": tool_result_content(f"Screenshot — 2×2 px\n{image_marker(img)}")},
        ]},
    ]


def test_openai_style_providers_get_image_after_tool_message(tmp_path):
    from jarvis.auth.opencode_client import _anthropic_messages_to_openai

    out = _anthropic_messages_to_openai(_conversation(tmp_path))
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool", "user"]  # tool msg right after tool_calls
    assert out[2]["content"] == "Screenshot — 2×2 px"
    parts = out[3]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_codex_responses_input_carries_the_image(tmp_path):
    from jarvis.auth.codex_client import _anthropic_messages_to_responses_input

    items = _anthropic_messages_to_responses_input(_conversation(tmp_path))
    out_item = next(i for i in items if i.get("type") == "function_call_output")
    assert out_item["output"] == "Screenshot — 2×2 px"
    img_msg = items[-1]
    assert img_msg["role"] == "user"
    assert img_msg["content"][1]["type"] == "input_image"


def test_old_screenshots_are_pruned_from_requests(tmp_path):
    from jarvis.repl.trim import _content_chars, prune_tool_images

    msgs = []
    for i in range(5):
        msgs += _conversation(tmp_path)[1:]
        msgs[-2]["content"][0]["id"] = f"t{i}"
        msgs[-1]["content"][0]["tool_use_id"] = f"t{i}"

    def images(ms):
        return sum(len(split_tool_result(m["content"][0]["content"])[1])
                   for m in ms if m["role"] == "user")

    kept = prune_tool_images(msgs, vision=True)
    assert images(kept) == 3 and images(msgs) == 5  # input untouched
    assert "earlier screenshot removed" in split_tool_result(kept[1]["content"][0]["content"])[0]
    assert images(prune_tool_images(msgs, vision=False)) == 0
    # Budgeting counts an image as a fixed estimate, never its base64 length.
    assert _content_chars(msgs[1]["content"]) < 9_000


def test_tool_loop_puts_the_image_into_the_conversation(tmp_path, monkeypatch):
    """A screenshot tool_use goes through render_assistant and lands in
    state.messages as a tool_result holding text + a native image block."""
    from types import SimpleNamespace

    from jarvis.repl import render

    img = _png(tmp_path / "page.png", 64, 48)
    monkeypatch.setattr(state, "messages", [
        {"role": "user", "content": "check the page"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "s1", "name": "screenshot",
                                           "input": {"path": str(img)}}]},
    ])
    monkeypatch.setattr(state, "_assistant_stream_ui_active", False, raising=False)
    resp = SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", id="s1", name="screenshot", input={"path": str(img)}),
    ])
    assert render.render_assistant(resp) is True
    result = state.messages[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "s1"
    text, images = split_tool_result(result["content"])
    assert text.startswith("Screenshot of image page.png — 64×48 px")
    assert "jarvis:image" not in text
    assert len(images) == 1 and images[0]["source"]["media_type"] == "image/png"


def test_missing_permission_asks_macos_once_and_names_the_terminal(monkeypatch):
    asked = []
    monkeypatch.setattr(shot.sys, "platform", "darwin")
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: False)
    monkeypatch.setattr(shot, "request_screen_recording", lambda: asked.append(1))
    monkeypatch.setenv("__CFBundleIdentifier", "com.googlecode.iterm2")
    monkeypatch.setattr(shot, "_run_jxa", lambda *a, **k: pytest.fail("must not capture"))

    first = shot.screenshot(app="Finder")
    assert first.startswith("ERROR: macOS blocked the capture — Screen Recording permission is off for iTerm")
    assert "opened System Settings" in first and "quit and reopen iTerm" in first
    second = shot.screenshot()
    assert "Tell the user to open System Settings" in second
    assert asked == [1]  # the prompt / Settings pane opens once, not on every call
    # url/path captures don't need the permission.
    monkeypatch.setattr(shot, "_chrome_capture", lambda url, out, w, h, ms: _png(out, w, h) and None)
    assert "jarvis:image" in shot.screenshot(url="localhost:3000")


def test_terminal_name_fallbacks(monkeypatch):
    # The Windows parent-process lookup would name the real host; test the env fallbacks.
    monkeypatch.setattr(shot, "_windows_terminal_name", lambda: None)
    monkeypatch.setenv("__CFBundleIdentifier", "com.apple.Terminal")
    assert shot.terminal_app_name() == "Terminal"
    monkeypatch.delenv("__CFBundleIdentifier")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    assert shot.terminal_app_name() == "Ghostty"
    monkeypatch.setenv("TERM_PROGRAM", "something-else")
    assert shot.terminal_app_name() == "your terminal app"


def test_check_permissions_reports_screen_recording(monkeypatch):
    from jarvis.tools.mac import ui as mac_ui

    class R:
        returncode, stdout, stderr = 0, "Finder\n", ""

    monkeypatch.setattr(mac_ui.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setenv("__CFBundleIdentifier", "com.apple.Terminal")
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: False)
    out = mac_ui.check_permissions()
    assert "Accessibility OK" in out and "SCREEN RECORDING OFF for Terminal" in out
    monkeypatch.setattr(shot, "screen_recording_allowed", lambda: True)
    assert "Screen Recording OK (Terminal)" in mac_ui.check_permissions()


# ── Windows ───────────────────────────────────────────────────────────────
# The capture layer (``tools/windows/capture.py``) is swapped for a fake so the
# screenshot logic — virtual-desktop offsets, errors, minimized fallback — is
# tested on every OS. Window matching itself (``_win32.match_windows``) and the
# real screen / OCR checks at the bottom run on Windows only.


def _win(hwnd, app_name, title, x, y, w, h, minimized=False):
    """A ``WindowInfo``-shaped window for the fake capture layer."""
    return SimpleNamespace(hwnd=hwnd, app_name=app_name, title=title, minimized=minimized,
                           rect=(x, y, x + w, y + h))


class _FakeWinCapture:
    """Stand-in for ``jarvis.tools.windows.capture`` (two monitors, one left of the primary)."""

    def __init__(self, windows=(), minimized=()):
        from PIL import Image

        self._image = Image
        self.windows = list(windows)
        self.minimized = list(minimized)
        self.grabs = []
        self.captured = []

    def ensure_dpi_aware(self):
        pass

    def virtual_screen(self):
        return -1920, 0, 3840, 1080

    def monitor_count(self):
        return 2

    def grab_screen(self, bbox=None):
        self.grabs.append(bbox)
        if bbox is None:
            return self._image.new("RGB", (3840, 1080), "navy")
        return self._image.new("RGB", (bbox[2] - bbox[0], bbox[3] - bbox[1]), "navy")

    def _match(self, app, wins):
        q = app.lower()
        return [w for w in wins if q in w.app_name.lower() or q in w.title.lower()]

    def find_windows(self, app):
        return self._match(app, self.windows) or self._match(app, self.minimized)

    def app_names(self):
        return sorted({w.app_name for w in self.windows})

    def capture_window(self, win):
        self.captured.append(win.hwnd)
        left, top, right, bottom = win.rect
        img = self._image.new("RGB", (right - left, bottom - top), "white")
        return img, {"x": left, "y": top, "w": right - left, "h": bottom - top}, ""

    def ancestor_exes(self):
        return []


_WIN_WINDOWS = [
    _win(11, "Visual Studio Code", "app.py - proj - Visual Studio Code", -1900, 10, 1800, 1000),
    _win(12, "Google Chrome", "Docs - Google Chrome", 100, 50, 1200, 800),
]


@pytest.fixture()
def fake_win(monkeypatch):
    pytest.importorskip("PIL")
    import types

    import jarvis.tools as tools_pkg

    fake = _FakeWinCapture(_WIN_WINDOWS)
    try:
        import jarvis.tools.windows as win_pkg
    except Exception:  # not Windows: the real package can't import
        win_pkg = types.ModuleType("jarvis.tools.windows")
        monkeypatch.setitem(sys.modules, "jarvis.tools.windows", win_pkg)
        monkeypatch.setattr(tools_pkg, "windows", win_pkg, raising=False)
    monkeypatch.setitem(sys.modules, "jarvis.tools.windows.capture", fake)
    monkeypatch.setattr(win_pkg, "capture", fake, raising=False)
    monkeypatch.setattr(shot.sys, "platform", "win32")
    return fake


def test_windows_full_screen_spans_every_monitor(fake_win):
    out = shot.screenshot()
    assert out.splitlines()[0] == "Screenshot of all 2 displays — 1568×441 px"
    assert fake_win.grabs == [None]
    # 3840 px wide virtual desktop starting at x=-1920, shown at 1568 px.
    assert "click_at(x=-1920 + px_x × 2.449, y=px_y × 2.449)" in out
    assert "jarvis:image" in out


def test_windows_region_uses_screen_pixels(fake_win):
    out = shot.screenshot(region={"x": 10, "y": 20, "width": 400, "height": 300})
    assert fake_win.grabs == [(10, 20, 410, 320)]
    assert out.startswith("Screenshot of screen region 400×300 at (10, 20) — 400×300 px")
    assert "click_at(x=10 + px_x × 1.000, y=20 + px_y × 1.000)" in out
    assert "screen pixels" in shot.screenshot(region="not json")
    assert "positive" in shot.screenshot(region={"x": 0, "y": 0, "width": 0, "height": 5})


def test_windows_region_at_origin_says_pixels_map_directly(fake_win):
    out = shot.screenshot(region={"x": 0, "y": 0, "width": 200, "height": 100})
    assert "Image pixels = screen pixels (use them directly with click_at)." in out


def test_windows_app_capture_offsets_and_errors(fake_win):
    out = shot.screenshot(app="visual studio")
    assert fake_win.captured == [11]
    assert out.startswith("Screenshot of Visual Studio Code window “app.py - proj - Visual Studio Code” — 1568×871 px")
    assert "click_at(x=-1900 + px_x × 1.148, y=10 + px_y × 1.148)" in out
    err = shot.screenshot(app="figma")
    assert err.startswith("ERROR: no window found for app 'figma'")
    assert "Google Chrome, Visual Studio Code" in err


def test_windows_app_capture_falls_back_to_minimized_windows(fake_win):
    fake_win.minimized = [_win(21, "Spotify", "Spotify Premium", 0, 0, 900, 600, minimized=True)]
    assert "Spotify window" in shot.screenshot(app="spotify")
    assert fake_win.captured == [21]


_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


@_WINDOWS_ONLY
def test_windows_find_windows_filters_and_matches(monkeypatch):
    """capture.find_windows = _win32.match_windows over real-sized, on-screen
    windows first, minimized ones as the fallback."""
    from jarvis.tools.windows import _win32, capture

    def info(hwnd, exe, title, w=1200, h=800):
        return _win32.WindowInfo(hwnd=hwnd, title=title, pid=100 + hwnd,
                                 exe_path=rf"C:\Apps\{exe}.exe", rect=(0, 0, w, h))

    names = {r"C:\Apps\Code.exe": "Visual Studio Code", r"C:\Apps\chrome.exe": "Google Chrome"}
    wins = [info(1, "Code", "app.py - Visual Studio Code"), info(2, "chrome", "Docs - Google Chrome"),
            info(3, "Notepad", "tiny", 30, 20), info(4, "Spotify", "Spotify Premium")]
    monkeypatch.setattr(_win32, "file_description", lambda path: names.get(path, ""))
    monkeypatch.setattr(_win32, "list_windows", lambda: wins)
    monkeypatch.setattr(capture, "is_minimized", lambda w: w.hwnd == 4)
    monkeypatch.setattr(capture, "restored_rect", lambda w: w.rect)

    def ids(app):
        return [w.hwnd for w in capture.find_windows(app)]

    for query in ("code", "Code.exe", "Visual Studio Code", "vscode", "studio"):
        assert ids(query) == [1], query
    for query in ("chrome", "Google Chrome", "docs"):
        assert ids(query) == [2], query
    assert ids("notepad") == []  # its only window is too small to be real
    assert ids("spotify") == [4]  # minimized: found only by the fallback pass
    assert capture.app_names() == ["Google Chrome", "Visual Studio Code"]  # on-screen, real-sized


def test_windows_capture_failure_is_explained(fake_win, monkeypatch):
    def boom(bbox=None):
        raise OSError("screen grab failed")

    monkeypatch.setattr(fake_win, "grab_screen", boom)
    err = shot.screenshot()
    assert err.startswith("ERROR: Windows refused the screen capture (screen grab failed)")
    assert "screenshot(url=…)" in err


def test_windows_needs_no_screen_recording_permission(monkeypatch):
    monkeypatch.setattr(shot.sys, "platform", "win32")
    assert _REAL_SCREEN_RECORDING_ALLOWED() is True
    monkeypatch.setattr(shot, "_windows_terminal_name", lambda: None)
    assert shot.permission_error().startswith("ERROR: Windows refused the screen capture")
    shot.request_screen_recording()  # a no-op, never runs macOS `open`


@pytest.mark.parametrize("chain, wt_session, expected", [
    (["pwsh.exe", "windowsterminal.exe", "explorer.exe"], "", "Windows Terminal"),
    (["python.exe", "powershell.exe", "code.exe"], "", "VS Code"),
    (["cmd.exe", "explorer.exe"], "", "Command Prompt"),
    (["pwsh.exe", "svchost.exe"], "1", "Windows Terminal"),
    (["python.exe", "explorer.exe"], "", "your terminal app"),
])
def test_windows_terminal_name_from_parent_processes(fake_win, monkeypatch, chain, wt_session, expected):
    monkeypatch.delenv("__CFBundleIdentifier", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setenv("WT_SESSION", wt_session) if wt_session else monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setattr(fake_win, "ancestor_exes", lambda: chain)
    assert shot.terminal_app_name() == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Windows install paths use backslashes")
def test_windows_find_chrome_checks_install_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(shot.sys, "platform", "win32")
    monkeypatch.delenv("HARNESS_CHROME", raising=False)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.setenv(var, str(tmp_path / "none"))
    local = tmp_path / "local"
    edge = local / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert shot.find_chrome() == str(edge)
    chrome = local / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    assert shot.find_chrome() == str(chrome)  # Chrome is preferred over Edge
    custom = tmp_path / "my-chrome.exe"
    custom.write_bytes(b"")
    monkeypatch.setenv("HARNESS_CHROME", str(custom))
    assert shot.find_chrome() == str(custom)  # the override still wins


def test_pillow_fit_downscales_off_macos(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(shot.sys, "platform", "win32")
    out = shot.screenshot(path=str(_png(tmp_path / "big.png", 3200, 1800)))
    saved = pathlib.Path(out.split("saved: ", 1)[1].splitlines()[0])
    assert saved != tmp_path / "big.png" and (tmp_path / "big.png").exists()  # source kept
    assert shot.image_size(saved) == (1568, 882)


def test_new_paths_never_collide(monkeypatch):
    monkeypatch.setattr(shot.time, "monotonic_ns", lambda: 5_000_000)  # a frozen, coarse clock
    a = shot._new_path()
    a.write_bytes(b"x")
    b = shot._new_path(".jpg")
    assert a != b and a.stem != b.stem


def test_windows_chrome_is_killed_as_a_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(shot.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(shot.subprocess, "run", lambda cmd, **k: calls.append(cmd))

    class Proc:
        pid = 4321

        def __init__(self):
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def kill(self):
            self.alive = False

        def wait(self, timeout=None):
            return 0

    shot._kill_tree_windows(Proc())
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


# ── Windows: real screen / real OCR (skipped elsewhere) ──────────────────

def _desktop_available() -> bool:
    try:
        from jarvis.tools.windows import capture as wc

        return wc.virtual_screen()[2] > 0
    except Exception:
        return False


@_WINDOWS_ONLY
def test_real_windows_screen_and_window_capture():
    pytest.importorskip("PIL")
    if not _desktop_available():
        pytest.skip("no interactive desktop")
    from jarvis.tools.windows import capture as wc

    out = shot.screenshot()
    if out.startswith("ERROR: Windows refused"):
        pytest.skip("desktop locked / secure desktop")
    saved = pathlib.Path(out.split("saved: ", 1)[1].splitlines()[0])
    size = shot.image_size(saved)
    assert size and max(size) <= shot.MAX_EDGE, out
    wins = wc.app_windows()
    if wins:
        out = shot.screenshot(app=wins[0].exe_name)
        assert out.startswith("Screenshot of ") and "window" in out.splitlines()[0], out


def _text_png(path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 90 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    for i, line in enumerate(lines):
        draw.text((20, 20 + 90 * i), line, fill="black", font=font)
    img.save(path)
    return path


@_WINDOWS_ONLY
def test_real_windows_ocr_reads_rendered_text(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("winrt.windows.media.ocr")
    from jarvis.tools.ocr import read_image_text

    img = _text_png(tmp_path / "hello.png", ["Hello Jarvis OCR", "Second line 12345"])
    text = read_image_text(str(img))
    if "language pack" in text:
        pytest.skip(text)
    assert text.splitlines() == ["Hello Jarvis OCR", "Second line 12345"]  # one line per text line
    jpg = tmp_path / "hello.jpg"
    from PIL import Image

    Image.open(img).convert("RGB").save(jpg)
    assert "Hello Jarvis OCR" in read_image_text(str(jpg))


@_WINDOWS_ONLY
def test_real_windows_ocr_edge_cases(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("winrt.windows.media.ocr")
    import asyncio

    from PIL import Image

    from jarvis.tools.ocr import read_image_text

    blank = tmp_path / "blank.png"
    Image.new("RGB", (120, 80), "white").save(blank)
    assert read_image_text(str(blank)) == "No text detected in image."
    bogus = tmp_path / "photo.heic"
    bogus.write_bytes(b"definitely not an image")
    err = read_image_text(str(bogus))
    assert err.startswith("ERROR: Windows OCR can't decode photo.heic")
    img = _text_png(tmp_path / "loop.png", ["Inside a loop"])

    async def call_inside_running_loop():
        return read_image_text(str(img))

    assert "Inside a loop" in asyncio.run(call_inside_running_loop())
