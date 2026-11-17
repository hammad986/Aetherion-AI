# Changelog

All notable changes to Aetherion CLI are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2024-07-15

### Added

#### Terminal Workspace (TUI)
- **Full Textual-based AI workspace** (`aetherion tui`) with 8 named screens
- **Single-layout architecture** — screens swap inside `ContentArea` widget, no push_screen overhead
- **Reactive state engine** (`tui/state.py`) — thread-safe, typed, subscription-based; 12+ workspace domains
- **Gateway bridge** (`tui/gateway.py`) — async SSE streaming, 3-retry exponential backoff, health monitoring, daemon threads
- **Design system** (`tui/theme.py`) — deep-space dark palette, electric violet accent, single CSS source of truth
- **Progressive-disclosure status bar** — greedy-budget algorithm drops tail segments gracefully on narrow terminals
- **Command palette** (`Ctrl+P`) — fuzzy-filtered, 16 categorized commands, keyboard-only navigation
- **Approval overlay** — risk-tier color borders (green/amber/red), `A`/`D` key resolution
- **Notification banner** — 4-second auto-dismiss, level-colored (info/success/warning/error)
- **Navigation sidebar** — reactive active indicators, connection status footer, `Ctrl+B` toggle

#### Workspace Screens
- **Dashboard** — 5 KPI cards, quick-launch bar, recent sessions table, live timeline widget
- **Chat** — split-and-freeze streaming markdown rendering, role-styled messages, context heatmap, command history
- **Session Browser** — 2-pane layout, real-time search/filter, detail panel, create/delete actions
- **Memory Explorer** — search-as-you-type, importance heatmap, inline add-memory form
- **Plugin Manager** — marketplace + installed filter, 2-pane detail, install/uninstall
- **Skills Manager** — 2-pane with enable/disable, inline skill creation panel
- **Provider Manager** — live latency indicators, BYOK API key input (encrypted), test/save/remove
- **Background Jobs** — live progress bars, summary bar, 15-second auto-refresh

#### Skill Creator
- **Workflow-to-skill converter** (`tui/skill_creator.py`) — packages conversation history into reusable `SKILL.md`
- Writes to `~/.aetherion/config/skills/<name>/SKILL.md` in standard SKILL.md format
- Auto-registers with backend via `POST /api/skills/install` when authenticated
- Non-blocking background thread execution
- `aetherion skills create` CLI command
- `aetherion skills local` CLI command (lists and JSON-exports local skills)

#### CLI Commands
- `aetherion chat` — interactive streaming conversation
- `aetherion run` — non-interactive one-shot task
- `aetherion plan` — generate plan without executing
- `aetherion history list/show` — session history
- `aetherion memory search/remember/list/clear` — persistent memory
- `aetherion plugins list/install/uninstall/info` — plugin management
- `aetherion skills list/install/create/local` — skill management
- `aetherion providers list/test` — provider status
- `aetherion config` — view and update configuration
- `aetherion doctor` — health check (connectivity, auth, environment)
- `aetherion login/logout/whoami` — authentication

#### Distribution
- PyPI package (`aetherion`, `aetherion[tui]`)
- Standalone binaries: Linux x86_64, macOS x86_64, macOS arm64, Windows x86_64
- PyInstaller spec with aggressive exclusion of unused dependencies
- GitHub Actions release pipeline (test matrix 3 OS × 4 Python versions)
- Homebrew formula (`aetherion/homebrew-tap`)
- Scoop manifest with auto-update
- Winget manifest v1.6.0

#### Infrastructure
- Thread-safe state with `threading.Lock` protecting all mutations
- `sys.excepthook` + `threading.excepthook` crash recovery → `~/.aetherion/logs/tui_crash.log`
- Keyring credential storage (macOS Keychain / Windows Credential Manager / libsecret)
- Atomic config file writes with POSIX owner-only permissions
- 82 automated tests (0 failures) covering state, skill creator, and CLI integration

### Architecture decisions
- **One backend** — CLI and TUI are pure API clients; no frontend-only persistence
- **Screen swap over push_screen** — avoids Textual screen stack overhead for instant navigation
- **Split-and-freeze streaming** — stable prefix (before last `\n\n` outside code fence) never re-renders
- **Greedy-budget status bar** — 5 priority-ordered tail segments, each silently dropped when terminal is narrow
- **Approval overlay** — Z-layer overlay, not a new screen; avoids input focus confusion
- **Skill creator is non-blocking** — always runs in a daemon thread; UI stays responsive

---

[1.0.0]: https://github.com/aetherion/aetherion-ai/releases/tag/v1.0.0
