# Harness — Jarvis Terminal Agent

**AI coding agent for your terminal.**  
Chat, run tools, edit files, execute shell commands, and control your desktop (macOS or Windows) — all from one TUI.

> **🪟 Windows edition.** This is the `windows-support` branch of [github.com/anujaes/harness-agent](https://github.com/anujaes/harness-agent/tree/windows-support): the full Jarvis agent ported to Windows 10/11 (UI Automation desktop control, Git Bash/PowerShell shell, Windows OCR, toasts, …). It is maintained as its own branch and is not merged into `main`. For macOS/Linux use [PrajsRamteke/harness-agent](https://github.com/PrajsRamteke/harness-agent) (`main`).



---

## ✨ Overview

Harness is a **terminal-native AI agent** that lives in your terminal. You talk to it, it uses tools — reads/writes files, runs shell commands, searches code, uses git, controls macOS and Windows apps, OCRs images, browses the web — and gets work done right where your code lives.

> **No web UI, no daemon.** Just `jarvis` in your project folder.

---

## 🚀 Quick Start

### 🪟 Windows

Open **PowerShell** or **Command Prompt** and paste this one line:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/install.ps1 | iex"
```

Then open a **new terminal** in your project folder and run:

```powershell
jarvis
```

**Fresh PC?** That's fine — if Python 3.10+ or Git is missing, the installer offers to install it
with `winget` and carries on. No admin rights needed for Jarvis itself. Other ways to install
(uv / pipx, manual clone) are under [Installation](#-installation).

### 🍎 macOS / 🐧 Linux

**macOS without Python 3.10+** (install Python, then Jarvis):

```bash
brew install python@3.11
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc   # or ~/.zprofile on macOS — needed so `jarvis` is on PATH
jarvis
```

If `jarvis` is not found in the same terminal right after install:

```bash
export PATH="$HOME/.local/bin:$PATH"
jarvis
```

**Already have Python 3.10+:**

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc   # or ~/.zprofile on macOS
jarvis
```

That's it. You'll be prompted to pick an auth method on first run.

---

## 🖼️ Screenshots


|     |     |
| --- | --- |
|     |     |


---

## 📋 Table of Contents

- [Features](#-features)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Usage](#-usage)
- [Slash Commands](#-slash-commands)
- [Environment Variables](#-environment-variables)
- [Windows](#-windows)
- [Project Layout](#-project-layout)
- [Notes](#-notes)

---

## 🧰 Features


|                                                                                                                                 |                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 💬 Interactive TUIRich terminal UI with markdown rendering, syntax-highlighted code, panels, and streaming responses.          | 🔐 Dual AuthUse an **API key** (sk-ant-…) or sign in with **OAuth** via PKCE.                                                             |
| 📁 File OperationsRead, write, edit files. List directories, glob patterns, rank files by relevance, search code with ripgrep. | 🐚 Shell AccessRun any shell command, view output inline — no context switching.                                                          |
| ⎇ Git IntegrationStatus, diff, log — all from the chat. No need to tab out.                                                    | 🖥️ Desktop ControlmacOS and Windows: launch/focus/quit apps, click UI elements, type text, run AppleScript / PowerShell, keyboard shortcuts, clipboard, notifications. |
| 🌐 Web AccessSearch the web and fetch URLs. Verified search cross-checks multiple sources for factual answers.                 | 🧠 Persistent MemoryRemembers facts about you across sessions. Stores skills, notes, and aliases under `~/.config/claude-agent/`.         |
| 📊 Cost Tracking`/cost` shows token usage and estimated USD spend per session.                                                 | 🔌 MCP SupportModel Context Protocol — connect external tools and data sources.                                                           |
| 🎨 ThemesBuilt-in **red** and **purple** themes. Easily extensible.                                                            |                                                                                                                                            |


---

## ✅ Requirements

- **Python 3.10+** — macOS ships with older system Python (`/usr/bin/python3`). Install a newer one before the install script:
  ```bash
  brew install python@3.11
  ```
  The install script also checks `/opt/homebrew/bin/python3.*` if Homebrew is not on your `PATH` yet.
- **Windows** — Python 3.10+ and Git (the one-line installer offers to install both). Manually via `winget`:
  ```powershell
  winget install -e --id Python.Python.3.12
  winget install -e --id Git.Git
  ```
  [Windows Terminal](https://aka.ms/terminal) is recommended for the TUI (it's the default on Windows 11).
- **Windows 10 (1809+) or Windows 11** — for the Windows desktop-control tools (UI Automation, OCR, toasts, speech). [Git for Windows](https://git-scm.com/download/win) is recommended: its Git Bash runs `run_bash` commands.
- **macOS** — required for macOS control features. Core agent works on any platform.
- **API key** (sk-ant-…) or a **Pro/Max subscription**

---

## 📦 Installation

### Windows — pick the way that suits you

| You are… | Install with | Updates |
| --- | --- | --- |
| **Anyone** (recommended) | the one-line PowerShell installer below | automatic, or `jarvis update` |
| **Python developer** using [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/) | `uv tool install` / `pipx install` from Git | `uv tool upgrade` / `pipx upgrade` |
| **Contributor** | `git clone` + editable install ([Development setup](#development-setup)) | `git pull` |

#### 1. One-line installer (recommended)

In **PowerShell** or **Command Prompt**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/install.ps1 | iex"
```

Already inside PowerShell? The shorter form works too, and makes `jarvis` available in that same window straight away:

```powershell
irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/install.ps1 | iex
```

What it does:

- checks for **Python 3.10+** and **Git**, and offers to install whichever is missing with `winget`
- clones the `windows-support` branch into `%LOCALAPPDATA%\harness-agent` and installs it in its own virtual environment (no admin rights needed)
- adds a `jarvis` command in `%USERPROFILE%\.local\bin` and puts that folder on your user `PATH`
- keeps tracking `windows-support`, so Jarvis updates itself from this branch

**Update:** run the same command again, or `jarvis update` (`jarvis update --check` to preview).

Installer options (set before running it): `JARVIS_YES=1` installs missing Python/Git without asking
(unattended setups), `JARVIS_NO_MODIFY_PATH=1` leaves your `PATH` alone, `JARVIS_INSTALL_DIR` /
`JARVIS_BIN_DIR` change the folders, `PYTHON` picks an interpreter. Example:

```powershell
$env:JARVIS_YES = "1"; irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/install.ps1 | iex
```

#### 2. uv or pipx (for Python users)

```powershell
uv tool install "git+https://github.com/anujaes/harness-agent.git@windows-support"
# or
pipx install "git+https://github.com/anujaes/harness-agent.git@windows-support"
```

Upgrade with `uv tool upgrade harness-jarvis` / `pipx upgrade harness-jarvis`. These installs are
managed by uv/pipx, so Jarvis' own auto-update is skipped.

**Troubleshooting: "'jarvis' is not recognized"** — open a **new** terminal so it picks up the updated `PATH`. For the current PowerShell window only:

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"
jarvis
```

**Uninstall** (removes the app and the `jarvis` command; asks before deleting your settings and sessions):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/uninstall.ps1 | iex"
```

### One-command install — macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
```

After install, open a **new terminal**, go to any project, and run:

```bash
jarvis
```

**Troubleshooting: "command not found: jarvis"**

If your shell can't find `jarvis`, add `~/.local/bin` to your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
jarvis
```

Add that line to your `~/.zshrc` to make it permanent.



### Development setup

From the repository root, run these **one at a time** (Python **3.10+**):

```bash
git clone https://github.com/PrajsRamteke/harness-agent.git
cd harness-agent
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
jarvis --help                # verify the CLI is on PATH
```

**Windows (PowerShell):**

```powershell
git clone --branch windows-support https://github.com/anujaes/harness-agent.git
cd harness-agent
py -3 -m venv .venv
.venv\Scripts\Activate.ps1   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e .
jarvis --help
```

`pip install -e .` installs runtime dependencies from `pyproject.toml` and registers the `jarvis` command. You do **not** need `pip install -r requirements.txt` for normal development (that file mirrors the same deps for reference or tooling-only installs).

**Run the agent:**

```bash
jarvis                     # TUI mode (default)
python agent.py            # same as jarvis
python agent.py --legacy   # Rich REPL mode
```

**Run tests** (after `pip install pytest`):

```bash
python -m pytest tests/ -q
```

The same setup sequence is documented at the top of `requirements.txt`.

---

## 🎮 Usage

```bash
jarvis
```

Run it from the **folder you want it to work in**. The status bar shows the current project path — all file operations, code searches, and shell commands are scoped to that directory.

### First run

On first launch, you'll pick how to authenticate:


| Option      | How it works                                                                      |
| ----------- | --------------------------------------------------------------------------------- |
| **API key** | Paste an `sk-ant-…` key. Saved at `~/.config/claude-agent/key` (permissions: 600) |
| **OAuth**   | Opens your browser to sign in with your Pro/Max account via PKCE                  |


### ⌨️ Slash Commands


| Command             | What it does                                                    |
| ------------------- | --------------------------------------------------------------- |
| `/help`             | List all commands                                               |
| `/model <name>`     | Switch models (e.g. `opus-4-7`, `haiku-4-5`)                    |
| `/agent`            | Open the agent picker — choose a project or global agent        |
| `/agent <name>`     | Activate an agent by name (Tab cycles through agents)           |
| `/agent new <name>` | Scaffold a new agent in `.harness/agents/<name>.md`             |
| `/agent init`       | Scaffold a `.harness/` tree in the current project              |
| `/skill`            | Open the skill browser (LLM auto-invokes skills by description) |
| `/skill add <link>` | Install a skill from GitHub / a `SKILL.md` link / an archive (`--project` or `--global`) |
| `/mcp`              | MCP control panel — add a server from a link, sign in, connect |
| `/mcp add <link>`   | Add an MCP server: `https://…`, `npx -y …`, `claude mcp add …`, JSON, a GitHub link, or a name like `linear` |
| `/mcp auth <name>`  | Sign in to a hosted MCP server in your browser |
| `/verbose` / `F2`   | Toggle internal thinking and tool traces (shown by default)     |
| `/cost`             | Show token usage + estimated USD cost                           |
| `/clear`            | Reset the conversation                                          |
| `/logout`           | Clear saved credentials                                         |
| `/theme`            | Switch themes                                                   |


---

## ⚙️ Environment Variables


| Variable                       | What it does                           | Default                            |
| ------------------------------ | -------------------------------------- | ---------------------------------- |
| `ANTHROPIC_API_KEY`            | Use this key instead of the stored one | —                                  |
| `CLAUDE_MODEL`                 | Override default model                 | `sonnet-4-6`                       |
| `HARNESS_MAX_PARALLEL_TOOLS`   | Max concurrent tool workers            | `64` (capped)                      |
| `HARNESS_HTTP_READ_TIMEOUT`    | Streaming response timeout (s)         | `240` (OpenRouter), `600` (direct) |
| `HARNESS_HTTP_CONNECT_TIMEOUT` | Connection timeout (s)                 | `30`                               |
| `HARNESS_STREAM_REPLY`         | Set to `0` to disable live streaming   | `1`                                |
| `HARNESS_SHELL`                | Windows only: shell for `run_bash` — `bash`, `powershell` or `cmd` | Git Bash if installed, else PowerShell |
| `HARNESS_MODELS_DEV`           | Set to `0` to turn off the models.dev provider/model catalog | `1`              |


---

## 🪟 Windows

Jarvis runs natively on Windows 10 (1809+) and Windows 11 — same TUI, same tools, same
config files, no WSL needed.

**Install / update** (PowerShell or Command Prompt — no admin rights; see [Installation](#-installation) for uv/pipx):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/install.ps1 | iex"
```

The checkout lives in `%LOCALAPPDATA%\harness-agent` (override with `JARVIS_INSTALL_DIR`),
and `jarvis.cmd` in `%USERPROFILE%\.local\bin` (override with `JARVIS_BIN_DIR`). Rerun the
command, `jarvis update` or `/upgrade` to update — even while another Jarvis window is open.

**Terminal:** use [Windows Terminal](https://aka.ms/terminal) (default on Windows 11). The
legacy console host works, but mouse support, colours and Unicode rendering are much better
in Windows Terminal. Key hints in the UI read `Ctrl+…` / `Alt+…` / `Shift+…` on Windows.

**Shell:** `run_bash` / `run_bg` / `!cmd` use **Git Bash** (`Git\usr\bin\bash.exe`) when Git for Windows is installed
(models write POSIX shell most reliably; `/c/Users/...` paths work too), otherwise
**PowerShell**. Pick one explicitly with `HARNESS_SHELL=bash|powershell|cmd`:

```powershell
$env:HARNESS_SHELL = "powershell"   # this window only
setx HARNESS_SHELL powershell       # future windows
```

**Desktop control:** the macOS tools have Windows twins built on UI Automation, so the same
requests ("open Spotify and pause it", "click Save", "what's on my screen?") work:

| macOS | Windows | Notes |
| --- | --- | --- |
| `launch_app` · `focus_app` · `quit_app` · `list_apps` · `frontmost_app` | same names | Start-menu apps, `.exe` names or paths |
| `applescript` | `powershell` | Run a PowerShell script (COM / .NET automation) |
| `read_ui` · `click_element` · `click_menu` · `wait` | same names | UI Automation tree instead of the Accessibility API |
| `type_text` · `key_press` · `click_at` | same names | `cmd+…` in a chord maps to `ctrl+…`; text over 200 chars is pasted via the clipboard (your clipboard text is restored) |
| `clipboard_get` · `clipboard_set` · `open_url` | same names | |
| `notify` · `speck` | same names | Toast notifications · SAPI speech |
| `check_permissions` | same name | Reports UI Automation / capture availability (no permission prompts) |
| `shortcut_run` (Shortcuts app) | `task_run` | Runs a Windows Scheduled Task, or a script from `~/.harness/tasks/<name>.ps1` / `<project>/.harness/tasks/` |
| `mac_control` | `system_control` | volume · mute · unmute · battery · wifi_on/off · sleep · lock · dark/light/toggle_dark · brightness |
| `screenshot` · `read_image_text` | same names | GDI capture (coordinates are physical pixels) · OCR via `Windows.Media.Ocr` |

**Feature parity:**

| Feature | macOS | Windows |
| --- | --- | --- |
| TUI, web remote, QR, Anywhere tunnel | ✅ | ✅ (`winget install Cloudflare.cloudflared`) |
| File, code-search, git, web and MCP tools | ✅ | ✅ |
| Shell (`run_bash`, background jobs, `!cmd`) | bash | Git Bash · PowerShell · cmd |
| App / UI control | Accessibility + AppleScript | UI Automation + PowerShell |
| Screenshots & OCR | `screencapture` · Vision | GDI · `Windows.Media.Ocr` |
| Notifications & speech | Notification Center · `say` | Toasts · SAPI |
| Automation by name | Shortcuts (`shortcut_run`) | Scheduled Tasks / `.ps1` (`task_run`) |
| Clipboard (`/copy`, ⌃Y) | ✅ | ✅ |
| Private credential files | mode 600 | ACL limited to your user (`icacls`) |
| Self-update (`/upgrade`, auto-update) | ✅ | ✅ |

---

## 🎛️ Agents & Skills

Harness uses two file-based extension points — **agents** (manual select) and
**skills** (LLM auto-invoke) — that aggregate from every AI tool's config
directory (Harness, Claude Code, OpenCode, Cursor, Windsurf, …).

```
project/
├── .harness/
│   ├── agents/                   ← project-local agents
│   │   ├── coding.md             ← user-creatable .md files with YAML frontmatter
│   │   ├── reverse_eng.md
│   │   └── setup.md
│   ├── skills/                   ← project-local skills
│   │   ├── debugging/SKILL.md
│   │   ├── testing/SKILL.md
│   │   └── security/SKILL.md
│   └── settings.json             ← (optional) per-project overrides
│
├── AGENTS.md  /  CLAUDE.md       ← project context (auto-detected)
└── …

~/.harness/                       ← user-global counterpart
├── agents/                       ← bundled coding/reverse_eng/setup seeded on first run
├── skills/
└── settings.json
```

**Agents** — markdown files with frontmatter (`name`, `description`, optional
`icon`/`color`). The active agent's body is appended to the system prompt.
One active at a time, shown in the status bar. `/agent` opens the picker;
`Tab` cycles. Project agents are always available; global ones require
`agent.global = true` in settings (or `/agent global on`).

**Skills** — `SKILL.md` packs with `name` + `description`. The LLM sees all
discovered descriptions and decides when to load a skill itself via
`/skill load <name>`.

### Adding skills and MCP servers

You don't have to edit any files. Do any of these — in the terminal or the web UI:

- **Ask Jarvis.** "Add this skill: https://github.com/anthropics/skills/tree/main/skills/pdf",
  "add the Linear MCP server to this project", or paste an `npx …` / `claude mcp add …`
  line. Jarvis asks you to approve, installs it, and connects it.
- **Use the dialogs.** `/skill` and `/mcp` (Skills and MCP in the web sidebar) have an
  *Add* box: paste a link, pick **Project** (this folder only) or **Global** (every
  project), done. Popular servers (Linear, Notion, Sentry, GitHub, …) are one click.
- **Slash commands.** `/skill add <link> [--project|--global]`, `/mcp add <link> …`.

Where things go: skills → `.harness/skills/<name>/` (project) or `~/.harness/skills/<name>/`
(global); MCP servers → `.mcp.json` in the project, or `~/.config/harness-agent/mcp.json`
(global). Skills from a GitHub repo need no `git`; a repo with several skills shows a list
to choose from.

**Signing in.** A hosted MCP server that wants a login shows an **Authenticate** button
(above the prompt in the terminal, in the MCP dialog and a banner on the web). Click it,
approve in your browser, and the tools appear on their own — no restart. Signing in from
another device (a phone)? Paste the address it ends on. Servers that want an API key
ask for it in a hidden box; keys are kept in `~/.config/harness-agent/mcp_secrets.json`
(mode 600), never in `.mcp.json`. Opening Jarvis never starts a sign-in by itself, and a
saved login refreshes silently.

---

## 🗂️ Project Layout

```
harness/
├── agent.py                # Entry point (routes to TUI or REPL)
├── pyproject.toml          # Package config
├── requirements.txt
├── CLAUDE.md               # Context file for AI assistants
├── JARVIS.md
│
├── jarvis/                 # Main package
│   ├── __main__.py         # `python -m jarvis`
│   ├── cli.py              # CLI entry point
│   ├── main.py             # Core send-and-loop logic
│   ├── state.py            # Module-level shared state
│   │
│   ├── auth/               # Authentication
│   │   ├── client.py       # Unified client factory
│   │   ├── api_key.py      # API key handling
│   │   ├── oauth_flow.py   # OAuth PKCE flow
│   │   ├── pkce.py         # PKCE utilities
│   │   ├── openrouter.py   # OpenRouter support
│   │   └── opencode.py     # OpenCode adapter
│   │
│   ├── tools/              # Tool implementations
│   │   ├── router.py       # Dynamic tool selection
│   │   ├── schemas_core.py # Core tool schemas
│   │   ├── schemas_mac.py  # macOS tool schemas
│   │   ├── mac/            # macOS control
│   │   ├── windows/        # Windows control (UI Automation, PowerShell)
│   │   └── web/            # Web fetch & search
│   │
│   ├── repl/               # Response handling
│   │   ├── stream.py       # Stream processing
│   │   ├── render.py       # Tool execution + rendering
│   │   ├── hallucination.py
│   │   └── trim.py         # Context trimming
│   │
│   ├── tui/                # Textual TUI
│   │   ├── app.py          # Terminal UI app
│   │   ├── agent_modal.py  # Agent picker
│   │   └── skill_modal.py  # Skill browser + install from a link
│   │
│   ├── commands/           # Slash commands
│   │   ├── dispatch.py
│   │   ├── agent.py        # /agent — pick / new / init / refresh
│   │   └── skill.py        # /skill — list / load / refresh
│   │
│   ├── storage/            # Persistence
│   │   ├── sessions.py     # SQLite session history
│   │   ├── memory.py       # User memory
│   │   ├── agents.py       # Agent discovery & loading
│   │   ├── skills.py       # Skill discovery & loading
│   │   ├── settings.py     # Unified settings.json (global + project merge)
│   │   └── prefs.py        # Legacy preferences
│   │
│   ├── mcp/                # MCP server management
│   │   ├── config.py
│   │   ├── registry.py
│   │   └── manager.py
│   │
│   ├── constants/          # Paths, models, prompts
│   └── utils/
│
├── scripts/                # Install scripts
├── assets/                 # Screenshots
└── tests/                  # pytest unit tests (`python -m pytest tests/ -q`)
```

---

## 📝 Notes

- **macOS permissions** — UI control tools need **Accessibility** and **Automation** permissions. Enable them in: System Settings → Privacy & Security → Accessibility / Automation.
- **Windows** — no permission prompts; UI control can't drive apps running as administrator unless Jarvis runs elevated too. See [Windows](#-windows).
- **Credentials** — All config, keys, and history live under `~/.config/claude-agent/`.
- **Tool selection is dynamic** — Harness only sends the schemas for tools it thinks you'll need, keeping context lean. Core file/code tools are always included; desktop-control (macOS / Windows), web, OCR tools are loaded on demand.
- **Project context** — Drop a `JARVIS.md` (or `CLAUDE.md`) in your project root, and the agent reads it automatically for project-specific instructions.
- **Providers and models** — The provider list and model details (context, price, tool support) come from [models.dev](https://models.dev), an open database by the OpenCode team (MIT licence, © models.dev). It's fetched in the background and cached; an unchanged list costs one tiny request.

---

Built with ❤️ by [Prajwal Ramteke](https://github.com/PrajsRamteke) · Windows edition by [anujaes](https://github.com/anujaes) ([`windows-support`](https://github.com/anujaes/harness-agent/tree/windows-support) · [Windows issues](https://github.com/anujaes/harness-agent/issues))

[GitHub](https://github.com/PrajsRamteke/harness-agent) · [Issues](https://github.com/PrajsRamteke/harness-agent/issues) · [Discussions](https://github.com/PrajsRamteke/harness-agent/discussions)