/* ═══════════════════════════════════════════════════════════════════════════
   NX Keyboard Shortcuts — Z22 Extracted Module
   Owns: the document keydown listener, all global shortcuts.
   Provides: `NxKeyboard.register(combo, handler, owner)` for module-owned shortcuts.
   Z23: skips shortcuts when focus is in contenteditable / input / textarea
        where the keypress is intentional user text entry.
   Extracted from ui.js — removes that code from the monolith.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxKeyboardShortcuts() {
  'use strict';

  /* ── Custom shortcut registry ─────────────────────────────────────────── */
  /* { combo: 'ctrl+k', handler: fn, owner: 'module', description: '' } */
  const _shortcuts = [];

  function register(combo, handler, owner, description) {
    _shortcuts.push({ combo: combo.toLowerCase(), handler, owner: owner || 'global', description: description || '' });
  }

  function unregister(owner) {
    const idx = _shortcuts.findIndex(s => s.owner === owner);
    if (idx > -1) _shortcuts.splice(idx, 1);
  }

  /* ── Focus context detection (Z23) ──────────────────────────────────────── */
  function _inTextInput(target) {
    if (!target) return false;
    const tag = target.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    if (target.isContentEditable) return true;
    // Monaco editor uses a textarea overlay
    if (target.classList && target.classList.contains('inputarea')) return true;
    return false;
  }

  /* ── Core keydown handler ─────────────────────────────────────────────── */
  function _onKeydown(e) {
    const target = e.target || document.activeElement;

    // ── Ctrl+Enter — run task (works even in text inputs via explicit check)
    if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key === 'Enter') {
      e.preventDefault();
      if (typeof window.nxRunOrStop === 'function') window.nxRunOrStop();
      return;
    }

    // ── Ctrl+K — command palette (works everywhere)
    if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key === 'k') {
      e.preventDefault();
      if (typeof window.nxOpenPalette === 'function') window.nxOpenPalette();
      return;
    }

    // ── Escape — dismiss topmost overlay
    if (e.key === 'Escape') {
      const pal = document.getElementById('nxPalette');
      if (pal && pal.classList.contains('open')) {
        if (typeof window.nxForcePaletteClose === 'function') window.nxForcePaletteClose();
        return;
      }
      if (typeof window.nxCloseMore === 'function') window.nxCloseMore();
      const wsDrawer = document.getElementById('nxWorkspaceDrawer');
      if (wsDrawer && wsDrawer.classList.contains('open')) {
        if (typeof window.nxWsDrawerClose === 'function') window.nxWsDrawerClose();
      }
      // Close settings modal if open
      if (typeof window.closeSettings === 'function') {
        const modal = document.getElementById('settingsBackdrop') || document.getElementById('settingsModal');
        if (modal && (modal.style.display === 'flex' || modal.classList.contains('open'))) {
          window.closeSettings();
        }
      }
      return;
    }

    // Remaining shortcuts skip when user is typing in an input/editor
    if (_inTextInput(target)) return;

    // ── Ctrl+, — settings
    if (e.ctrlKey && !e.shiftKey && e.key === ',') {
      e.preventDefault();
      if (typeof window.openSettings === 'function') window.openSettings();
      return;
    }

    // ── Ctrl+S — save file (code tab only)
    if (e.ctrlKey && !e.shiftKey && e.key === 's' && window.NX?.activeTab === 'code') {
      e.preventDefault();
      if (typeof window.saveCurrentFile === 'function') window.saveCurrentFile();
      return;
    }

    // ── Ctrl+Shift+E — toggle left panel
    if (e.ctrlKey && e.shiftKey && e.key === 'E') {
      e.preventDefault();
      if (typeof window.NxWorkspace !== 'undefined') window.NxWorkspace.toggleLeft();
      else if (typeof window.nxToggleLeft === 'function') window.nxToggleLeft();
      return;
    }

    // ── Ctrl+Shift+I — toggle right panel (inspector)
    if (e.ctrlKey && e.shiftKey && e.key === 'I') {
      e.preventDefault();
      if (typeof window.NxWorkspace !== 'undefined') window.NxWorkspace.toggleRight();
      else if (typeof window.nxToggleRight === 'function') window.nxToggleRight();
      return;
    }

    // ── Process registered custom shortcuts ──────────────────────────────
    for (const s of _shortcuts) {
      if (_matchesCombo(e, s.combo)) {
        e.preventDefault();
        try { s.handler(e); } catch (err) {
          console.warn('[NxKeyboard] Shortcut handler error:', err);
        }
        return;
      }
    }
  }

  function _matchesCombo(e, combo) {
    const parts = combo.split('+');
    const key = parts[parts.length - 1];
    const ctrl = parts.includes('ctrl');
    const shift = parts.includes('shift');
    const alt = parts.includes('alt');
    return e.ctrlKey === ctrl && e.shiftKey === shift && e.altKey === alt
        && e.key.toLowerCase() === key;
  }

  /* ── List all shortcuts (for help screen) ──────────────────────────────── */
  function list() {
    return [
      { combo: 'Ctrl+Enter',   description: 'Run / stop task',         owner: 'NxKeyboard' },
      { combo: 'Ctrl+K',       description: 'Open command palette',    owner: 'NxKeyboard' },
      { combo: 'Ctrl+,',       description: 'Open settings',           owner: 'NxKeyboard' },
      { combo: 'Ctrl+S',       description: 'Save current file',       owner: 'NxKeyboard' },
      { combo: 'Ctrl+Shift+E', description: 'Toggle left panel',       owner: 'NxKeyboard' },
      { combo: 'Ctrl+Shift+I', description: 'Toggle right panel',      owner: 'NxKeyboard' },
      { combo: 'Escape',       description: 'Dismiss overlay / modal', owner: 'NxKeyboard' },
      ..._shortcuts.map(s => ({ combo: s.combo, description: s.description, owner: s.owner })),
    ];
  }

  /* ── Module init ────────────────────────────────────────────────────────── */
  document.addEventListener('keydown', _onKeydown);
  console.debug('[NxKeyboard] ready');

  window.NxKeyboard = { register, unregister, list };

})();
