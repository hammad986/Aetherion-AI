"""
aetherion_cli/tui/theme.py
Aetherion Terminal Workspace — Design System

A single source of truth for all colors, glyphs, and style tokens.
All widgets reference constants from this module rather than hardcoding styles.

Design principles:
- Deep-space dark base: #0a0c12 — darker than standard dark themes
- Electric violet accent: #7c3aed — owned, distinctive, not generic blue
- Warm amber for warnings: #f59e0b — approachable not aggressive
- Semantic greens/reds stay muted (not neon) to reduce eye strain
- Monospace glyph set for terminal-native feel
"""

# ─────────────────────────────────────────────────────────────────────────────
# Color palette
# ─────────────────────────────────────────────────────────────────────────────

class Colors:
    # Base surfaces
    BG_DEEP       = "#0a0c12"   # deepest background (screen)
    BG_SURFACE    = "#12151f"   # panel surfaces
    BG_RAISED     = "#1a1e2e"   # raised cards / active items
    BG_OVERLAY    = "#0a0c12ee" # modal overlays

    # Borders
    BORDER_DIM    = "#1e2433"
    BORDER_ACTIVE = "#3d4a6b"
    BORDER_FOCUS  = "#7c3aed"

    # Accent
    ACCENT        = "#7c3aed"   # violet primary
    ACCENT_BRIGHT = "#9d5cf0"   # hover/active state
    ACCENT_DIM    = "#4c2288"   # dimmed accent for backgrounds

    # Semantic
    SUCCESS       = "#10b981"
    SUCCESS_DIM   = "#064e3b"
    WARNING       = "#f59e0b"
    WARNING_DIM   = "#78350f"
    ERROR         = "#ef4444"
    ERROR_DIM     = "#7f1d1d"
    INFO          = "#3b82f6"

    # Text
    TEXT_PRIMARY  = "#e2e8f0"
    TEXT_MUTED    = "#64748b"
    TEXT_DIM      = "#334155"
    TEXT_INVERSE  = "#0a0c12"

    # Role colors (for message roles)
    USER          = "#60a5fa"   # calm blue — readable, distinct
    ASSISTANT     = "#a78bfa"   # soft violet — on-brand
    SYSTEM        = "#6b7280"   # muted grey
    TOOL          = "#fbbf24"   # amber — draws attention


# ─────────────────────────────────────────────────────────────────────────────
# Glyphs / icons (box-drawing + Unicode — all single width)
# ─────────────────────────────────────────────────────────────────────────────

class Glyphs:
    # Product logo / identity
    AETHERION_LOGO = "◈"   # Double-circle — the Aetherion agent identity mark

    # Status indicators
    IDLE          = "○"
    RUNNING       = "◎"
    STREAMING     = "●"
    DONE          = "✓"
    ERROR         = "✗"
    WAITING       = "◐"
    QUEUED        = "◯"

    # Navigation
    ARROW_RIGHT   = "›"
    ARROW_LEFT    = "‹"
    ARROW_DOWN    = "↓"
    ARROW_UP      = "↑"
    CHEVRON_RIGHT = "❯"
    CHEVRON_DOWN  = "❯"  # rotated in CSS
    BULLET        = "·"
    DASH          = "─"

    # Roles
    USER_ICON     = "⬡"   # hexagon — user
    AI_ICON       = "◈"   # double circle — AI
    TOOL_ICON     = "⚙"
    SYSTEM_ICON   = "ℹ"

    # Panels
    SESSIONS      = "⊞"
    MEMORY        = "◷"
    PLUGINS       = "⊕"
    SKILLS        = "◆"
    PROVIDERS     = "⬡"
    METRICS       = "⊙"
    JOBS          = "⊟"
    GIT           = "⎇"
    TIMELINE      = "⏱"
    HELP          = "?"

    # Borders (box drawing)
    H             = "─"
    V             = "│"
    TL            = "╭"
    TR            = "╮"
    BL            = "╰"
    BR            = "╯"
    T_DOWN        = "┬"
    T_UP          = "┴"
    T_RIGHT       = "├"
    T_LEFT        = "┤"
    CROSS         = "┼"

    # Misc
    SPARKLE       = "✦"
    BOLT          = "⚡"
    CLOCK         = "⏱"
    LOCK          = "🔒"
    OPEN_LOCK     = "🔓"
    FIRE          = "🔥"
    STAR          = "★"
    DOT_FULL      = "█"
    DOT_EMPTY     = "░"
    DOT_HALF      = "▒"


# ─────────────────────────────────────────────────────────────────────────────
# Textual CSS design tokens
# ─────────────────────────────────────────────────────────────────────────────

# These are embedded in the App CSS string. Using Python constants lets us
# have one source of truth for colors across both Python and CSS code.

AETHERION_CSS = f"""
/* ═══ Aetherion Design System ═══════════════════════════════════════════════ */

Screen {{
    background: {Colors.BG_DEEP};
    color: {Colors.TEXT_PRIMARY};
    overflow: hidden;
}}

/* ── Header ─────────────────────────────────────────────────────────────── */
Header {{
    background: {Colors.BG_SURFACE};
    color: {Colors.TEXT_PRIMARY};
    height: 1;
    border-bottom: solid {Colors.BORDER_DIM};
}}
Header > .header--title {{
    color: {Colors.ACCENT_BRIGHT};
    text-style: bold;
}}
Header > .header--sub-title {{
    color: {Colors.TEXT_MUTED};
}}

/* ── Footer ─────────────────────────────────────────────────────────────── */
Footer {{
    background: {Colors.BG_SURFACE};
    color: {Colors.TEXT_MUTED};
    height: 1;
    border-top: solid {Colors.BORDER_DIM};
}}

/* ── Scrollbars ─────────────────────────────────────────────────────────── */
* {{
    scrollbar-color: {Colors.BORDER_ACTIVE} {Colors.BG_SURFACE};
    scrollbar-size: 1 1;
}}

/* ══ Workspace Layout ════════════════════════════════════════════════════════ */

#workspace-container {{
    height: 1fr;
    width: 1fr;
}}

/* ── Left sidebar ───────────────────────────────────────────────────────── */
#left-sidebar {{
    width: 22;
    background: {Colors.BG_SURFACE};
    border-right: solid {Colors.BORDER_DIM};
    height: 1fr;
}}
#left-sidebar.hidden {{
    display: none;
}}

/* Sidebar nav items */
.nav-item {{
    height: 1;
    padding: 0 1;
    color: {Colors.TEXT_MUTED};
}}
.nav-item:hover {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
}}
.nav-item.active {{
    background: {Colors.ACCENT_DIM};
    color: {Colors.ACCENT_BRIGHT};
    border-left: thick {Colors.ACCENT};
}}

/* ── Main content area ──────────────────────────────────────────────────── */
#main-content {{
    width: 1fr;
    height: 1fr;
}}

/* ── Right panel ────────────────────────────────────────────────────────── */
#right-panel {{
    width: 28;
    background: {Colors.BG_SURFACE};
    border-left: solid {Colors.BORDER_DIM};
    height: 1fr;
    display: none;
}}
#right-panel.visible {{
    display: block;
}}

/* ══ Status Bar ══════════════════════════════════════════════════════════════ */

#status-bar {{
    height: 1;
    background: {Colors.BG_SURFACE};
    border-top: solid {Colors.BORDER_DIM};
    padding: 0 1;
}}

/* ══ Chat / Conversation Area ════════════════════════════════════════════════ */

#conversation-area {{
    height: 1fr;
    width: 1fr;
}}

ConversationView {{
    height: 1fr;
    padding: 0;
}}

/* Message rows */
MessageRow {{
    padding: 0 2;
    margin-bottom: 1;
    width: 1fr;
}}
MessageRow.user {{
    border-left: thick {Colors.USER};
    background: {Colors.BG_RAISED};
}}
MessageRow.assistant {{
    border-left: thick {Colors.ASSISTANT};
}}
MessageRow.tool {{
    border-left: thick {Colors.WARNING};
    background: {Colors.BG_RAISED};
    padding: 0 2;
}}
MessageRow.system {{
    color: {Colors.TEXT_MUTED};
    padding: 0 2;
}}
MessageRow.error {{
    border-left: thick {Colors.ERROR};
    background: {Colors.ERROR_DIM};
}}

/* ── Context heatmap ────────────────────────────────────────────────────── */
#heatmap-strip {{
    height: 1;
    background: {Colors.BG_SURFACE};
    border-top: solid {Colors.BORDER_DIM};
    padding: 0 1;
}}

/* ── Input bar ──────────────────────────────────────────────────────────── */
#input-container {{
    height: auto;
    min-height: 3;
    max-height: 10;
    background: {Colors.BG_RAISED};
    border-top: solid {Colors.BORDER_FOCUS};
    padding: 0 1;
}}
#input-container:focus-within {{
    border-top: solid {Colors.ACCENT};
}}
#tui-input {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
    border: none;
    height: auto;
    min-height: 1;
    padding: 0;
}}
#input-hint {{
    height: 1;
    color: {Colors.TEXT_DIM};
}}

/* ══ Dashboard ═══════════════════════════════════════════════════════════════ */

DashboardScreen {{
    height: 1fr;
    width: 1fr;
    padding: 0 1;
}}
.dash-card {{
    border: solid {Colors.BORDER_DIM};
    background: {Colors.BG_SURFACE};
    padding: 1;
    margin: 0 0 1 0;
    height: auto;
}}
.dash-card .card-title {{
    color: {Colors.ACCENT_BRIGHT};
    text-style: bold;
    margin-bottom: 1;
}}
.dash-metric {{
    color: {Colors.TEXT_PRIMARY};
    text-style: bold;
}}
.dash-label {{
    color: {Colors.TEXT_MUTED};
}}

/* ══ Sessions Browser ════════════════════════════════════════════════════════ */

SessionBrowserScreen {{
    height: 1fr;
    padding: 0;
}}
.session-row {{
    height: 2;
    padding: 0 1;
    border-bottom: solid {Colors.BORDER_DIM};
}}
.session-row:hover {{
    background: {Colors.BG_RAISED};
}}
.session-row.selected {{
    background: {Colors.ACCENT_DIM};
}}
.session-status-done   {{ color: {Colors.SUCCESS}; }}
.session-status-running{{ color: {Colors.WARNING}; }}
.session-status-error  {{ color: {Colors.ERROR}; }}

/* ══ Memory Explorer ═════════════════════════════════════════════════════════ */

MemoryScreen {{
    height: 1fr;
    padding: 0 1;
}}
.memory-entry {{
    border: solid {Colors.BORDER_DIM};
    background: {Colors.BG_SURFACE};
    padding: 0 1;
    margin-bottom: 1;
    height: auto;
}}
.memory-score {{
    color: {Colors.WARNING};
}}

/* ══ Plugin Manager ══════════════════════════════════════════════════════════ */

PluginsScreen {{
    height: 1fr;
    padding: 0 1;
}}
.plugin-card {{
    border: solid {Colors.BORDER_DIM};
    background: {Colors.BG_SURFACE};
    padding: 1;
    margin-bottom: 1;
    height: auto;
}}
.plugin-installed {{
    color: {Colors.SUCCESS};
}}
.plugin-available {{
    color: {Colors.TEXT_MUTED};
}}

/* ══ Providers Screen ════════════════════════════════════════════════════════ */

ProvidersScreen {{
    height: 1fr;
    padding: 0 1;
}}
.provider-ok    {{ color: {Colors.SUCCESS}; }}
.provider-fail  {{ color: {Colors.ERROR}; }}

/* ══ Command Palette ═════════════════════════════════════════════════════════ */

CommandPaletteOverlay {{
    align: center top;
    background: {Colors.BG_OVERLAY};
    overlay: screen;
    width: 70;
    height: auto;
    max-height: 30;
    border: solid {Colors.ACCENT};
    margin-top: 2;
    padding: 0;
    display: none;
}}
CommandPaletteOverlay.visible {{
    display: block;
}}
#palette-input-wrapper {{
    background: {Colors.BG_RAISED};
    padding: 0 1;
    height: 3;
    border-bottom: solid {Colors.BORDER_ACTIVE};
}}
#palette-search {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
    border: none;
    height: 1;
    margin-top: 1;
    width: 1fr;
}}
#palette-results {{
    height: auto;
    max-height: 22;
    background: {Colors.BG_SURFACE};
}}
.palette-item {{
    height: 2;
    padding: 0 2;
    border-bottom: solid {Colors.BORDER_DIM};
    color: {Colors.TEXT_PRIMARY};
}}
.palette-item:hover, .palette-item.highlighted {{
    background: {Colors.ACCENT_DIM};
    color: {Colors.ACCENT_BRIGHT};
}}
.palette-item-label {{
    color: {Colors.TEXT_PRIMARY};
}}
.palette-item-hint {{
    color: {Colors.TEXT_DIM};
    text-align: right;
}}
.palette-group {{
    height: 1;
    padding: 0 2;
    background: {Colors.BG_DEEP};
    color: {Colors.TEXT_DIM};
    text-style: italic;
}}

/* ══ Approval Center ═════════════════════════════════════════════════════════ */

ApprovalOverlay {{
    align: center middle;
    background: {Colors.BG_OVERLAY};
    overlay: screen;
    width: 64;
    height: auto;
    border: thick {Colors.WARNING};
    padding: 1 2;
    display: none;
}}
ApprovalOverlay.visible {{
    display: block;
}}
.approval-title {{
    color: {Colors.WARNING};
    text-style: bold;
    height: 1;
}}
.approval-tool {{
    color: {Colors.TEXT_PRIMARY};
    text-style: bold;
    margin: 1 0;
}}
.approval-risk-low    {{ color: {Colors.SUCCESS}; }}
.approval-risk-medium {{ color: {Colors.WARNING}; }}
.approval-risk-high   {{ color: {Colors.ERROR}; text-style: bold; }}
.approval-desc {{
    color: {Colors.TEXT_MUTED};
    margin-bottom: 1;
    height: auto;
}}
#approval-buttons {{
    height: 3;
    margin-top: 1;
    align-horizontal: left;
}}

/* ══ Notification Banner ═════════════════════════════════════════════════════ */

NotificationBanner {{
    height: 1;
    background: {Colors.INFO};
    color: {Colors.TEXT_INVERSE};
    padding: 0 1;
    display: none;
    text-style: bold;
}}
NotificationBanner.visible {{ display: block; }}
NotificationBanner.success {{ background: {Colors.SUCCESS}; }}
NotificationBanner.warning {{ background: {Colors.WARNING}; }}
NotificationBanner.error   {{ background: {Colors.ERROR}; }}

/* ══ Job / Timeline Panel ════════════════════════════════════════════════════ */

.timeline-entry {{
    height: 2;
    padding: 0 1;
    border-bottom: solid {Colors.BORDER_DIM};
}}
.timeline-ts {{
    color: {Colors.TEXT_DIM};
    width: 8;
}}
.timeline-event {{
    color: {Colors.TEXT_PRIMARY};
    width: 1fr;
}}

/* ══ Metrics Panel ═══════════════════════════════════════════════════════════ */

.metric-block {{
    width: 1fr;
    height: 4;
    border: solid {Colors.BORDER_DIM};
    background: {Colors.BG_SURFACE};
    padding: 0 1;
    margin-right: 1;
}}
.metric-value {{
    color: {Colors.ACCENT_BRIGHT};
    text-style: bold;
}}
.metric-label {{
    color: {Colors.TEXT_MUTED};
}}

/* ══ Buttons ═════════════════════════════════════════════════════════════════ */

Button {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
    border: solid {Colors.BORDER_ACTIVE};
    margin-right: 1;
}}
Button:hover {{
    background: {Colors.ACCENT_DIM};
    border: solid {Colors.ACCENT};
    color: {Colors.ACCENT_BRIGHT};
}}
Button.-primary {{
    background: {Colors.ACCENT};
    color: white;
    border: solid {Colors.ACCENT};
}}
Button.-success {{
    background: {Colors.SUCCESS_DIM};
    color: {Colors.SUCCESS};
    border: solid {Colors.SUCCESS};
}}
Button.-danger {{
    background: {Colors.ERROR_DIM};
    color: {Colors.ERROR};
    border: solid {Colors.ERROR};
}}

/* ══ Input / TextArea ════════════════════════════════════════════════════════ */

Input {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
    border: solid {Colors.BORDER_DIM};
}}
Input:focus {{
    border: solid {Colors.ACCENT};
}}
TextArea {{
    background: {Colors.BG_RAISED};
    color: {Colors.TEXT_PRIMARY};
}}

/* ══ Loading / Empty States ══════════════════════════════════════════════════ */

.loading-indicator {{
    color: {Colors.TEXT_MUTED};
    text-align: center;
    padding: 2;
}}
.empty-state {{
    color: {Colors.TEXT_MUTED};
    text-align: center;
    padding: 4;
}}
.empty-state-icon {{
    color: {Colors.BORDER_ACTIVE};
    text-align: center;
    text-style: bold;
}}
.error-state {{
    color: {Colors.ERROR};
    background: {Colors.ERROR_DIM};
    padding: 1;
    border: solid {Colors.ERROR};
}}
.dash-section-title {{
    color: {Colors.ACCENT_BRIGHT};
    text-style: bold;
    height: 1;
    margin-bottom: 1;
    padding: 0 1;
}}

/* ══ DataTable ═══════════════════════════════════════════════════════════════ */

DataTable {{
    background: {Colors.BG_SURFACE};
    color: {Colors.TEXT_PRIMARY};
    border: none;
    height: 1fr;
}}
DataTable > .datatable--header {{
    background: {Colors.BG_DEEP};
    color: {Colors.ACCENT_BRIGHT};
    text-style: bold;
}}
DataTable > .datatable--cursor {{
    background: {Colors.ACCENT_DIM};
    color: {Colors.TEXT_PRIMARY};
}}
DataTable > .datatable--hover {{
    background: {Colors.BG_RAISED};
}}

/* ══ Progress / Bars ═════════════════════════════════════════════════════════ */

ProgressBar {{
    color: {Colors.ACCENT};
    background: {Colors.BG_RAISED};
}}
"""
