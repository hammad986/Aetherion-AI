/**
 * nx-session-cleanup.js — Long-Session Memory Leak Prevention
 * ════════════════════════════════════════════════════════════
 * Phase: Beta Operations — 6-12 Hour Session Stability
 *
 * Fixes:
 *   - Monaco editor model disposal on session end
 *   - ResizeObserver disconnect on cleanup
 *   - xterm terminal cleanup on session end
 *   - Cognition list filter chips (reduce DOM load)
 *   - NxBus listener audit + cleanup
 *   - localStorage replay GC (prune old sessions)
 *   - Long-poll interval cleanup registry
 *
 * Usage: loaded after nx-agi-surface.js in index.html
 * All cleanup triggered by NxBus 'session.idle' or 'session.end'
 */
'use strict';

(function () {
  if (window.NxSessionCleanup) return;

  /* ── Cleanup Registry ────────────────────────────────────────────── */
  const _intervals = [];
  const _observers = [];
  const _listeners = [];

  function _track(type, ref) {
    if (type === 'interval')  _intervals.push(ref);
    if (type === 'observer')  _observers.push(ref);
    if (type === 'listener')  _listeners.push(ref);
  }

  /* ── Monaco Cleanup ──────────────────────────────────────────────── */
  function _cleanupMonaco() {
    try {
      if (!window.monaco) return;
      // Dispose all models except the active one
      const models = monaco.editor.getModels();
      models.forEach((model, i) => {
        // Keep last model (active editor state)
        if (i < models.length - 1) {
          try { model.dispose(); } catch (_) {}
        }
      });
      // Dispose editor instances that are no longer in DOM
      if (window._monacoEditorInstances) {
        window._monacoEditorInstances = window._monacoEditorInstances.filter(ed => {
          try {
            const domNode = ed.getDomNode();
            if (!domNode || !document.contains(domNode)) {
              ed.dispose();
              return false;
            }
            return true;
          } catch (_) {
            return false;
          }
        });
      }
      console.debug('[NxCleanup] Monaco models cleaned. Remaining:', monaco.editor.getModels().length);
    } catch (e) {
      console.debug('[NxCleanup] Monaco cleanup skipped:', e.message);
    }
  }

  /* ── ResizeObserver Cleanup ──────────────────────────────────────── */
  function _cleanupResizeObservers() {
    _observers.forEach(obs => {
      try { obs.disconnect(); } catch (_) {}
    });
    _observers.length = 0;

    // Disconnect any NxAgiSurface resize observers
    if (window._nxResizeObservers) {
      window._nxResizeObservers.forEach(obs => {
        try { obs.disconnect(); } catch (_) {}
      });
      window._nxResizeObservers = [];
    }
    console.debug('[NxCleanup] ResizeObservers disconnected.');
  }

  /* ── xterm Cleanup ───────────────────────────────────────────────── */
  function _cleanupXterm() {
    try {
      // Dispose xterm instances that have detached from DOM
      if (window._xtermInstances) {
        window._xtermInstances = window._xtermInstances.filter(term => {
          try {
            const el = term.element;
            if (!el || !document.contains(el)) {
              term.dispose();
              return false;
            }
            return true;
          } catch (_) { return false; }
        });
      }
      console.debug('[NxCleanup] xterm instances cleaned.');
    } catch (e) {
      console.debug('[NxCleanup] xterm cleanup skipped:', e.message);
    }
  }

  /* ── Interval Cleanup ────────────────────────────────────────────── */
  function _cleanupIntervals() {
    _intervals.forEach(id => {
      try { clearInterval(id); } catch (_) {}
    });
    _intervals.length = 0;
    console.debug('[NxCleanup] Intervals cleared.');
  }

  /* ── localStorage Replay GC ──────────────────────────────────────── */
  function _gcReplayStorage() {
    try {
      const PREFIX = 'nx_dag_replay:';
      const MAX_AGE_MS = 48 * 60 * 60 * 1000; // 48 hours
      const now = Date.now();
      const toDelete = [];

      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!key || !key.startsWith(PREFIX)) continue;
        try {
          const raw = localStorage.getItem(key);
          const obj = JSON.parse(raw);
          if (obj.saved_at && (now - obj.saved_at) > MAX_AGE_MS) {
            toDelete.push(key);
          }
        } catch (_) {
          toDelete.push(key); // corrupt — delete
        }
      }
      toDelete.forEach(k => localStorage.removeItem(k));
      if (toDelete.length) {
        console.debug(`[NxCleanup] GC'd ${toDelete.length} old replay entries from localStorage.`);
      }
    } catch (e) {
      console.debug('[NxCleanup] localStorage GC skipped:', e.message);
    }
  }

  /* ── Cognition Filter Chips ──────────────────────────────────────── */
  /**
   * Injects filter chip UI above the cognition list.
   * Filters: All | Reasoning | Tool | Error | Memory
   * Dramatically reduces visible DOM nodes under verbose agents.
   */
  function _injectCognitionFilters() {
    const list = document.getElementById('agiCognitionList');
    if (!list || document.getElementById('agiCognitionFilters')) return;

    const bar = document.createElement('div');
    bar.id = 'agiCognitionFilters';
    bar.style.cssText = [
      'display:flex',
      'gap:6px',
      'padding:6px 10px 4px',
      'border-bottom:1px solid var(--nds-surface-3)',
      'flex-wrap:wrap',
      'flex-shrink:0',
    ].join(';');

    const FILTERS = [
      { label: 'All',       value: 'all',       color: 'var(--nds-text-lo)' },
      { label: '💭 Think',  value: 'thought',   color: 'var(--nds-accent)' },
      { label: '🔧 Tool',   value: 'tool',      color: 'var(--nds-green)' },
      { label: '❌ Error',  value: 'error',     color: 'var(--nds-red)' },
      { label: '🧠 Memory', value: 'memory',    color: 'var(--nds-purple)' },
    ];

    let _activeFilter = 'all';

    function _applyFilter(val) {
      _activeFilter = val;
      const items = list.querySelectorAll('[data-cog-type]');
      items.forEach(item => {
        if (val === 'all') {
          item.style.display = '';
        } else {
          const t = item.getAttribute('data-cog-type') || '';
          item.style.display = t.includes(val) ? '' : 'none';
        }
      });
      // Update chip active state
      bar.querySelectorAll('.cog-chip').forEach(chip => {
        chip.style.background = chip.dataset.val === val
          ? 'var(--nds-accent)'
          : 'var(--nds-surface-3)';
        chip.style.color = chip.dataset.val === val
          ? '#fff'
          : 'var(--nds-text-lo)';
      });
    }

    FILTERS.forEach(f => {
      const chip = document.createElement('button');
      chip.className = 'cog-chip';
      chip.dataset.val = f.value;
      chip.textContent = f.label;
      chip.style.cssText = [
        'padding:2px 8px',
        'border-radius:10px',
        'border:none',
        'cursor:pointer',
        'font-size:10px',
        'font-family:inherit',
        'transition:background 0.15s',
        `background:${f.value === 'all' ? 'var(--nds-accent)' : 'var(--nds-surface-3)'}`,
        `color:${f.value === 'all' ? '#fff' : 'var(--nds-text-lo)'}`,
      ].join(';');
      chip.addEventListener('click', () => _applyFilter(f.value));
      bar.appendChild(chip);
    });

    // Insert before the cognition list
    list.parentNode.insertBefore(bar, list);

    // Re-apply filter when new items are added (MutationObserver)
    const obs = new MutationObserver(() => {
      if (_activeFilter !== 'all') _applyFilter(_activeFilter);
    });
    obs.observe(list, { childList: true });
    _track('observer', obs);

    console.debug('[NxCleanup] Cognition filter chips injected.');
  }

  /* ── NxBus listener audit ────────────────────────────────────────── */
  function _auditNxBusListeners() {
    if (!window.NxBus) return;
    try {
      const count = typeof NxBus.listenerCount === 'function'
        ? NxBus.listenerCount()
        : '?';
      console.debug(`[NxCleanup] NxBus listeners: ${count}`);
      // If NxBus exposes pruneOrphaned, call it
      if (typeof NxBus.pruneOrphaned === 'function') {
        NxBus.pruneOrphaned();
      }
    } catch (_) {}
  }

  /* ── Full session cleanup ────────────────────────────────────────── */
  function runCleanup(reason = 'manual') {
    console.info(`[NxCleanup] Running cleanup (reason: ${reason})...`);
    _cleanupMonaco();
    _cleanupResizeObservers();
    _cleanupXterm();
    _cleanupIntervals();
    _gcReplayStorage();
    _auditNxBusListeners();
    console.info('[NxCleanup] Cleanup complete.');
  }

  /* ── Periodic light GC (every 15 min for long sessions) ──────────── */
  const _gcInterval = setInterval(() => {
    _gcReplayStorage();
    _cleanupMonaco();
    _auditNxBusListeners();
  }, 15 * 60 * 1000);
  _track('interval', _gcInterval);

  /* ── NxBus wiring ────────────────────────────────────────────────── */
  function _wireCleanup() {
    if (!window.NxBus) return;
    NxBus.on('session.idle',  () => runCleanup('session.idle'),  { owner: 'nx-cleanup' });
    NxBus.on('session.done',  () => setTimeout(() => runCleanup('session.done'), 2000), { owner: 'nx-cleanup' });
    NxBus.on('session.error', () => setTimeout(() => runCleanup('session.error'), 2000), { owner: 'nx-cleanup' });

    // Inject cognition filters once DOM is ready
    _injectCognitionFilters();
  }

  /* ── Init ────────────────────────────────────────────────────────── */
  function _init() {
    _wireCleanup();
    if (!window.NxBus) {
      const t = setInterval(() => {
        if (window.NxBus) { _wireCleanup(); clearInterval(t); }
      }, 300);
    }
    // Run localStorage GC once on startup
    setTimeout(_gcReplayStorage, 3000);
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window.NxSessionCleanup = {
    run: runCleanup,
    trackInterval: (id) => _track('interval', id),
    trackObserver: (obs) => _track('observer', obs),
    gcReplayStorage: _gcReplayStorage,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 200));
  } else {
    setTimeout(_init, 200);
  }

})();
