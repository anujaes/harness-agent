# Release notes — Jarvis for Windows

What each update of the Windows edition (`anujaes/harness-agent`, branch
`windows-support`) brings to Windows users. Upstream
([PrajsRamteke/harness-agent](https://github.com/PrajsRamteke/harness-agent))
is ported change by change, never merged; each port section records the
upstream range it covers. Maintained with the `release-notes` skill
(`.claude/skills/release-notes/`).

## Unreleased

Upstream port `6b5a8c0..16961d6` (Jarvis 0.2.5) — not committed yet.

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
