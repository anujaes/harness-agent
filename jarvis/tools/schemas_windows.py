"""Tool JSON schemas for Windows desktop control tools (twin of ``schemas_mac``)."""

_NAME = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
_EMPTY = {"type": "object", "properties": {}}

WINDOWS_TOOLS = [
    {"name": "launch_app",
     "description": "Launch a Windows app by name and bring it to the front (e.g. 'Chrome', 'Notepad', "
                    "'WhatsApp', 'Visual Studio Code', 'Calculator'). Finds Start-menu apps, Store apps, "
                    "programs on PATH, or a full path to an .exe/.lnk. Focuses it if already running.",
     "input_schema": _NAME},
    {"name": "focus_app", "description": "Bring a running app's window to the front.", "input_schema": _NAME},
    {"name": "quit_app",
     "description": "Close an app gracefully (like clicking X on each of its windows). force=true kills it "
                    "(unsaved work is lost).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "list_apps", "description": "List open app windows as 'App — window title' lines.",
     "input_schema": _EMPTY},
    {"name": "frontmost_app", "description": "Get the frontmost app and its window title.",
     "input_schema": _EMPTY},
    {"name": "powershell",
     "description": "Run arbitrary PowerShell. Highest-leverage Windows automation: COM (Outlook, Excel, "
                    "Word via New-Object -ComObject), registry, services, Get-Process, Start-Process, "
                    "WinRT, file ops, networking. Asks the user for approval like run_bash.",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["code"]}},
    {"name": "read_ui",
     "description": "Read the UI Automation tree of an app as text (no screenshot, no OCR). Hierarchical dump "
                    "of every visible element: control type, name, value, description, and center "
                    "coordinates (physical screen pixels, usable with click_at). Use this to SEE the screen "
                    "before deciding what to click or type.",
     "input_schema": {"type": "object", "properties": {
         "app": {"type": "string", "description": "app name; blank = frontmost"},
         "max_depth": {"type": "integer"},
         "max_lines": {"type": "integer"},
         "max_chars": {"type": "integer"}}}},
    {"name": "click_element",
     "description": "Find a UI element by text (matches name/value/description/automation id, "
                    "case-insensitive) and press it via UI Automation (Invoke/Toggle/Select), falling back "
                    "to a real click. Much more reliable than click_at. Optional role filter ('button', "
                    "'edit', 'link', 'listitem', 'tabitem', 'checkbox', 'menuitem', …).",
     "input_schema": {"type": "object", "properties": {
         "app": {"type": "string"}, "query": {"type": "string"},
         "role": {"type": "string"}, "nth": {"type": "integer"}},
         "required": ["app", "query"]}},
    {"name": "wait", "description": "Sleep N seconds to let the UI settle after a click/keystroke before reading it again.",
     "input_schema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "check_permissions",
     "description": "Diagnose desktop control: UI Automation, screen capture, and whether the target app runs "
                    "as Administrator (which blocks reading/controlling it). Call this first if UI tools, "
                    "clicks or keystrokes are failing.",
     "input_schema": _EMPTY},
    {"name": "type_text", "description": "Type a string into the focused control of the frontmost app (any language/emoji).",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "key_press",
     "description": "Press a key or chord, e.g. 'enter', 'ctrl+f', 'ctrl+shift+t', 'alt+tab', 'alt+f4', "
                    "'win+d', 'down', 'f5'. 'backspace' deletes left, 'delete' deletes right. "
                    "(cmd is treated as ctrl.)",
     "input_schema": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}},
    {"name": "click_menu",
     "description": "Click a menu-bar item by path, e.g. app='Notepad', path=['File','Save as']. Works for "
                    "classic menu bars; Office's ribbon uses tabs — use click_element there.",
     "input_schema": {"type": "object", "properties": {
         "app": {"type": "string"},
         "path": {"type": "array", "items": {"type": "string"}}}, "required": ["app", "path"]}},
    {"name": "click_at",
     "description": "Click at absolute screen coordinates in physical pixels, as reported by read_ui / "
                    "screenshot (last resort). button: left|right|middle; double=true for a double-click.",
     "input_schema": {"type": "object", "properties": {
         "x": {"type": "integer"}, "y": {"type": "integer"},
         "button": {"type": "string"}, "double": {"type": "boolean"}}, "required": ["x", "y"]}},
    {"name": "clipboard_get", "description": "Return current clipboard text.", "input_schema": _EMPTY},
    {"name": "clipboard_set", "description": "Set clipboard text.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "open_url",
     "description": "Open a URL, file or folder in its default handler (e.g. 'https://…', "
                    "'whatsapp://send?phone=…', 'ms-settings:display', 'C:\\\\Users\\\\me\\\\report.pdf').",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "notify", "description": "Show a Windows notification (toast).",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "message": {"type": "string"}}, "required": ["title"]}},
    {"name": "speck",
     "description": "Speak text aloud (Windows speech). Use only for brief, human-style utterances — the user "
                    "hears this like a real conversation: a few words, not a paragraph. For long explanations, "
                    "reply in text and speck a short blip (e.g. status). Optional `voice` (part of an installed "
                    "voice name, e.g. 'Zira'; voice='?' lists them), optional `rate` (words/min, 0=default).",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "A handful of words or one very short sentence (think in-person, not a script)."},
         "voice": {"type": "string", "description": "Installed voice name (or part of it); omit for default; '?' lists voices."},
         "rate": {"type": "integer", "description": "Speech rate in words per minute; 0 = default."}}, "required": ["text"]}},
    {"name": "task_run",
     "description": "Run a saved automation by name — a PowerShell script in ~/.harness/tasks/<name>.ps1 "
                    "(or <project>/.harness/tasks) or a Windows Task Scheduler task. input_text reaches the "
                    "script on stdin and as $env:JARVIS_INPUT. name='?' lists available tasks.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "input_text": {"type": "string"}}, "required": ["name"]}},
    {"name": "system_control",
     "description": "System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off, sleep, "
                    "lock, dark_mode, light_mode, toggle_dark, brightness}. value: 0-100 for volume/"
                    "brightness (omit to read the current level).",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string"}, "value": {"type": "string"}}, "required": ["action"]}},
]
