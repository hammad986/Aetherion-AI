/**
 * nx-state.js — Nexora Canonical State Governance v1
 * ══════════════════════════════════════════════════════════════════════
 * Three canonical state slices. Each slice owns its data exclusively.
 *
 * GOVERNANCE:
 *   - WorkspaceState  → NxWorkspace owns. Persisted to localStorage.
 *   - RuntimeState    → runtime.js owns. Transient. Never persisted.
 *   - UIState         → ui.js owns. Partially persisted (preferences).
 *
 *   No module may mutate another module's slice directly.
 *   All mutations go through the slice's set() method.
 *   Cross-module reads use NxState.get(slice, key).
 *   Cross-module updates use NxBus.emit(EVENTS.*).
 *
 * Ownership: frontend/architecture
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Persistence helpers ─────────────────────────────────────────── */
  const _store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; }
      catch(_) { return fallback; }
    },
    set(key, val) {
      try { localStorage.setItem(key, JSON.stringify(val)); } catch(_) {}
    },
    del(key) {
      try { localStorage.removeItem(key); } catch(_) {}
    },
  };

  /* ── Slice factory ───────────────────────────────────────────────── */
  function _makeSlice(name, defaults, persistKey) {
    let _data = { ...defaults };

    // Load persisted data if applicable
    if (persistKey) {
      const saved = _store.get(persistKey, {});
      // Only restore keys that exist in defaults (schema guard)
      Object.keys(defaults).forEach(k => {
        if (k in saved && typeof saved[k] === typeof defaults[k]) _data[k] = saved[k];
      });
    }

    function get(key) { return key ? _data[key] : { ..._data }; }

    function set(patch) {
      const changed = [];
      Object.keys(patch).forEach(k => {
        if (!(k in _data)) {
          console.warn(`[NxState:${name}] Unknown key "${k}" — ignored`);
          return;
        }
        if (_data[k] !== patch[k]) { _data[k] = patch[k]; changed.push(k); }
      });
      if (!changed.length) return;
      if (persistKey) _store.set(persistKey, _data);
      // Notify via bus if available
      if (window.NxBus) {
        window.NxBus.emit(`nx:state:${name}:changed`, { keys: changed, state: { ..._data } });
      }
    }

    function reset() {
      _data = { ...defaults };
      if (persistKey) _store.del(persistKey);
    }

    function snapshot() { return { ..._data }; }

    return { get, set, reset, snapshot, _name: name };
  }

  /* ══ Slice 1: WorkspaceState (persisted) ════════════════════════════
     Owner: workspace.js / NxWorkspace
     Persistent: yes (nx_ws_state_v1)
  ════════════════════════════════════════════════════════════════════ */
  const WorkspaceState = _makeSlice('workspace', {
    leftOpen:    false,
    rightOpen:   true,
    bottomOpen:  false,
    leftW:       0,
    rightW:      290,
    bottomH:     200,
    activePreset: null,
    closedTabs:   [],
    activeTab:   'logs',
  }, 'nx_ws_state_v1');

  /* ══ Slice 2: RuntimeState (transient) ══════════════════════════════
     Owner: runtime.js / agent execution
     Persistent: NO — lost on page load by design
  ════════════════════════════════════════════════════════════════════ */
  const RuntimeState = _makeSlice('runtime', {
    agentStatus:    'idle',    // 'idle'|'running'|'planning'|'thinking'|'done'|'error'|'stopped'
    activeSid:      null,
    streamOpen:     false,
    model:          '',
    provider:       '',
    tokensUsed:     0,
    costEstimate:   0,
    wsState:        'disconnected', // 'connected'|'disconnected'|'error'|'connecting'
    lastError:      null,
    taskLabel:      '',
    planMode:       'elite',
  }, null /* NOT persisted */);

  /* ══ Slice 3: UIState (partially persisted) ══════════════════════════
     Owner: ui.js
     Persistent: preferences only (nx_ui_prefs_v1)
  ════════════════════════════════════════════════════════════════════ */
  const UIState = _makeSlice('ui', {
    /* Preferences (persisted) */
    theme:          'dark',
    reducedMotion:  false,
    /* Transient UI state — NOT in defaults, set separately */
    paletteOpen:    false,
    settingsOpen:   false,
    modalStack:     [],
  }, 'nx_ui_prefs_v1');

  /* ── Central NxState namespace ───────────────────────────────────── */
  const NxState = {
    workspace: WorkspaceState,
    runtime:   RuntimeState,
    ui:        UIState,

    /** Cross-slice read: NxState.get('runtime', 'agentStatus') */
    get(slice, key) {
      const s = this[slice];
      if (!s) { console.warn(`[NxState] Unknown slice "${slice}"`); return undefined; }
      return s.get(key);
    },

    /** Export full state snapshot for diagnostics */
    snapshot() {
      return {
        workspace: WorkspaceState.snapshot(),
        runtime:   RuntimeState.snapshot(),
        ui:        UIState.snapshot(),
        ts:        Date.now(),
      };
    },

    /** Hard reset all slices (dev/test use) */
    resetAll() {
      WorkspaceState.reset();
      RuntimeState.reset();
      UIState.reset();
    },
  };

  window.NxState = NxState;

  /* ── Wire NxState into existing NX global ───────────────────────── */
  function _bridge() {
    const nx = window.NX;
    if (!nx) return;

    // Sync runtime.js's NX.lastStatus → RuntimeState
    const origSetStatus = window.nxSetStatus;
    if (typeof origSetStatus === 'function') {
      window.nxSetStatus = function(status, detail) {
        origSetStatus(status, detail);
        NxState.runtime.set({ agentStatus: status, taskLabel: detail || '' });
        if (window.NxBus) NxBus.emit(NxBus.EVENTS.AGENT_STATUS, { status, detail });
      };
    }

    // Sync NxWorkspace state changes → WorkspaceState
    if (window.NxWorkspace) {
      const origToggleLeft  = NxWorkspace.toggleLeft.bind(NxWorkspace);
      const origToggleRight = NxWorkspace.toggleRight.bind(NxWorkspace);
      NxWorkspace.toggleLeft  = function() { origToggleLeft();  _syncWorkspace(); };
      NxWorkspace.toggleRight = function() { origToggleRight(); _syncWorkspace(); };
    }

    // Expose diagnostic helper
    nx.stateSnapshot = () => NxState.snapshot();
  }

  function _syncWorkspace() {
    if (!window.NxWorkspace) return;
    const s = NxWorkspace.getState();
    NxState.workspace.set({
      leftOpen:  s.leftOpen  || false,
      rightOpen: s.rightOpen || false,
      bottomOpen:s.bottomOpen|| false,
      leftW:     s.leftW     || 0,
      rightW:    s.rightW    || 0,
    });
  }

  /* ── Boot integration ────────────────────────────────────────────── */
  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_bridge);
  } else {
    document.addEventListener('DOMContentLoaded', _bridge);
  }
})();
