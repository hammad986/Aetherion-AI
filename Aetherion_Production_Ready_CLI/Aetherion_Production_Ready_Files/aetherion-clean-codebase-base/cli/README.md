# Aetherion CLI

**The official command-line interface and terminal workspace for the Aetherion AI Platform.**

[![PyPI version](https://img.shields.io/pypi/v/aetherion?color=%237c3aed&label=PyPI)](https://pypi.org/project/aetherion/)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/aetherion?color=%237c3aed)](https://pypi.org/project/aetherion/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE.md)
[![CI](https://github.com/aetherion/aetherion-ai/actions/workflows/release.yml/badge.svg)](https://github.com/aetherion/aetherion-ai/actions)

---

Aetherion is not a command-line wrapper. It is a **complete AI operating environment** that runs inside your terminal — designed for developers who want to work with AI agents without leaving their workflow.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ✦ Aetherion                          ✦ Dashboard   ↑↓ navigate  Ctrl+P   │
│  AI Workspace                                                               │
│  user@aetherion.ai  ──────────────────────────────────────────────────────  │
│  ★ Pro                                                                      │
│                      ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  WORKSPACE           │ Sessions │ │  Tasks   │ │  Tokens  │ │ Latency  │  │
│  ✦ Dashboard  1      │    42    │ │    18    │ │  1.2M    │ │  320ms   │  │
│  ◈ Chat       2      └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  ⊞ Sessions   3                                                             │
│  ◷ Memory     4      Recent Sessions                                        │
│  ⊕ Plugins    5      ✓ Build REST API       3m ago                         │
│  ◆ Skills     6      ✓ Code review PR #42   1h ago                         │
│  ⬡ Providers  7      ◎ Deploy pipeline      running                        │
│  ⊟ Jobs       8                                                             │
│                                                                             │
│  ● online 320ms  ─────────────────────────────────────────────────────────  │
│  Ctrl+P palette                                                             │
│  Ctrl+B toggle   ◎ idle  │  gpt-4o  │  ✓ online 320ms  │  ctx 12%         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Installation

### PyPI (recommended)

```bash
pip install aetherion-cli

# With TUI support (full terminal workspace)
pip install "aetherion-cli[tui]"

# Editable install from source
git clone https://github.com/aetherion/aetherion-ai
cd aetherion-ai/client-cli/aetherion-cli
pip install -e ".[dev]"
```

### Standalone binary (no Python required)

> **Coming soon** — binaries will be available on GitHub Releases for Linux, macOS, and Windows.

### Package managers

> **Coming soon** — Homebrew, Scoop, and Winget packages are planned for v1.1.

---

## Quickstart

```bash
# 1. Authenticate (creates ~/.aetherion/config.json)
aetherion login

# 2. Launch the terminal workspace
aetherion tui

# 3. Or run a one-shot task from the command line
aetherion chat "Build a FastAPI CRUD API for a todo app"

# 4. Non-interactive (pipe-friendly)
aetherion run "Summarise this file" < notes.txt
```

---

## Terminal Workspace (TUI)

Launch with `aetherion tui`. The workspace opens with a full keyboard-driven interface.

### Global shortcuts

| Key | Action |
|-----|--------|
| `1` – `8` | Switch workspace screen |
| `Ctrl+P` | Command palette (fuzzy search all actions) |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+N` | New session |
| `Escape` | Back / dismiss overlay |
| `F1` | Keyboard help |

### Workspace screens

| Screen | Key | Description |
|--------|-----|-------------|
| Dashboard | `1` | KPI metrics, recent sessions, live activity timeline |
| Chat | `2` | Streaming AI conversation with markdown rendering |
| Sessions | `3` | Browse, search, resume, and delete sessions |
| Memory | `4` | Persistent agent memory — search, add, and review |
| Plugins | `5` | Marketplace plugin manager — install/uninstall |
| Skills | `6` | Skill library manager + workflow-to-skill creator |
| Providers | `7` | Live provider status, latency, BYOK key management |
| Jobs | `8` | Background tasks with real-time progress bars |

### Chat screen shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit message |
| `Shift+Enter` | New line |
| `↑` / `↓` | Navigate input history |
| `Ctrl+U` | Clear input |
| `Ctrl+L` | Clear conversation |
| `A` | Allow tool approval (when prompted) |
| `D` / `Escape` | Deny tool approval |

---

## Command Reference

### Authentication

```bash
aetherion login                          # Interactive prompt (email + password)
aetherion login --email you@example.com  # Non-interactive email/password login
aetherion login --email you@example.com --password 'secret'  # Fully non-interactive
aetherion whoami                         # Show current user profile
aetherion logout                         # Clear stored credentials
```

### Agent tasks

```bash
# Interactive streaming chat
aetherion chat "Refactor this function to use async/await"

# One-shot, non-interactive
aetherion run "Write unit tests for auth.py" --model gpt-4o

# Generate a plan without executing
aetherion plan "Migrate PostgreSQL to CockroachDB"

# Resume a session
aetherion chat --session <session-id>

# Choose output format
aetherion chat "List all routes" -o json
```

### Session management

```bash
aetherion history list             # List recent sessions
aetherion history show <id>        # Show session transcript
```

### Replay (time-travel debugging)

Replay is a projection over the append-only evidence ledger — nothing is stored,
every answer is recomputed. Frames are addressed by `seq` (a stable 0-based index
into the ordered event list) because two events can share a millisecond.

```bash
aetherion replay status                       # Engine + read-edge health
aetherion replay list -s <session-id>         # Replayable executions
aetherion replay show <execution-id>          # Summary + trailing frames
aetherion replay show <id> --kinds tool,filesystem --tail 50
aetherion replay frame <id> <seq>             # One frame, with its state
aetherion replay seek <id> --seq 42           # State at an instant
aetherion replay seek <id> --timestamp 1700000000 --transcript
aetherion replay diff <id> --from 0 --to 100  # What changed between frames
aetherion replay export <id> -f bundle.json   # Portable redacted bundle
```

`replay export -o json` writes the bundle to **stdout only** — piping to `jq`
should not also litter the working directory. Full capability guide:
[`docs/replay.md`](../../docs/replay.md).

### Memory

```bash
aetherion memory search "postgres"    # Search agent memory
aetherion memory remember "Always use async SQLAlchemy"
aetherion memory list
aetherion memory clear
```

### Plugins

```bash
aetherion plugins list             # All available plugins
aetherion plugins install git      # Install a plugin
aetherion plugins uninstall git    # Remove a plugin
aetherion plugins info git         # Plugin details
```

### Skills

```bash
# List skills from backend
aetherion skills list

# Install a skill
aetherion skills install code-review

# Create a new skill from a description
aetherion skills create --name git-workflow \
  --description "Automate git commit, tag, and push"

# List locally created skills
aetherion skills local
```

Skills are written to `~/.aetherion/config/skills/<name>/SKILL.md` in standard
SKILL.md format (name, description, triggers, body). Create them from the TUI
via the Skills screen → "Create Skill" panel, which automatically captures
the current conversation as workflow steps.

### Providers

```bash
aetherion providers list           # All providers + status
aetherion providers test openai    # Test connectivity + latency
```

### Configuration

```bash
aetherion config --show                        # Show current config
aetherion config --url https://api.aetherion.ai
aetherion config --model claude-3-5-sonnet
aetherion config --output json
```

### Health

```bash
aetherion doctor          # Connectivity, auth, environment check
```

---

## Configuration

Settings are stored in `~/.aetherion/config.json`. Credentials are stored in
the OS keyring (macOS Keychain, Windows Credential Manager, libsecret on Linux)
with a plain-text file fallback.

| Variable | Priority | Description |
|----------|----------|-------------|
| `AETHERION_URL` | Env (highest) | Backend server URL |
| `AETHERION_TOKEN` | Env | Auth token (CI/CD use) |
| `AETHERION_MODEL` | Env | Default model override |
| OS keyring | Keyring | Token for interactive use |
| `~/.aetherion/config.json` | File | All other settings |

### Example: CI/CD usage

```bash
# Set env vars — no interactive login required
export AETHERION_URL=https://api.aetherion.ai
export AETHERION_TOKEN=${{ secrets.AETHERION_TOKEN }}

aetherion run "Run security audit on the PR diff" -o json > audit.json
```

---

## Architecture

```
aetherion (CLI + TUI)
├── aetherion_cli/
│   ├── main.py          # All Click commands
│   ├── client.py        # HTTP client (httpx, retries, auth)
│   ├── config.py        # Config + keyring credential management
│   └── tui/
│       ├── app.py       # Textual App — single layout, screen swap
│       ├── state.py     # Thread-safe reactive state store
│       ├── gateway.py   # Async bridge: SSE streaming + REST API
│       ├── theme.py     # Design system: colors, glyphs, CSS
│       ├── sidebar.py   # Navigation sidebar
│       ├── overlays.py  # Command palette, approval, notifications
│       ├── skill_creator.py   # Workflow → SKILL.md converter
│       └── screens/
│           ├── dashboard.py   # KPI cards + timeline
│           ├── chat.py        # Streaming conversation
│           ├── sessions.py    # 2-pane session browser
│           ├── memory.py      # Memory explorer
│           ├── plugins.py     # Plugin manager
│           ├── skills.py      # Skills manager
│           ├── providers.py   # Provider BYOK management
│           └── jobs.py        # Background task monitor
```

The CLI and TUI are **pure clients** of the unified Python backend. All business
logic lives in the backend. No frontend-only persistence. The backend is the
single source of truth.

---

## Development

```bash
# Clone and install in editable mode with all extras
git clone https://github.com/aetherion/aetherion-ai
cd aetherion-ai/client-cli/aetherion-cli
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=aetherion_cli --cov-report=term-missing

# Lint
ruff check aetherion_cli/

# Type check
mypy aetherion_cli/ --ignore-missing-imports

# Build standalone binary
pip install pyinstaller
pyinstaller aetherion.spec --noconfirm --clean
./dist/aetherion --version
```

---

## Distribution

| Channel | Status | Command |
|---------|--------|---------|
| PyPI | ✅ | `pip install aetherion-cli` |
| pipx | ✅ | `pipx install aetherion-cli` |
| Homebrew | 🔜 Coming soon | — |
| Scoop | 🔜 Coming soon | — |
| Winget | 🔜 Coming soon | — |
| Standalone binary | 🔜 Coming soon | GitHub Releases |

Releases are automated via GitHub Actions on `v*.*.*` tag push.

---

## License

MIT © 2024 Aetherion. See [LICENSE.md](LICENSE.md).

---

> **Aetherion CLI** — not a wrapper. A workspace.
