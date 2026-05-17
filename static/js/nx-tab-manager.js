/* ═══════════════════════════════════════════════════════════════════════════
   NX Tab Manager — Z22 Module
   Wraps and augments the tab-switching system from ui.js.
   Owns: tab-change event emission, aria-selected syncing, tab history.
   Z23: manages role="tablist"/"tab" ARIA, focus-follows-tab.
   Does NOT re-implement nxSetTab — wraps it via monkey-patch after ui.js loads.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxTabManager() {
  'use strict';

  const _TAB_HISTORY = [];
  const MAX_HISTORY  = 20;
  let   _patchApplied = false;

  /* ── Wait for nxSetTab to be registered, then patch it ──────────────────── */
  function _patchSetTab() {
    if (_patchApplied) return;
    if (typeof window.nxSetTab !== 'function') return;
    _patchApplied = true;

    const _orig = window.nxSetTab;
    window.nxSetTab = function(id) {
      const prev = window.NX?.activeTab;
      _orig(id);
      // Track history
      _TAB_HISTORY.push({ tab: id, ts: Date.now() });
      if (_TAB_HISTORY.length > MAX_HISTORY) _TAB_HISTORY.shift();
      // Emit normalized event via NxBus
      window.NxBus?.emit('tabChange', { tab: id, prev });
    };
    // Also alias
    window.nxSwitchTab = window.nxSetTab;
  }

  /* ── Ensure tablist ARIA on the tab bar ────────────────────────────────── */
  function _setupTablistAria() {
    const bar = document.getElementById('nxTabBar');
    if (bar && !bar.getAttribute('role')) {
      bar.setAttribute('role', 'tablist');
      bar.setAttribute('aria-label', 'Workspace panels');
    }
    // Assign role=tab to any nx-tab buttons that don't already have it
    document.querySelectorAll('.nx-tab').forEach(btn => {
      if (!btn.getAttribute('role')) btn.setAttribute('role', 'tab');
      // Initialise aria-selected if missing
      if (!btn.getAttribute('aria-selected')) {
        btn.setAttribute('aria-selected', btn.classList.contains('active') ? 'true' : 'false');
      }
    });
  }

  /* ── Arrow-key navigation within the tablist (Z23) ─────────────────────── */
  function _wireArrowNavigation() {
    const bar = document.getElementById('nxTabBar');
    if (!bar) return;
    bar.addEventListener('keydown', e => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      const tabs = Array.from(bar.querySelectorAll('.nx-tab:not(.hidden):not([disabled])'));
      const current = document.activeElement;
      const idx = tabs.indexOf(current);
      if (idx < 0) return;
      e.preventDefault();
      let next;
      if (e.key === 'ArrowRight') next = tabs[(idx + 1) % tabs.length];
      else if (e.key === 'ArrowLeft') next = tabs[(idx - 1 + tabs.length) % tabs.length];
      else if (e.key === 'Home') next = tabs[0];
      else if (e.key === 'End') next = tabs[tabs.length - 1];
      if (next) {
        next.focus();
        next.click(); // activate the tab
      }
    });
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.NxTabManager = {
    setTab:       (id) => window.nxSetTab?.(id),
    getActive:    ()   => window.NX?.activeTab,
    getHistory:   ()   => [..._TAB_HISTORY],
    onTabChange:  (fn, owner) => window.NxBus?.on('tabChange', fn, owner || 'NxTabManager'),
  };

  /* ── Init (deferred so ui.js has time to register nxSetTab) ─────────────── */
  function _init() {
    _patchSetTab();
    _setupTablistAria();
    _wireArrowNavigation();
    console.debug('[NxTabManager] ready');
  }

  if (typeof window.NX_LOAD_TASKS !== 'undefined') {
    window.NX_LOAD_TASKS.push(_init);
  } else {
    document.addEventListener('DOMContentLoaded', _init);
  }

})();
