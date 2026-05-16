# Z23 — Screen Reader Compatibility Report

**Date:** 2026-05-16  
**Phase:** Z23 — Accessibility & Keyboard Operations Hardening

---

## 1. Live Regions Installed

| Region ID | Element | Level | Atomic | Content |
|-----------|---------|-------|--------|---------|
| `#nxExecAnnounce` | `div.nx-sr-only` | `polite` | Yes | Exec state changes: "Task started", "Task completed", "Error" |
| `#nxPaletteAnnounce` | `div.nx-sr-only` | `assertive` | Yes | Palette events: "Command palette open. N commands available" |
| `#nxModalAnnounce` | `div.nx-sr-only` | `polite` | Yes | Modal events: "Settings dialog opened", "Dialog closed" |
| `#nxSbStatusSr` | `div.nx-sr-only` | `polite` | Yes | Manual status updates |
| `.nx-log-trimmed-notice` | visible div | `polite` | No | "N older lines trimmed" |
| `#nxActivityBar` | visible div | `polite` | No | Agent activity status |
| `#nxTab-logs` | visible log panel | `polite` | No | Live log output |

**Announcement discipline:**
- `assertive` used only for command palette open (time-sensitive context switch)
- All other announcements use `polite` to avoid interrupting user speech
- Each region is cleared before setting new content to ensure VoiceOver re-reads it

---

## 2. Dialog / Modal Semantics

### Settings Modal
```html
<!-- Applied by nx-modal-system.js on open -->
<div id="settingsBackdrop"
     role="dialog"
     aria-modal="true"
     aria-label="Settings">
```
- Focus trapped: `Tab` / `Shift+Tab` cycle through modal focusable elements
- Focus restored: trigger element `.focus()` on close
- Announced: "Settings dialog opened" (polite)
- Closed: "Dialog closed" (polite)

### Command Palette
```html
<!-- Applied by nx-command-palette.js -->
<div id="nxPalette"
     role="dialog"
     aria-modal="true"
     aria-label="Command palette"
     aria-hidden="true|false">
  <input role="combobox"
         aria-autocomplete="list"
         aria-haspopup="listbox"
         aria-controls="nxPaletteList">
  <div id="nxPaletteList"
       role="listbox"
       aria-label="Commands">
    <div role="option" aria-selected="true|false">...</div>
  </div>
</div>
```
- Announced: "Command palette open. 15 commands available."
- Items announced as selected via `aria-selected` update
- Combobox + listbox pattern follows WAI-ARIA 1.2 Combobox pattern

### Uncertainty / Confirmation Modal
```html
<!-- Already in HTML -->
<div id="uncertaintyModal"
     role="dialog"
     aria-modal="true"
     aria-labelledby="uncertaintyQuestion">
```
- Has `aria-labelledby` pointing to the question heading — correct pattern

---

## 3. Tablist / Tab Panel Semantics

```html
<div id="nxTabBar" role="tablist" aria-label="Workspace panels">
  <button class="nx-tab active"
          role="tab"
          aria-selected="true"
          aria-controls="nxTab-logs"
          data-nxtab="logs">Logs</button>
  <!-- ... other tabs -->
</div>
<div id="nxTab-logs"
     role="log"
     aria-live="polite"
     aria-label="Execution output">
  <!-- live log content -->
</div>
```

**Announcement on tab switch:** aria-selected changes from `"false"` to `"true"` on the activated button. Most screen readers announce the tab name + "tab, selected" automatically.

---

## 4. Button Semantics

All topbar and nav rail buttons have `aria-label` attributes providing descriptive labels even when the visible text is icon-only:

| Button | `aria-label` | `aria-pressed` |
|--------|-------------|--------------|
| Run button | "Execute task" / "Stop task execution" | `"false"` / `"true"` (Z23) |
| Stop button | "Stop execution" | — |
| Model button | "Model and API configuration" | — |
| Palette trigger | "Open command palette (Ctrl+K)" | — |
| Inspector button | "Toggle inspector panel" | — |
| Settings button | "Open settings" | — |
| Nav: Files | "Files panel" | — |
| Nav: Chat | "Chat panel" | — |
| Nav: History | "Session history" | — |
| Nav: Settings | "Settings panel" | — |
| Nav: Left toggle | "Toggle navigation panel" | — |

---

## 5. Skip Navigation

Screen readers can use the skip nav link to bypass the topbar, nav rail, and left panel:

1. User presses `Tab` — first focusable = "Skip to main content"
2. User presses `Enter` — focus jumps to `#nxMainContent` (the main workspace area)
3. User can immediately interact with tabs, log viewer, or composer

---

## 6. Screen-Reader Behaviour Map

| User action | Screen reader output |
|-------------|---------------------|
| Tab to active tab | "Logs, tab, selected" |
| Arrow to another tab | Tab name + activates tab |
| Open palette (Ctrl+K) | "Command palette open. 15 commands available." [assertive] |
| Type in palette | Filtered results update (not announced per-char) |
| Arrow through palette | Role="option" each item described |
| Select palette item | "Run Task" → closes → trigger focus restored |
| Open settings | "Settings dialog opened" [polite] |
| Close settings | "Dialog closed" [polite] |
| Task starts | "Task started" [polite] |
| Task completes | "Task completed" [polite] |
| Task errors | "Task encountered an error" [polite] |
| SSE reconnecting | "reconnecting…" appended to status text |
| Log trim | "N older lines trimmed" visible + polite |

---

## 7. Known Screen-Reader Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| Monaco editor | Monaco provides its own textarea overlay for reading | Accept; Monaco's own a11y is adequate |
| xterm.js terminal | Not fully screen-reader accessible | Plan for future phase: add terminal output live region |
| Log area verbosity | `aria-live="polite"` on a high-volume log area may interrupt | Resolved by `MAX_LOG_LINES` ceiling; user can navigate logically |
| Nav rail sub-panels | Some sub-panels lack `aria-expanded` | Minor; panels are visually obvious and can be Tab-navigated |
