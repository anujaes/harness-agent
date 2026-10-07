# Release notes — Jarvis for Windows

What each update of the Windows edition (`anujaes/harness-agent`, branch
`windows-support`) brings to Windows users. Upstream
([PrajsRamteke/harness-agent](https://github.com/PrajsRamteke/harness-agent))
is ported change by change, never merged; each port section records the
upstream range it covers. Maintained with the `release-notes` skill
(`.claude/skills/release-notes/`).

## Unreleased

## 2026-10-07 — Windows: updating while Jarvis is open (Jarvis 0.3.1)

### Windows support
- `/upgrade`, `jarvis update` and the automatic update no longer get stuck while Jarvis is open. The `jarvis` command now starts `python -m jarvis` instead of `jarvis.exe`, which the running Jarvis kept locked so the package reinstall failed. Existing installs switch over by themselves at the next launch. Tests: `test_windows_update.py`.
- Updates only reinstall the package when its dependencies changed. A plain code update just restarts, so it's quicker.
- If the reinstall still can't finish (an older Jarvis window is open), Jarvis restarts into the new code anyway and finishes the install at the next launch, instead of stopping at "pip install failed".
- Leftover `~arness_jarvis-*.dist-info` folders from earlier failed updates are cleaned up.

## 2026-10-07 — upstream port `16961d6..2dfeb9f` (Jarvis 0.3.1)

### From upstream
- `/stats` shows a session-statistics card in the web remote; tool-call counts survive `/load` and resumed sessions. (`cc2bd72`, `65aff42`)
- Model lists drop models the OpenCode gateways retired, and the free Harness Agent model follows OpenCode Zen's live free list instead of a fixed one. (`7518160`, `dfb3393`)
- Optional frosted-glass look for the web remote (translucent panels). (`dff0c9e`, `fe68b05`)
- Token counts stay accurate when a provider omits usage data (estimated instead of 0). (`7e30c81`)
- MCP marketplace: guided setup for servers that need your own OAuth app, richer catalog descriptions. (`88403cc`)
- One consistent dialog layout for every list in the web remote (models, agents, sessions, skills, commands, MCP, providers); small picker fixes. (`2079889`, `e369580`, `6cd3748`)
- Messages typed while Jarvis is busy are queued and sent in order, in the terminal and on the web. (`a27e9a6`)
- Anywhere mode keeps the computer awake while the public link is open (`web.keep_awake`, on by default). (`eb5701e`)
- Project switcher: every running Jarvis on this computer is reachable from one web link and one tunnel. (`c50e829`)
- Open a folder from the browser: starts a Jarvis there with no terminal (headless), browse folders, move the chat to another folder, stop projects opened this way. (`5117c9a`)
- MCP on/off: switch single servers or all of MCP off without removing them. (`3c47683`)
- Better prompt caching and `read_file` with numbered lines and size limits (`HARNESS_PROMPT_CACHE`, `HARNESS_READ_MAX_LINES`, `HARNESS_READ_MAX_CHARS`); tool groups stay for the whole conversation. Long command output keeps its start and its end. (`25bac96`)
- Context budget: big context packs are folded so a request never overflows the model's window. (`db5fc3a`)
- Start a new chat (in a new Jarvis, choosing the folder) while a reply is still running. (`5244e10`, `2dfeb9f`)
- Parallel subagents: `/team` and the `spawn_agents` tool split a big task across 2–6 agents (settings `subagents.*`). (`535ab38`)
- Per-model thinking levels for Codex and models.dev models. (`ef179ae`)
- Version 0.3.1; `/retry` removed. (`960cfc9`, `e1d0995`)

### Windows support
- Keep awake works on Windows: a small helper holds `SetThreadExecutionState` (system stays awake, display may sleep) and ends with Jarvis even if it crashes. The message says "PC kept awake". Tests: `test_windows_keep_awake_helper_ends_with_the_process_it_watches`, `test_tunnel_holds_the_pc_awake_until_stopped`, `test_awake_label_names_this_computer`.
- Project switcher: checking whether another Jarvis is alive no longer uses `os.kill(pid, 0)`, which on Windows *terminates* that process. Its instance files are private to your user (owner-only folder ACL, set once instead of spawning `icacls` every few seconds). Tests: `test_pid_alive_never_harms_the_process_it_checks`, `test_registration_is_listed_private_and_removed`.
- Projects opened from the browser keep running after the terminal closes (own hidden console, own process group, out of the terminal's job), and are found even though a venv's `python.exe` starts the real interpreter as a child. Tests: `test_the_child_does_not_inherit_the_terminals_tunnel`, `test_a_launcher_process_in_between_is_followed`.
- Stopping a project from the browser asks it to quit through a Windows stop event, so it cleans up (registry entry, MCP servers, jobs); one that doesn't respond is ended with its whole process tree. Tests: `test_stop_signal.py`, `test_windows_stop_uses_the_stop_event_not_terminate`, `test_windows_stop_forces_a_jarvis_that_ignores_the_request`, `test_windows_headless_app_exits_on_the_stop_event`.
- Folder picker on Windows: hidden/system folders (`AppData`) are hidden, drives (`C:\`, `D:\`) and your real (OneDrive) Desktop/Documents/Downloads are shortcuts, `~\x` paths work, folder matching ignores case, and folders your user may not open are marked. Tests: the `test_windows_*` tests in `test_web_launch.py`.
- The thinking picker sees a refreshed model catalog right away on Windows. Two quick catalog updates of the same size could land in the same ~15 ms Windows file-time tick and show stale thinking levels. Test: `test_a_rewrite_with_the_same_size_and_mtime_is_still_seen`.
- Desktop tools stay loaded for the whole conversation like the other tool groups (the Windows edition's group is called `desktop`). Test: `test_desktop_tools_stay_once_added`.
- Upstream's new tests run on Windows (UTF-8 reads, `fake_program` stand-ins instead of `#!/bin/sh` scripts, Windows twins for POSIX-only checks).

- `read_file`, `write_file`, `edit_file` and `multi_edit` read and write UTF-8 on Windows. Before, `café` was read as `cafÃ©`, an edit wrote that back into the file, and `✓` could not be written at all. Test: `test_files_utf8.py`.
- File-search snippets, the code-context graph and `/notes` read UTF-8 too. Non-English text in snippets was garbled, symbols with non-ASCII names were dropped from the graph, and `/notes` could crash on some characters. Test: `test_read_utf8.py`.

## 2026-10-02 — upstream port `6b5a8c0..16961d6` (Jarvis 0.2.5)

### From upstream
- Dozens of extra providers and models from the models.dev catalog: add a key in `/key` (or set e.g. `DEEPSEEK_API_KEY`) and its models show up in `/model`; `HARNESS_MODELS_DEV=0` turns it off. (`3636316`)
- Esc (or web Stop / New chat) now stops a running shell command at once instead of letting it hold the shell for up to a minute; switching session while a turn runs stops that turn. Simpler footer for the web remote. (`37673b5`)
- Screenshots of web pages prefer the lightweight `chrome-headless-shell` (Playwright / Puppeteer) over full Chrome; session deletion fixes. (`fd7941d`)
- Agents, skills and MCP servers from other tools' folders (Claude, Cursor, Codex, Gemini, OpenCode, Kiro …) are found and tagged with the tool they come from. (`223564f`)
- Attach images, PDFs and other files from the web remote; images go to models that can see them. `HARNESS_UPLOAD_MAX_MB`, `HARNESS_UPLOAD_KEEP_DAYS`. (`ab97718`)
- Model list shows free-tier and image-support labels. (`4aa68b9`)
- Model list in the browser: collapsible groups and better search. (`ffbc250`)
- Manage slash commands from the browser (create, edit, copy, delete) plus web UI polish. (`16961d6`)

### Windows support
- Esc stops the whole shell command on Windows too — the shell, the program it started and that program's children (Job Object), in Git Bash, PowerShell and cmd — and the next command runs right away. Tests: `test_cancelling_a_turn_kills_its_shell_command_tree_on_windows`, `test_shell.py`.
- models.dev provider keys (`provider_keys.json`) get an owner-only ACL before any key is written. Test: `test_keys_file_is_private_and_round_trips`.
- Web attachments are stored in a folder locked to your Windows user, also when the folder already existed. Test: `test_upload_store_is_private_to_the_user`.
- `chrome-headless-shell.exe` is found in `%LOCALAPPDATA%\ms-playwright` and Puppeteer's cache. Test: `test_find_headless_shell_in_windows_playwright_and_puppeteer_caches`.
- The new welcome-screen hint reads `Shift+Enter newline` on Windows. Test: `test_welcome_hints_spell_keys_for_the_os`.
- Upstream's new tests run on Windows (home folder redirect via `USERPROFILE`, UTF-8 file reads, native path checks).
- The repo now carries its rules for upstream ports, test-first development and code comments (`CLAUDE.md`), and the `release-notes` skill.

### Known gaps
- HEIC/HEIF photos attached from the web are stored but not converted for the model on Windows (upstream converts them with macOS `sips`; Pillow can't read HEIC without the optional `pillow-heif`). PNG, JPEG, GIF, WebP, BMP and TIFF work.
- Key dialogs still say "(chmod 600)" on Windows, where the file is protected with an owner-only ACL instead (wording only).

### Removed
- The Kimchi provider (removed upstream). A saved Kimchi choice starts on the free tier instead. (`a0985de`)

## 2026-10-01 — Windows edition baseline (Jarvis 0.2.5, upstream `6b5a8c0`)

### Windows support
- First Windows release: one-line installer (`scripts/install.ps1`) and uninstaller, self-update on Windows, Git Bash / PowerShell / cmd shells (`HARNESS_SHELL`), desktop tools built on UI Automation (`powershell`, `task_run` and `system_control` replace `applescript`, `shortcut_run` and `mac_control`), screenshots and OCR, owner-only private files, Ctrl/Alt key hints and web-remote tunnels.
