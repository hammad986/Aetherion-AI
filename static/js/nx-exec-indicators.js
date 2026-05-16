/* ═══════════════════════════════════════════════════════════════════════════
   NX Exec Indicators — Z22 Module
   Owns: execution state machine, run-dot visibility, topbar status text,
         status-bar aria-live announcements, [data-exec-state] attribute.
   Z23: aria-live status announcement on state change, colour-blind-safe
        indicators via attribute (not colour-only).
   Listens to NxBus events from runtime.js and ui.js.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxExecIndicators() {
  'use strict';

  /* ── States ─────────────────────────────────────────────────────────────── */
  const STATES = { idle: 'idle', running: 'running', streaming: 'streaming', error: 'error', stopping: 'stopping' };
  let _current = STATES.idle;
  const _handlers = [];

  /* ── Element cache (lazy) ────────────────────────────────────────────────── */
  function _el(id) { return document.getElementById(id); }
  function _root()     { return _el('nxShellRoot') || document.body; }
  function _runDot()   { return document.querySelector('.nx-run-dot'); }
  function _statusEl() { return _el('nxSbStatus') || _el('nxStatusTxt'); }
  function _runBtn()   { return _el('runBtn'); }

  /* ── Announce to screen reader ───────────────────────────────────────────── */
  function _announce(msg) {
    let ar = _el('nxExecAnnounce');
    if (!ar) {
      ar = document.createElement('div');
      ar.id = 'nxExecAnnounce';
      ar.className = 'nx-sr-only';
      ar.setAttribute('aria-live', 'polite');
      ar.setAttribute('aria-atomic', 'true');
      document.body.appendChild(ar);
    }
    ar.textContent = '';
    requestAnimationFrame(() => { ar.textContent = msg; });
  }

  /* ── State application ───────────────────────────────────────────────────── */
  function setState(state) {
    if (!STATES[state]) return;
    const prev = _current;
    _current = state;

    /* data-exec-state on root (CSS hooks) */
    _root().setAttribute('data-exec-state', state);

    /* Run dot */
    const dot = _runDot();
    if (dot) {
      dot.classList.toggle('visible', state === STATES.running || state === STATES.streaming);
      dot.setAttribute('aria-label', state === STATES.running ? 'Task running' : '');
    }

    /* Run button label */
    const btn = _runBtn();
    if (btn) {
      const label = _el('runBtnLabel') || btn;
      if (state === STATES.running || state === STATES.streaming) {
        label.textContent = '⏹ Stop';
        btn.setAttribute('aria-label', 'Stop task execution');
        btn.setAttribute('aria-pressed', 'true');
      } else {
        label.textContent = '▶ Run';
        btn.setAttribute('aria-label', 'Execute task');
        btn.setAttribute('aria-pressed', 'false');
      }
    }

    /* Status bar text */
    const sb = _statusEl();
    if (sb) {
      const labels = { idle: 'Idle', running: 'Running…', streaming: 'Streaming…', error: 'Error', stopping: 'Stopping…' };
      sb.textContent = labels[state] || state;
    }

    /* Screen-reader announcement on state transitions (Z23) */
    const announcements = {
      running:   'Task started',
      streaming: 'Streaming output',
      idle:      prev !== 'idle' ? 'Task completed' : '',
      error:     'Task encountered an error',
      stopping:  'Stopping task…',
    };
    if (announcements[state]) _announce(announcements[state]);

    /* Notify subscribed handlers */
    for (const fn of _handlers) {
      try { fn(state, prev); } catch (_) {}
    }

    /* Emit on NxBus */
    window.NxBus?.emit('execStateChange', { state, prev });
  }

  function getState() { return _current; }

  function onStateChange(fn) { _handlers.push(fn); }

  /* ── Patch nxSetGlobalStatus from ui.js ──────────────────────────────────── */
  function _patchGlobalStatus() {
    const _orig = window.nxSetGlobalStatus;
    if (typeof _orig !== 'function') return;
    window.nxSetGlobalStatus = function(state) {
      _orig(state);
      setState(state);
    };
  }

  /* ── Init ───────────────────────────────────────────────────────────────── */
  function _init() {
    _patchGlobalStatus();
    setState(STATES.idle); // ensure clean initial state
    console.log('[NxExecIndicators] Execution indicators ready');
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.NxExecState = { setState, getState, onStateChange, STATES };

  if (typeof window.NX_LOAD_TASKS !== 'undefined') {
    window.NX_LOAD_TASKS.push(_init);
  } else {
    document.addEventListener('DOMContentLoaded', _init);
  }

})();
