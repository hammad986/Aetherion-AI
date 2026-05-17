/* ═══════════════════════════════════════════════════════════════════════════
   NX Phase Z21 / Z24 — Runtime Performance, Memory Discipline & Stress Hardening
   Z21: bounded DOM growth, toast eviction, SSE health tracking, perf HUD.
   Z24: memory trend tracking, observer leak detection, SSE reconnect storm
        protection, long-session uptime tracking, DOM node auto-alarm.
   ═══════════════════════════════════════════════════════════════════════════ */
(function NxRuntimeHygiene() {
  'use strict';

  /* ── Constants ─────────────────────────────────────────────────────────── */
  const LOG_DOM_CEILING     = 1500;   // max log-line nodes in #logArea
  const TOAST_TTL_MS        = 6000;   // auto-remove toasts after 6 s
  const TOAST_MAX_ALIVE     = 5;      // max simultaneous toast nodes
  const SSE_STALE_MS        = 45000;  // flag SSE stale if silent 45 s
  const DOM_WARN_NODES      = 8000;   // warn when DOM node count crosses
  const DOM_CRIT_NODES      = 14000;  // critical threshold
  const HUD_INTERVAL_MS     = 2000;   // perf HUD refresh rate
  /* Z24 */
  const HEAP_SAMPLE_INTERVAL = 30000; // memory trend sample every 30 s
  const HEAP_SAMPLE_MAX      = 20;    // keep 20 samples (~10 min window)
  const SSE_STORM_WINDOW_MS  = 60000; // detect > N reconnects in this window
  const SSE_STORM_THRESHOLD  = 5;     // N reconnects = storm
  const SESSION_UPTIME_START = Date.now();

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
    /* Z24 */
    heapSamples: [],        // [{ts, used, total}]
    sseReconnects: [],      // timestamps of recent reconnects
    sseStormActive: false,
    observerCount: 0,
  };

  /* ── 1. Log DOM ceiling enforcement ────────────────────────────────────── */
  function enforceLogCeiling() {
    const area = document.getElementById('logArea');
    if (!area) return;
    const children = area.children;
    if (children.length <= LOG_DOM_CEILING) return;

    const toRemove = children.length - LOG_DOM_CEILING;
    for (let i = 0; i < toRemove; i++) {
      if (area.firstChild) area.removeChild(area.firstChild);
    }
    _state.logTrimCount += toRemove;

    let notice = area.querySelector('.nx-log-trimmed-notice');
    if (!notice) {
      notice = document.createElement('div');
      notice.className = 'nx-log-trimmed-notice';
      notice.setAttribute('aria-live', 'polite');
      area.insertBefore(notice, area.firstChild);
    }
    notice.textContent = `▲ ${_state.logTrimCount} older lines trimmed (ceiling: ${LOG_DOM_CEILING})`;
  }

  setInterval(enforceLogCeiling, 5000);

  /* ── 2. Toast / notification node cleanup ──────────────────────────────── */
  function patchToastSystem() {
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

    const body = document.body;
    if (!body) return;

    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'childList' && m.addedNodes.length) pruneToasts(body);
      }
    });
    obs.observe(body, { childList: true, subtree: false });
    setInterval(() => pruneToasts(body), TOAST_TTL_MS);
  }

  /* ── 3. SSE health tracking ─────────────────────────────────────────────── */
  function installSSEHealthPatch() {
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
            document.body.classList.remove('nx-sse-reconnecting');
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
        /* Z24: reconnect storm detection */
        const now = Date.now();
        _state.sseReconnects.push(now);
        _state.sseReconnects = _state.sseReconnects.filter(t => now - t < SSE_STORM_WINDOW_MS);
        if (_state.sseReconnects.length >= SSE_STORM_THRESHOLD && !_state.sseStormActive) {
          _state.sseStormActive = true;
          console.warn('[NX:Z24] SSE reconnect storm detected —',
            _state.sseReconnects.length, 'reconnects in', SSE_STORM_WINDOW_MS / 1000, 's');
          document.body.setAttribute('data-sse-storm', 'true');
          window.NxBus?.emit('sseReconnectStorm', { count: _state.sseReconnects.length });
        }
      });

      es.addEventListener('open', () => {
        _state.sseConnected = true;
        _state.sseLastMessage = Date.now();
        document.body.classList.remove('nx-sse-reconnecting');
        if (_state.sseStormActive) {
          _state.sseStormActive = false;
          document.body.removeAttribute('data-sse-storm');
          console.log('[NX:Z24] SSE storm resolved');
        }
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

  /* ── 4. FPS sampling ─────────────────────────────────────────────────────── */
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

  /* ── 5. Z24: Memory trend tracking ──────────────────────────────────────── */
  function sampleHeap() {
    const mem = performance.memory;
    if (!mem) return;
    _state.heapSamples.push({
      ts: Date.now(),
      used: mem.usedJSHeapSize,
      total: mem.totalJSHeapSize,
      limit: mem.jsHeapSizeLimit,
    });
    if (_state.heapSamples.length > HEAP_SAMPLE_MAX) _state.heapSamples.shift();

    /* Detect monotonically growing heap (Z24 stress signal) */
    if (_state.heapSamples.length >= 5) {
      const last5 = _state.heapSamples.slice(-5);
      const growing = last5.every((s, i) => i === 0 || s.used > last5[i - 1].used);
      if (growing) {
        const mb = (last5[4].used / 1048576).toFixed(1);
        console.warn('[NX:Z24] Heap growth trend detected — current:', mb, 'MB');
        window.NxBus?.emit('heapGrowthTrend', { samples: last5 });
      }
    }
  }
  setInterval(sampleHeap, HEAP_SAMPLE_INTERVAL);

  /* ── 6. Z24: DOM node auto-alarm ──────────────────────────────────────── */
  function checkDOMNodes() {
    const count = document.querySelectorAll('*').length;
    if (count > DOM_CRIT_NODES) {
      console.error('[NX:Z24] Critical DOM node count:', count, '— check for detached node leaks');
      window.NxBus?.emit('domNodeCritical', { count });
    } else if (count > DOM_WARN_NODES) {
      console.warn('[NX:Z24] DOM node count warning:', count);
    }
  }
  setInterval(checkDOMNodes, 30000);

  /* ── 7. Z24: MutationObserver count detection ────────────────────────────── */
  /* Patch MutationObserver to count how many are active */
  (function patchObserverCount() {
    const OrigMO = window.MutationObserver;
    if (!OrigMO || window._nxMOPatched) return;
    window._nxMOPatched = true;
    window.MutationObserver = function(cb) {
      const mo = new OrigMO(cb);
      _state.observerCount++;
      const origDisconnect = mo.disconnect.bind(mo);
      mo.disconnect = function() {
        _state.observerCount = Math.max(0, _state.observerCount - 1);
        return origDisconnect();
      };
      return mo;
    };
    window.MutationObserver.prototype = OrigMO.prototype;
  })();

  /* ── 8. Perf HUD ─────────────────────────────────────────────────────────── */
  function createHUD() {
    const el = document.createElement('div');
    el.className = 'nx-perf-hud';
    el.id = 'nxPerfHud';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = `
      <div class="nx-perf-hud-title">NX Perf HUD</div>
      <div class="nx-perf-hud-row"><span>FPS</span><span class="nx-perf-hud-val" id="nxHudFps">—</span></div>
      <div class="nx-perf-hud-row"><span>DOM nodes</span><span class="nx-perf-hud-val" id="nxHudDom">—</span></div>
      <div class="nx-perf-hud-row"><span>Log rows</span><span class="nx-perf-hud-val" id="nxHudLog">—</span></div>
      <div class="nx-perf-hud-row"><span>Trimmed</span><span class="nx-perf-hud-val" id="nxHudTrimmed">0</span></div>
      <div class="nx-perf-hud-row"><span>Observers</span><span class="nx-perf-hud-val" id="nxHudObs">—</span></div>
      <div class="nx-perf-hud-row"><span>SSE</span><span class="nx-perf-hud-val" id="nxHudSSE">—</span></div>
      <div class="nx-perf-hud-row"><span>JS Heap</span><span class="nx-perf-hud-val" id="nxHudHeap">—</span></div>
      <div class="nx-perf-hud-row"><span>Uptime</span><span class="nx-perf-hud-val" id="nxHudUptime">—</span></div>
    `;
    document.body.appendChild(el);
    _state.hudEl = el;
  }

  function updateHUD() {
    if (!_state.hudEl) return;
    const $ = (id) => document.getElementById(id);

    const fpsEl = $('nxHudFps');
    if (fpsEl) {
      fpsEl.textContent = _state.fps;
      fpsEl.className = 'nx-perf-hud-val' +
        (_state.fps < 20 ? ' nx-perf-hud-crit' :
         _state.fps < 40 ? ' nx-perf-hud-warn' : '');
    }

    const domCount = document.querySelectorAll('*').length;
    const domEl = $('nxHudDom');
    if (domEl) {
      domEl.textContent = domCount;
      domEl.className = 'nx-perf-hud-val' +
        (domCount > DOM_CRIT_NODES ? ' nx-perf-hud-crit' :
         domCount > DOM_WARN_NODES ? ' nx-perf-hud-warn' : '');
    }

    const logArea = document.getElementById('logArea');
    const logEl = $('nxHudLog');
    if (logEl) logEl.textContent = logArea ? logArea.children.length : '—';

    const trimEl = $('nxHudTrimmed');
    if (trimEl) trimEl.textContent = _state.logTrimCount;

    const obsEl = $('nxHudObs');
    if (obsEl) {
      obsEl.textContent = _state.observerCount;
      obsEl.className = 'nx-perf-hud-val' + (_state.observerCount > 20 ? ' nx-perf-hud-warn' : '');
    }

    const sseEl = $('nxHudSSE');
    if (sseEl) {
      const age = Math.round((Date.now() - _state.sseLastMessage) / 1000);
      sseEl.textContent = _state.sseStormActive
        ? 'STORM (' + _state.sseReconnects.length + ' reconnects)'
        : (_state.sseConnected ? 'ok (' + age + 's ago)' : 'disconnected');
      sseEl.className = 'nx-perf-hud-val' +
        (_state.sseStormActive || !_state.sseConnected || age > 30 ? ' nx-perf-hud-warn' : '');
    }

    const heapEl = $('nxHudHeap');
    if (heapEl) {
      const mem = performance.memory;
      if (mem) {
        const used  = (mem.usedJSHeapSize  / 1048576).toFixed(1);
        const total = (mem.totalJSHeapSize / 1048576).toFixed(1);
        heapEl.textContent = `${used} / ${total} MB`;
        heapEl.className = 'nx-perf-hud-val' +
          (mem.usedJSHeapSize / mem.jsHeapSizeLimit > 0.85 ? ' nx-perf-hud-crit' :
           mem.usedJSHeapSize / mem.jsHeapSizeLimit > 0.6  ? ' nx-perf-hud-warn' : '');

        /* Z24: track trend */
        if (_state.heapSamples.length >= 2) {
          const oldest = _state.heapSamples[0].used;
          const newest = _state.heapSamples[_state.heapSamples.length - 1].used;
          const delta  = ((newest - oldest) / 1048576).toFixed(1);
          heapEl.textContent += ` (Δ${delta > 0 ? '+' : ''}${delta}MB)`;
        }
      } else {
        heapEl.textContent = 'n/a';
      }
    }

    /* Z24: session uptime */
    const uptimeEl = $('nxHudUptime');
    if (uptimeEl) {
      const ms = Date.now() - SESSION_UPTIME_START;
      const h = Math.floor(ms / 3600000);
      const m = Math.floor((ms % 3600000) / 60000);
      const s = Math.floor((ms % 60000) / 1000);
      uptimeEl.textContent = `${h}h ${m}m ${s}s`;
    }
  }

  /* ── 9. Timer leak guard ─────────────────────────────────────────────────── */
  const _timerIds = new Set();

  window._nxClearAllTimers = function() {
    let cleared = 0;
    _timerIds.forEach(id => { clearInterval(id); cleared++; });
    _timerIds.clear();
    console.log('[NX:Z21/Z24] Cleared', cleared, 'tracked timers');
  };

  /* ── 10. Z24: Long-session diagnostic snapshot ──────────────────────────── */
  window._nxDiagSnapshot = function() {
    const uptime = Math.round((Date.now() - SESSION_UPTIME_START) / 1000);
    return {
      uptimeSec: uptime,
      fps: _state.fps,
      logTrimCount: _state.logTrimCount,
      sseConnected: _state.sseConnected,
      sseStaleSec: Math.round((Date.now() - _state.sseLastMessage) / 1000),
      sseReconnects: _state.sseReconnects.length,
      sseStormActive: _state.sseStormActive,
      observerCount: _state.observerCount,
      heapSamples: _state.heapSamples.map(s => ({
        ts: new Date(s.ts).toISOString(),
        usedMB: (s.used / 1048576).toFixed(1),
      })),
      domNodes: document.querySelectorAll('*').length,
      logRows: document.getElementById('logArea')?.children.length || 0,
    };
  };

  /* ── Public state ──────────────────────────────────────────────────────── */
  window._nxPerfState = _state;

  /* ── 11. Toggle HUD ─────────────────────────────────────────────────────── */
  window.nxPerfHUD = function(on) {
    if (on === undefined) on = !document.body.classList.contains('nx-debug-perf');
    document.body.classList.toggle('nx-debug-perf', on);
    console.log('[NX:Z21] Perf HUD', on ? 'ON — use _nxDiagSnapshot() for full report' : 'OFF');
  };

  /* ── Initialise ─────────────────────────────────────────────────────────── */
  function init() {
    installSSEHealthPatch();
    patchToastSystem();

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', createHUD);
    } else {
      createHUD();
    }

    setInterval(updateHUD, HUD_INTERVAL_MS);
    console.debug('[NX:Z21/Z24] hygiene active');
  }

  init();

})();
