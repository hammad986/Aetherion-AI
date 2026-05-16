/* ═══════════════════════════════════════════════════════════════════════════
   NX Command Palette — Z22 Extracted Module
   Owns: palette state, focus trap, input wiring, keyboard ArrowUp/Down/Enter.
   Z19: full focus restoration on close.
   Z23: ARIA role=dialog, aria-modal, aria-label; keyboard trap inside palette.
   Extracted from ui.js — removes that code from the monolith.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxCommandPalette() {
  'use strict';

  /* ── Palette command registry ──────────────────────────────────────────── */
  const _ITEMS = [
    { icon: '▶',  label: 'Run Task',       hint: 'Ctrl+Enter', action: () => { if (typeof window.nxQueueTask === 'function') window.nxQueueTask(); } },
    { icon: '📋', label: 'View Logs',      hint: '',           action: () => window.nxSetTab?.('logs') },
    { icon: '👁',  label: 'Preview App',   hint: '',           action: () => window.nxSetTab?.('preview') },
    { icon: '📁', label: 'Code Editor',   hint: '',           action: () => window.nxSetTab?.('code') },
    { icon: '💻', label: 'Terminal',       hint: '',           action: () => window.nxSetTab?.('terminal') },
    { icon: '📊', label: 'Metrics',        hint: '',           action: () => window.nxSetTab?.('metrics') },
    { icon: '🤖', label: 'Agent State',    hint: '',           action: () => window.nxSetTab?.('agents') },
    { icon: '📅', label: 'Timeline',       hint: '',           action: () => window.nxSetTab?.('timeline') },
    { icon: '🧐', label: 'Step Trace',     hint: '',           action: () => window.nxSetTab?.('steps') },
    { icon: '⚙',  label: 'Settings',       hint: '',           action: () => { if (typeof window.openSettings === 'function') window.openSettings(); } },
    { icon: '📂', label: 'Sessions',       hint: '',           action: () => window.nxOpenPanel?.('sessions') },
    { icon: '🧹', label: 'Clear Memory',   hint: '',           action: () => { if (typeof window.clearAgentMemory === 'function') window.clearAgentMemory(); } },
    { icon: '💾', label: 'Save File',      hint: 'Ctrl+S',    action: () => { if (typeof window.saveCurrentFile === 'function') window.saveCurrentFile(); } },
    { icon: '⬇',  label: 'Download Project', hint: '',        action: () => { if (typeof window.downloadProject === 'function') window.downloadProject(); } },
    { icon: '🔍', label: 'Perf HUD',       hint: '',           action: () => { if (typeof window.nxPerfHUD === 'function') window.nxPerfHUD(); } },
  ];

  /* Allow external code to register palette items */
  function register(item) { _ITEMS.push(item); }

  /* ── State ─────────────────────────────────────────────────────────────── */
  let _selected = 0;
  let _filtered = [..._ITEMS];
  let _lastFocus = null;  // Z19 focus restoration

  /* ── Render ────────────────────────────────────────────────────────────── */
  function _render(q) {
    const list = document.getElementById('nxPaletteList');
    if (!list) return;
    _filtered = q
      ? _ITEMS.filter(i => i.label.toLowerCase().includes(q.toLowerCase()))
      : _ITEMS;
    if (!_filtered.length) {
      list.innerHTML = '<div class="nx-palette-empty" role="status">No commands found</div>';
      return;
    }
    list.innerHTML = _filtered.map((item, i) =>
      `<div class="nx-palette-item${i === _selected ? ' selected' : ''}"
            role="option"
            aria-selected="${i === _selected}"
            tabindex="-1"
            onclick="window._NxPalette.runItem(${i})">
        <span class="nx-palette-item-icon" aria-hidden="true">${item.icon}</span>
        <span class="nx-palette-item-label">${item.label}</span>
        ${item.hint ? `<span class="nx-palette-item-hint"><kbd class="nx-kbd">${item.hint}</kbd></span>` : ''}
      </div>`
    ).join('');
  }

  /* ── Open / close ──────────────────────────────────────────────────────── */
  function open() {
    _lastFocus = document.activeElement;  // Z19 capture
    const backdrop = document.getElementById('nxPalette');
    const input = document.getElementById('nxPaletteInput');
    if (backdrop) {
      backdrop.classList.add('open');
      backdrop.setAttribute('aria-hidden', 'false');
    }
    if (input) {
      input.value = '';
      input.focus();
    }
    _selected = 0;
    _render('');
    window.NxBus?.emit('paletteOpen', {});
    // Z23: announce to screen reader
    _announce('Command palette open. ' + _ITEMS.length + ' commands available.');
  }

  function _restoreFocus() {
    if (_lastFocus && typeof _lastFocus.focus === 'function') {
      _lastFocus.focus();
      _lastFocus = null;
    }
  }

  function close(e) {
    if (e && e.target !== document.getElementById('nxPalette')) return;
    _closeClean();
  }

  function forceClose() {
    _closeClean();
  }

  function _closeClean() {
    const pal = document.getElementById('nxPalette');
    if (pal) {
      pal.classList.remove('open');
      pal.setAttribute('aria-hidden', 'true');
    }
    _restoreFocus();
    window.NxBus?.emit('paletteClose', {});
  }

  function runItem(i) {
    _closeClean();
    _filtered[i]?.action?.();
  }

  /* ── Z23: Live region announcement ─────────────────────────────────────── */
  function _announce(msg) {
    let ar = document.getElementById('nxPaletteAnnounce');
    if (!ar) {
      ar = document.createElement('div');
      ar.id = 'nxPaletteAnnounce';
      ar.setAttribute('aria-live', 'assertive');
      ar.setAttribute('aria-atomic', 'true');
      ar.className = 'nx-sr-only';
      document.body.appendChild(ar);
    }
    ar.textContent = '';
    requestAnimationFrame(() => { ar.textContent = msg; });
  }

  /* ── Input wiring (runs after DOM is ready) ────────────────────────────── */
  function _wireInput() {
    const pinput = document.getElementById('nxPaletteInput');
    if (!pinput) return;

    pinput.addEventListener('input', e => {
      _selected = 0;
      _render(e.target.value);
    });

    pinput.addEventListener('keydown', e => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          _selected = Math.min(_selected + 1, _filtered.length - 1);
          _render(pinput.value);
          break;
        case 'ArrowUp':
          e.preventDefault();
          _selected = Math.max(0, _selected - 1);
          _render(pinput.value);
          break;
        case 'Enter':
          e.preventDefault();
          e.stopPropagation();
          runItem(_selected);
          break;
        case 'Escape':
          _closeClean();
          break;
        case 'Tab':
          // Z23: keep focus inside palette (trap)
          e.preventDefault();
          break;
      }
    });
  }

  /* ── ARIA setup on palette backdrop ────────────────────────────────────── */
  function _setupAria() {
    const pal = document.getElementById('nxPalette');
    if (!pal) return;
    pal.setAttribute('role', 'dialog');
    pal.setAttribute('aria-modal', 'true');
    pal.setAttribute('aria-label', 'Command palette');
    pal.setAttribute('aria-hidden', 'true');

    const listbox = document.getElementById('nxPaletteList');
    if (listbox) {
      listbox.setAttribute('role', 'listbox');
      listbox.setAttribute('aria-label', 'Commands');
    }

    const input = document.getElementById('nxPaletteInput');
    if (input) {
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-haspopup', 'listbox');
      input.setAttribute('aria-controls', 'nxPaletteList');
    }
  }

  /* ── Module init ────────────────────────────────────────────────────────── */
  function _init() {
    _setupAria();
    _wireInput();
    console.log('[NxPalette] Command palette module ready');
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window._NxPalette = { open, close, forceClose, runItem, register };

  // Expose on window for legacy callers (HTML onclick, keyboard module, etc.)
  window.nxOpenPalette   = open;
  window.nxClosePalette  = close;
  window.nxForcePaletteClose = forceClose;
  window.nxRunPaletteItem = runItem;

  // Wire input after DOM ready
  if (typeof window.NX_BOOT_TASKS !== 'undefined') {
    window.NX_BOOT_TASKS.push(_init);
  } else {
    document.addEventListener('DOMContentLoaded', _init);
  }

})();
