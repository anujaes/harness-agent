"""Windows desktop control — the twin of ``jarvis.tools.mac``.

Same tool names and result shapes where the concept carries over
(``launch_app``, ``read_ui``, ``click_element``, ``key_press`` …), with
Windows-native stand-ins where it doesn't: ``powershell`` for ``applescript``,
``task_run`` for Apple Shortcuts, ``system_control`` for ``mac_control``.
Built on UI Automation, ``SendInput`` and WinRT; imported only on Windows.
"""
from .powershell import powershell
from .apps import launch_app, focus_app, quit_app, list_apps, frontmost_app
from .ui import read_ui, click_element, wait, check_permissions
from .input import type_text, key_press, click_menu, click_at
from .clipboard import clipboard_get, clipboard_set
from .system import open_url, notify, speck, task_run, system_control
