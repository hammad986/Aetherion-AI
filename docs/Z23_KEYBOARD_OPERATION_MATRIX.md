# Z23 — Keyboard Operation Matrix

**Date:** 2026-05-16  
**Phase:** Z23 — Accessibility & Keyboard Operations Hardening

Complete mapping of every keyboard-accessible operation in the Nexora AI UI.

---

## 1. Global Shortcuts (work everywhere)

| Shortcut | Action | Owner module | Context restriction |
|----------|--------|-------------|-------------------|
| `Ctrl+Enter` | Run / stop task | `nx-keyboard-shortcuts.js` | None |
| `Ctrl+K` | Open command palette | `nx-keyboard-shortcuts.js` | None |
| `Ctrl+,` | Open settings | `nx-keyboard-shortcuts.js` | Not in text input |
| `Escape` | Dismiss topmost overlay | `nx-keyboard-shortcuts.js` | None |
| `Ctrl+Shift+E` | Toggle left panel | `nx-keyboard-shortcuts.js` | Not in text input |
| `Ctrl+Shift+I` | Toggle right panel (Inspector) | `nx-keyboard-shortcuts.js` | Not in text input |
| `Ctrl+S` | Save current file | `nx-keyboard-shortcuts.js` | Code tab only |

### Escape priority order
1. Command palette (if open) — closes and restores focus
2. Settings modal (if open) — closes and restores focus
3. Workspace drawer (if open) — closes
4. `nxCloseMore()` — closes any remaining overlays

---

## 2. Tab Navigation

| Shortcut | Action | Owner |
|----------|--------|-------|
| `→` | Next tab | `nx-tab-manager.js` |
| `←` | Previous tab | `nx-tab-manager.js` |
| `Home` | First tab | `nx-tab-manager.js` |
| `End` | Last tab | `nx-tab-manager.js` |
| `Enter` / `Space` | Activate focused tab | Browser default |
| `Tab` | Leave tablist, continue to next focusable | Browser default |

**Pattern:** WAI-ARIA Tablist Keyboard Pattern (roving tabindex not implemented — direct focus management)

---

## 3. Command Palette

| Shortcut | Action | Owner |
|----------|--------|-------|
| `Ctrl+K` | Open palette | `nx-keyboard-shortcuts.js` |
| `Type` | Filter commands | `nx-command-palette.js` |
| `↓` | Select next item | `nx-command-palette.js` |
| `↑` | Select previous item | `nx-command-palette.js` |
| `Enter` | Execute selected command | `nx-command-palette.js` |
| `Escape` | Close, restore focus | `nx-keyboard-shortcuts.js` |
| `Tab` | Trapped (stays in palette) | `nx-command-palette.js` |

**Focus management:** Trigger element focus is captured before open; restored on all close paths.

---

## 4. Settings Modal

| Shortcut | Action | Owner |
|----------|--------|-------|
| `Ctrl+,` | Open settings | `nx-keyboard-shortcuts.js` |
| `Tab` | Move to next focusable in modal | `nx-modal-system.js` (trap) |
| `Shift+Tab` | Move to previous focusable | `nx-modal-system.js` (trap) |
| `Escape` | Close modal, restore focus | `nx-keyboard-shortcuts.js` |

**Focus trap:** Focus is trapped inside the modal. Tabbing past the last element wraps to the first, and vice versa.

---

## 5. Nav Rail

| Shortcut | Action |
|----------|--------|
| `Tab` | Move between nav rail buttons |
| `Enter` / `Space` | Activate panel |
| `Escape` | (panel closes if open) |

**ARIA:** Each button has `aria-label` describing its panel. Active state communicated via `aria-expanded` or visual `.active` class.

---

## 6. Execution Controls

| Shortcut | Action | Notes |
|----------|--------|-------|
| `Ctrl+Enter` | Run task | Works even in text areas |
| `Ctrl+Enter` | Stop task (if running) | Toggle |
| `Tab` to Run button, `Enter` | Run task | Standard button activation |
| `Tab` to Stop button, `Enter` | Stop task | Standard button activation |

---

## 7. Composer / Task Input

| Shortcut | Action |
|----------|--------|
| `Tab` | Move to task input |
| `Type` | Enter task description |
| `Ctrl+Enter` | Submit and run |

**Context note:** `Ctrl+S` and `Ctrl+,` do not fire when focus is inside `<input>`, `<textarea>`, or `contenteditable` — this is intentional (Z23 text input context detection in nx-keyboard-shortcuts.js).

---

## 8. Log Viewer

| Shortcut | Action |
|----------|--------|
| `Tab` to log area | Reach log area |
| `↑` / `↓` | Scroll (browser-native for overflow containers) |
| `Page Up` / `Page Down` | Scroll faster |

**ARIA:** Log area has `role="log"` + `aria-live="polite"`. New log lines are announced by screen readers.

---

## 9. Terminal

| Shortcut | Action |
|----------|--------|
| `Tab` | Move to terminal input |
| `Type` | Enter command |
| `Enter` | Execute command |
| `↑` / `↓` | Command history (xterm.js native) |

---

## 10. Inspector / Collapsible Sections

| Shortcut | Action |
|----------|--------|
| `Tab` | Move to collapsible label |
| `Enter` / `Space` | Toggle collapse |

**ARIA:** Collapsible labels use `cursor: pointer` and `display: flex` (via `.nx-insp-collapsible-label`). Future improvement: add `aria-expanded` on these controls.

---

## 11. Custom Shortcut Registration

External modules can register additional shortcuts via `NxKeyboard.register()`:

```javascript
// Register a custom shortcut
window.NxKeyboard.register('ctrl+shift+l', function() {
  window.nxSetTab('logs');
}, 'MyPlugin', 'Go to logs tab');

// List all shortcuts
console.table(window.NxKeyboard.list());
```

---

## 12. Focus Visibility Reference

All interactive elements produce a visible `2px solid var(--purple, #bc8cff)` focus ring with `outline-offset: 3px` when navigated by keyboard (`focus-visible`). Mouse-click navigation does NOT produce a visible ring.
