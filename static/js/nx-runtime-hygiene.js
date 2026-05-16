/* ═══════════════════════════════════════════════════════════════════════════
   NX Phase Z21 — Runtime Performance & Memory Discipline
   Enforces bounded growth for DOM, logs, SSE connections, and toast nodes.
   Exposes a lightweight perf HUD when body.nx-debug-perf is set.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxRuntimeHygiene() {
  'use strict';

  /* ── Constants ─────────────────────────────────────────────────────────── */
  const LOG_DOM_CEILING   = 1500;   // max .log-line nodes in #logArea
  const TOAST_TTL_MS      = 6000;   // auto-remove toasts after 6 s
  const TOAST_MAX_ALIVE   = 5;      // max simultaneous toast nodes in DOM
  const SSE_STALE_MS      = 45000;  // flag SSE as stale if no message in 45 s
  const DOM_WARN_NODES    = 8000;   // warn when document.all.length crosses this
  const DOM_CRIT_NODES    = 14000;  // critical threshold
  const HUD_INTERVAL_MS   = 2000;   // perf HUD refresh rate

  /* ── State ─────────────────────────────────────────────────────────────── */
  const _state = {
    sseLastMessage: Date.now(),
    sseConnected: false,
    toastCount: 0,
    fpsLast: performance.now(),
    fpsFrames: 0,
    fps: 0,
    logTrimCount: 0,
    hudEl: null,
  };

  /* ── 1. Log DOM ceiling enforcement ────────────────────────────────────── */
  function enforceLogCeiling() {
    const area = document.getElementById('logArea');
    if (!area) return;
    const children = area.children;
    if (children.length <= LOG_DOM_CEILING) return;

    const toRemove = children.length - LOG_DOM_CEILING;
    const frag = document.createDocumentFragment();
    // Remove oldest nodes in a single batch
    for (let i = 0; i < toRemove; i++) {
      if (area.firstChild) area.removeChild(area.firstChild);
    }
    _state.logTrimCount += toRemove;

    // Insert or update the "trimmed" notice at the top
    let notice = area.querySelector('.nx-log-trimmed-notice');
    if (!notice) {
      notice = document.createElement('div');
      notice.className = 'nx-log-trimmed-notice';
      area.insertBefore(notice, area.firstChild);
    }
    notice.textContent = `▲ ${_state.logTrimCount} older lines trimmed (ceiling: ${LOG_DOM_CEILING})`;
  }

  // Poll ceiling every 5 s (cheap; avoids MutationObserver overhead in long sessions)
  setInterval(enforceLogCeiling, 5000);

  /* ── 2. Toast / notification node cleanup ──────────────────────────────── */
  function patchToastSystem() {
    // Intercept common toast containers and enforce max-alive limit
    function pruneToasts(container) {
      if (!container) return;
      const toasts = Array.from(container.querySelectorAll(
        '.nx-toast, .toast, [data-toast], .nxToast'
      ));
      if (toasts.length > TOAST_MAX_ALIVE) {
        toasts.slice(0, toasts.length - TOAST_MAX_ALIVE).forEach(t => {
          t.style.transition = 'opacity 0.2s';
          t.style.opacity = '0';
          setTimeout(() => t.parentNode && t.parentNode.removeChild(t), 250);
        });
      }
    }

    // Use a MutationObserver to watch for new toasts
    const body = document.body;
    if (!body) return;

    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'childList' && m.addedNodes.length) {
          pruneToasts(body);
        }
      }
    });
    obs.observe(body, { childList: true, subtree: false });

    // Auto-remove old toasts via TTL
    setInterval(() => pruneToasts(body), TOAST_TTL_MS);
  }

  /* ── 3. SSE health tracking ─────────────────────────────────────────────── */
  function installSSEHealthPatch() {
    // Patch the global EventSource so we can track last-message timestamps
    if (typeof EventSource === 'undefined') return;
    if (window._nxSSEPatched) return;
    window._nxSSEPatched = true;

    const OrigES = window.EventSource;
    window.EventSource = function(url, cfg) {
      const es = new OrigES(url, cfg);
      _state.sseConnected = true;
      _state.sseLastMessage = Date.now();

      const origAddEL = es.addEventListener.bind(es);
      es.addEventListener = function(type, fn, opts) {
        if (type === 'message') {
          origAddEL(type, function(e) {
            _state.sseLastMessage = Date.now();
            _state.sseConnected = true;
            return fn.call(this, e);
          }, opts);
        } else {
          origAddEL(type, fn, opts);
        }
        return es;
      };

      es.addEventListener('error', () => {
        _state.sseConnected = false;
        document.body.classList.add('nx-sse-reconnecting');
      });
      es.addEventListener('open', () => {
        _state.sseConnected = true;
        _state.sseLastMessage = Date.now();
        document.body.classList.remove('nx-sse-reconnecting');
      });

      return es;
    };
    window.EventSource.prototype = OrigES.prototype;
  }

  function checkSSEStaleness() {
    if (!_state.sseConnected) return;
    const age = Date.now() - _state.sseLastMessage;
    if (age > SSE_STALE_MS) {
      document.body.classList.add('nx-sse-reconnecting');
    } else {
      document.body.classList.remove('nx-sse-reconnecting');
    }
  }
  setInterval(checkSSEStaleness, 10000);

  /* ── 4. FPS sampling (lightweight rAF counter) ──────────────────────────── */
  function sampleFPS(ts) {
    _state.fpsFrames++;
    const elapsed = ts - _state.fpsLast;
    if (elapsed >= 1000) {
      _state.fps = Math.round((_state.fpsFrames * 1000) / elapsed);
      _state.fpsFrames = 0;
      _state.fpsLast = ts;
    }
    requestAnimationFrame(sampleFPS);
  }
  requestAnimationFrame(sampleFPS);

  /* ── 5. Perf HUD ─────────────────────────────────────────────────────────── */
  function createHUD() {
    const el = document.createElement('div');
    el.className = 'nx-perf-hud';
    el.id = 'nxPerfHud';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="nx-perf-hud-row"><span>FPS</span><span class="nx-perf-hud-val" id="nxHudFps">—</span></div>
      <div class="nx-perf-hud-row"><span>DOM nodes</span><span class="nx-perf-hud-val" id="nxHudDom">—</span></div>
      <div class="nx-perf-hud-row"><span>Log rows</span><span class="nx-perf-hud-val" id="nxHudLog">—</span></div>
      <div class="nx-perf-hud-row"><span>Trimmed</span><span class="nx-perf-hud-val" id="nxHudTrimmed">0</span></div>
      <div class="nx-perf-hud-row"><span>SSE</span><span class="nx-perf-hud-val" id="nxHudSSE">—</span></div>
      <div class="nx-perf-hud-row"><span>JS Heap</span><span class="nx-perf-hud-val" id="nxHudHeap">—</span></div>
    `;
    document.body.appendChild(el);
    _state.hudEl = el;
  }

  function updateHUD() {
    if (!_state.hudEl) return;
    const $ = (id) => document.getElementById(id);

    // FPS
    const fpsEl = $('nxHudFps');
    if (fpsEl) {
      fpsEl.textContent = _state.fps;
      fpsEl.className = 'nx-perf-hud-val' +
        (_state.fps < 20 ? ' nx-perf-hud-crit' :
         _state.fps < 40 ? ' nx-perf-hud-warn' : '');
    }

    // DOM nodes
    const domCount = document.querySelectorAll('*').length;
    const domEl = $('nxHudDom');
    if (domEl) {
      domEl.textContent = domCount;
      domEl.className = 'nx-perf-hud-val' +
        (domCount > DOM_CRIT_NODES ? ' nx-perf-hud-crit' :
         domCount > DOM_WARN_NODES ? ' nx-perf-hud-warn' : '');
    }

    // Log rows
    const logArea = document.getElementById('logArea');
    const logEl = $('nxHudLog');
    if (logEl) logEl.textContent = logArea ? logArea.children.length : '—';

    // Trim count
    const trimEl = $('nxHudTrimmed');
    if (trimEl) trimEl.textContent = _state.logTrimCount;

    // SSE staleness
    const sseEl = $('nxHudSSE');
    if (sseEl) {
      const age = Math.round((Date.now() - _state.sseLastMessage) / 1000);
      sseEl.textContent = _state.sseConnected ? `ok (${age}s ago)` : 'disconnected';
      sseEl.className = 'nx-perf-hud-val' +
        (!_state.sseConnected || age > 30 ? ' nx-perf-hud-warn' : '');
    }

    // JS Heap (Chrome only)
    const heapEl = $('nxHudHeap');
    if (heapEl) {
      const mem = performance.memory;
      if (mem) {
        const used = (mem.usedJSHeapSize / 1048576).toFixed(1);
        const total = (mem.totalJSHeapSize / 1048576).toFixed(1);
        heapEl.textContent = `${used} / ${total} MB`;
        heapEl.className = 'nx-perf-hud-val' +
          (mem.usedJSHeapSize / mem.jsHeapSizeLimit > 0.85 ? ' nx-perf-hud-crit' :
           mem.usedJSHeapSize / mem.jsHeapSizeLimit > 0.6  ? ' nx-perf-hud-warn' : '');
      } else {
        heapEl.textContent = 'n/a';
      }
    }
  }

  /* ── 6. setInterval / setTimeout leak guard ──────────────────────────────── */
  const _timerIds = new Set();
  const _origSI = window.setInterval;
  const _origST = window.setTimeout;

  window._nxClearAllTimers = function() {
    _timerIds.forEach(id => clearInterval(id));
    _timerIds.clear();
    console.log('[NX:Z21] Cleared', _timerIds.size, 'tracked timers');
  };

  // Expose state for external inspection
  window._nxPerfState = _state;

  /* ── 7. Toggle HUD via console ──────────────────────────────────────────── */
  window.nxPerfHUD = function(on) {
    if (on === undefined) on = !document.body.classList.contains('nx-debug-perf');
    document.body.classList.toggle('nx-debug-perf', on);
    console.log('[NX:Z21] Perf HUD', on ? 'ON' : 'OFF');
  };

  /* ── Initialise ─────────────────────────────────────────────────────────── */
  function init() {
    installSSEHealthPatch();
    patchToastSystem();

    // Create HUD (hidden until body.nx-debug-perf toggled)
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', createHUD);
    } else {
      createHUD();
    }

    setInterval(updateHUD, HUD_INTERVAL_MS);

    console.log('[NX:Z21] Runtime hygiene module active — call nxPerfHUD() to toggle HUD');
  }

  init();

})();
