/**
 * nx-bus.js — Aetherion Canonical Event Bus v1
 * ══════════════════════════════════════════════════════════════════════
 * Architecture: typed pub/sub bus. Replaces direct window.* coupling.
 *
 * GOVERNANCE:
 *   - All cross-module communication MUST go through NxBus.
 *   - Direct window.someFunction() calls from foreign modules are banned.
 *   - Every listener MUST be cleaned up via NxBus.off() or the returned
 *     unsubscribe function to prevent memory leaks.
 *   - Duplicate listener detection is on by default in dev mode.
 *
 * CANONICAL EVENT NAMES (use these constants, never raw strings):
 *   NxBus.EVENTS.* — see registry below.
 *
 * Ownership: frontend/architecture
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Canonical event registry ────────────────────────────────────── */
  const EVENTS = Object.freeze({
    /* Boot lifecycle */
    BOOT_START:        'nx:boot:start',
    BOOT_READY:        'nx:boot:ready',
    BOOT_DEGRADED:     'nx:boot:degraded',
    BOOT_ERROR:        'nx:boot:error',

    /* Agent / runtime */
    AGENT_START:       'nx:agent:start',
    AGENT_STOP:        'nx:agent:stop',
    AGENT_DONE:        'nx:agent:done',
    AGENT_ERROR:       'nx:agent:error',
    AGENT_STATUS:      'nx:agent:status',      // {status, detail}

    /* Streaming */
    STREAM_OPEN:       'nx:stream:open',
    STREAM_CHUNK:      'nx:stream:chunk',       // {text}
    STREAM_CLOSE:      'nx:stream:close',
    STREAM_ERROR:      'nx:stream:error',       // {error}

    /* Activity timeline */
    ACTIVITY_EVENT:    'nx:activity:event',     // {type, label, detail}
    ACTIVITY_CLEAR:    'nx:activity:clear',

    /* Workspace */
    WORKSPACE_LAYOUT:  'nx:workspace:layout',   // {leftW, rightW, bottomH, ...}
    WORKSPACE_PRESET:  'nx:workspace:preset',   // {id}
    WORKSPACE_SNAPSHOT:'nx:workspace:snapshot', // {action:'save'|'restore', id}
    TAB_CHANGE:        'nx:tab:change',         // {tab}
    TAB_OPEN:          'nx:tab:open',           // {tab}
    TAB_CLOSE:         'nx:tab:close',          // {tab}
    PANEL_TOGGLE:      'nx:panel:toggle',       // {panel:'left'|'right'|'bottom'}

    /* UI */
    TOAST:             'nx:toast',              // {msg, type, opts}
    MODAL_OPEN:        'nx:modal:open',         // {id}
    MODAL_CLOSE:       'nx:modal:close',        // {id}
    SETTINGS_OPEN:     'nx:settings:open',
    PALETTE_OPEN:      'nx:palette:open',
    PALETTE_CLOSE:     'nx:palette:close',

    /* Inspector */
    INSPECTOR_MODE:    'nx:inspector:mode',     // {mode}

    /* Persistence */
    STATE_SAVE:        'nx:state:save',         // {key, data}
    STATE_LOAD:        'nx:state:load',         // {key}

    /* Network */
    WS_STATUS:         'nx:ws:status',          // {state:'connected'|'disconnected'|'error'}
    API_ERROR:         'nx:api:error',          // {status, url, msg}
    API_PLAN_LOCKED:   'nx:api:plan_locked',    // {url}
    API_RATE_LIMITED:  'nx:api:rate_limited',   // {url}

    /* Session */
    SESSION_CREATED:   'nx:session:created',    // {sid}
    SESSION_RESTORED:  'nx:session:restored',   // {sid}
    SESSION_CLEARED:   'nx:session:cleared',
  });

  /* ── Bus core ────────────────────────────────────────────────────── */
  const _listeners = new Map();   // event → Set<{fn, owner, once}>
  const _history   = [];           // last N events for debug
  const MAX_HIST   = 500;  // Increased for post-mortem debugging of long agent runs
  const _dev       = window.location.hostname === 'localhost' ||
                     window.location.search.includes('nx_debug');

  function _getSet(event) {
    if (!_listeners.has(event)) _listeners.set(event, new Set());
    return _listeners.get(event);
  }

  /**
   * Subscribe to an event.
   * @param {string} event - Use NxBus.EVENTS.* constant
   * @param {function} fn  - Handler fn(data, event)
   * @param {object}  [opts]
   * @param {string}  [opts.owner]  - Module name for leak detection
   * @param {boolean} [opts.once]   - Auto-remove after first call
   * @returns {function} unsubscribe — call to remove this listener
   */
  function on(event, fn, opts = {}) {
    if (typeof fn !== 'function') throw new TypeError(`NxBus.on: handler must be a function (event: ${event})`);
    const set = _getSet(event);

    // Duplicate detection in dev mode
    if (_dev) {
      for (const entry of set) {
        if (entry.fn === fn) {
          console.warn(`[NxBus] Duplicate listener on "${event}" from owner "${opts.owner || 'unknown'}"`);
        }
      }
    }

    const entry = { fn, owner: opts.owner || 'unknown', once: !!opts.once };
    set.add(entry);

    return () => set.delete(entry);  // unsubscribe
  }

  /** Subscribe once, auto-removes after first emission. */
  function once(event, fn, opts = {}) {
    return on(event, fn, { ...opts, once: true });
  }

  /** Unsubscribe a specific handler. */
  function off(event, fn) {
    const set = _listeners.get(event);
    if (!set) return;
    for (const entry of set) {
      if (entry.fn === fn) { set.delete(entry); return; }
    }
  }

  /** Remove all listeners owned by a given module (call in cleanup). */
  function offAll(owner) {
    let count = 0;
    for (const [, set] of _listeners) {
      for (const entry of set) {
        if (entry.owner === owner) { set.delete(entry); count++; }
      }
    }
    if (_dev && count) console.debug(`[NxBus] Removed ${count} listeners for owner "${owner}"`);
    return count;
  }

  /**
   * Emit an event synchronously.
   * @param {string} event
   * @param {*}      [data]
   */
  function emit(event, data) {
    // Record history
    _history.push({ event, data, at: Date.now() });
    if (_history.length > MAX_HIST) _history.shift();

    const set = _listeners.get(event);
    if (!set || !set.size) return;

    // Snapshot to avoid mutation during iteration
    const snap = Array.from(set);
    for (const entry of snap) {
      try {
        entry.fn(data, event);
      } catch (err) {
        console.error(`[NxBus] Error in listener for "${event}" (owner: ${entry.owner}):`, err);
      }
      if (entry.once) set.delete(entry);
    }

    // Mirror to DOM for legacy listeners (opt-out via nx_no_dom_events)
    if (!window._NX_NO_DOM_EVENTS) {
      try {
        document.dispatchEvent(new CustomEvent(event, { detail: data, bubbles: false }));
      } catch(_) {}
    }
  }

  /** Emit on next microtask (for events fired inside constructors). */
  function emitAsync(event, data) {
    Promise.resolve().then(() => emit(event, data));
  }

  /* ── Debug API ───────────────────────────────────────────────────── */
  function listenerCounts() {
    const out = {};
    for (const [event, set] of _listeners) {
      if (set.size) out[event] = set.size;
    }
    return out;
  }

  function history(n = 20) {
    return _history.slice(-n);
  }

  function leakReport() {
    const counts = listenerCounts();
    const suspects = Object.entries(counts)
      .filter(([,n]) => n > 5)
      .map(([e,n]) => `${e}: ${n}`);
    return suspects.length ? suspects : ['No obvious leaks'];
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  const NxBus = { on, once, off, offAll, emit, emitAsync, EVENTS, listenerCounts, history, leakReport };
  window.NxBus = NxBus;

  /* ── Bridge: wire NxBus into existing runtime globals ───────────── */
  /* Done lazily after DOMContentLoaded so boot.js is already wired.  */
  function _wireBridge() {
    // Toast bridge: NxBus.TOAST → NdsToast
    NxBus.on(EVENTS.TOAST, (d) => {
      if (typeof NdsToast === 'function') NdsToast(d.msg, d.type, d.opts);
      else if (typeof nxToast === 'function') nxToast(d.msg, d.type);
    }, { owner: 'nx-bus-bridge' });

    // Activity bridge: ACTIVITY_EVENT → NxActivity
    NxBus.on(EVENTS.ACTIVITY_EVENT, (d) => {
      if (window.NxActivity) NxActivity.log(d.type, d.label, d.detail);
    }, { owner: 'nx-bus-bridge' });

    // Status bridge: AGENT_STATUS → NxStatusBar
    NxBus.on(EVENTS.AGENT_STATUS, (d) => {
      if (window.NxStatusBar) NxStatusBar.setTask(d.status, d.detail);
    }, { owner: 'nx-bus-bridge' });

    // WS status bridge
    NxBus.on(EVENTS.WS_STATUS, (d) => {
      if (window.NxStatusBar) NxStatusBar.setWs(d.state);
    }, { owner: 'nx-bus-bridge' });

    // Tab change bridge: TAB_CHANGE → NxInspector
    NxBus.on(EVENTS.TAB_CHANGE, (d) => {
      if (window.NxInspector) NxInspector.render(d.tab);
    }, { owner: 'nx-bus-bridge' });

    // Workspace preset bridge
    NxBus.on(EVENTS.WORKSPACE_PRESET, (d) => {
      if (typeof nxApplyPreset === 'function') nxApplyPreset(d.id);
    }, { owner: 'nx-bus-bridge' });

    // Shim: global nxEmit for legacy code
    window.nxEmit = (event, data) => NxBus.emit(event, data);

    if (window.NX) {
      NxBus.emit(EVENTS.BOOT_READY, { at: Date.now() });
    }
  }

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_wireBridge);
  } else {
    document.addEventListener('DOMContentLoaded', _wireBridge);
  }
})();
