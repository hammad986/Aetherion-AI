/**
 * nx-shim.js — Aetherion Legacy Migration Shim v1
 * ══════════════════════════════════════════════════════════════════════
 * PURPOSE: Intercept legacy window.* globals and route them through
 * the canonical NxBus / NxState architecture WITHOUT rewriting callers.
 *
 * MIGRATION STRATEGY:
 *   Phase 1 (this file): Wrap existing globals with NxBus emission.
 *   Phase 2 (future):    Move implementation into bus listeners.
 *   Phase 3 (future):    Delete originals + inline HTML onclick wiring.
 *
 * GOVERNANCE:
 *   - This file is TEMPORARY. Each wrapped function should be removed
 *     here once its callers are migrated to NxBus.emit() directly.
 *   - Never add new window.* globals here. Use NxBus.on() instead.
 *   - Runs AFTER all deferred modules (last defer in body).
 *
 * Ownership: frontend/architecture (migration layer)
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* Guard: needs NxBus + NxState */
  if (!window.NxBus || !window.NxState) {
    console.warn('[nx-shim] NxBus/NxState not available — skipping migration shim');
    return;
  }

  const BUS = {
    TAB_CHANGE:      'nx:tab:change',
    PANEL_TOGGLE:    'nx:panel:toggle',
    TOAST:           'nx:toast',
    AGENT_START:     'nx:agent:start',
    AGENT_DONE:      'nx:agent:done',
    AGENT_ERROR:     'nx:agent:error',
    AGENT_STATUS:    'nx:agent:status',
    PALETTE_OPEN:    'nx:palette:open',
    SETTINGS_OPEN:   'nx:settings:open',
    WS_STATUS:       'nx:ws:status',
    STREAM_OPEN:     'nx:stream:open',
    STREAM_CHUNK:    'nx:stream:chunk',
    STREAM_CLOSE:    'nx:stream:close',
    STREAM_ERROR:    'nx:stream:error',
    ACTIVITY_EVENT:  'nx:activity:event',
    API_PLAN_LOCKED: 'nx:api:plan_locked',
    API_RATE_LIMITED:'nx:api:rate_limited',
    API_ERROR:       'nx:api:error',
  };

  /* ── Helper: wrap a global, emit bus event, then call original ───── */
  function _wrap(name, busEvent, dataFn) {
    const orig = window[name];
    if (typeof orig !== 'function') return false;
    window[name] = function (...args) {
      try {
        const data = dataFn ? dataFn(...args) : { args };
        NxBus.emit(busEvent, data);
      } catch(_) {}
      return orig.apply(this, args);
    };
    window[name].__nxShimmed = true;
    window[name].__nxOriginal = orig;
    return true;
  }

  /* ── Helper: shim with NxState update ───────────────────────────── */
  function _wrapState(name, busEvent, stateFn) {
    const orig = window[name];
    if (typeof orig !== 'function') return false;
    window[name] = function (...args) {
      const result = orig.apply(this, args);
      try {
        const patch = stateFn ? stateFn(...args) : null;
        if (patch) {
          Object.entries(patch).forEach(([slice, data]) => {
            if (NxState[slice]) NxState[slice].set(data);
          });
        }
        if (busEvent) NxBus.emit(busEvent, { args });
      } catch(_) {}
      return result;
    };
    window[name].__nxShimmed = true;
    return true;
  }

  const _shimLog = [];

  function _record(name, ok) {
    _shimLog.push({ name, ok, at: Date.now() });
  }

  /* ══ Tab lifecycle migrations ════════════════════════════════════════
     Owner: workspace.js / NxWorkspace                                  */

  _record('nxSetTab', _wrap('nxSetTab', BUS.TAB_CHANGE,
    tab => ({ tab })
  ));

  _record('nxSwitchTab', _wrap('nxSwitchTab', BUS.TAB_CHANGE,
    tab => ({ tab })
  ));

  /* ══ Panel toggle migrations ═════════════════════════════════════════
     Owner: workspace.js / NxWorkspace                                  */

  _record('nxToggleLeft', _wrapState('nxToggleLeft', BUS.PANEL_TOGGLE,
    () => ({ panel: 'left' })
  ));

  _record('nxToggleRight', _wrapState('nxToggleRight', BUS.PANEL_TOGGLE,
    () => ({ panel: 'right' })
  ));

  _record('nxToggleBottom', _wrapState('nxToggleBottom', BUS.PANEL_TOGGLE,
    () => ({ panel: 'bottom' })
  ));

  /* ══ Toast migration ═════════════════════════════════════════════════
     Owner: nx-onboard.js / NdsToast
     All toast calls go through NxBus.TOAST which routes to NdsToast.  */

  _record('nxToast', _wrap('nxToast', BUS.TOAST,
    (msg, type, opts) => ({ msg, type: type || 'info', opts })
  ));

  /* ══ Agent run/stop migration ════════════════════════════════════════
     Owner: runtime.js                                                  */

  _record('nxRunOrStop', _wrap('nxRunOrStop', BUS.AGENT_START, () => ({})));

  /* ══ Palette migration ════════════════════════════════════════════════
     Owner: workspace.js                                                 */

  _record('nxOpenPalette', _wrap('nxOpenPalette', BUS.PALETTE_OPEN, () => ({})));

  /* ══ Settings migration ═══════════════════════════════════════════════
     Owner: ui.js                                                        */

  _record('openSettings', _wrap('openSettings', BUS.SETTINGS_OPEN, () => ({})));

  /* ══ Status migration ════════════════════════════════════════════════
     Owner: runtime.js                                                  */
  _record('nxSetStatus', _wrapState('nxSetStatus', BUS.AGENT_STATUS,
    (status, detail) => ({
      runtime: { agentStatus: status || 'idle', taskLabel: detail || '' }
    })
  ));

  /* ══ SSE / WebSocket migration ═══════════════════════════════════════
     Patch EventSource to emit WS_STATUS / STREAM_* events              */
  const _origES = window.EventSource;
  if (_origES && !_origES.__nxShimmed) {
    window.EventSource = function(url, cfg) {
      const es = new _origES(url, cfg);
      NxBus.emit(BUS.WS_STATUS, { state: 'connecting' });
      NxState.runtime.set({ wsState: 'connecting' });

      es.addEventListener('open', () => {
        NxBus.emit(BUS.WS_STATUS, { state: 'connected' });
        NxBus.emit(BUS.STREAM_OPEN, { url });
        NxState.runtime.set({ wsState: 'connected', streamOpen: true });
      });
      es.addEventListener('error', () => {
        NxBus.emit(BUS.WS_STATUS, { state: 'error' });
        NxBus.emit(BUS.STREAM_ERROR, { url });
        NxState.runtime.set({ wsState: 'error', streamOpen: false });
      });

      // Intercept common event types emitted by backend SSE
      ['log','thought','decision','tool_call','file_write','command','done','error','chunk'].forEach(type => {
        es.addEventListener(type, (e) => {
          let data;
          try { data = JSON.parse(e.data); } catch(_) { data = { raw: e.data }; }

          // Route to activity timeline
          const typeMap = {
            thought: 'think', decision: 'plan', tool_call: 'tool',
            file_write: 'file', command: 'run', error: 'error',
          };
          if (typeMap[type]) {
            NxBus.emit(BUS.ACTIVITY_EVENT, {
              type: typeMap[type],
              label: data.content || data.msg || data.tool || type,
              detail: data.file || data.cmd || data.detail || '',
            });
          }
          if (type === 'chunk') {
            NxBus.emit(BUS.STREAM_CHUNK, { text: data.content || data.text || '' });
            NxBus.emit(BUS.AGENT_STATUS, { status: 'thinking' });
          }
          if (type === 'done') {
            NxBus.emit(BUS.AGENT_DONE, {});
            NxBus.emit(BUS.STREAM_CLOSE, {});
            NxState.runtime.set({ agentStatus: 'done', streamOpen: false });
          }
          if (type === 'error') {
            NxBus.emit(BUS.AGENT_ERROR, { error: data });
            NxState.runtime.set({ agentStatus: 'error', lastError: data });
          }
        });
      });

      return es;
    };
    Object.assign(window.EventSource, _origES);
    window.EventSource.__nxShimmed = true;
    _record('EventSource', true);
  }

  /* ══ fetch() migration: catch API errors and route through bus ═══════ */
  const _origFetch = window.fetch;
  if (_origFetch && !_origFetch.__nxShimmed) {
    window.fetch = function(url, opts) {
      return _origFetch.call(this, url, opts).then(res => {
        if (res.status === 403) {
          NxBus.emit(BUS.API_PLAN_LOCKED, { url: String(url), status: 403 });
        } else if (res.status === 429) {
          NxBus.emit(BUS.API_RATE_LIMITED, { url: String(url), status: 429 });
        } else if (!res.ok && res.status >= 500) {
          NxBus.emit(BUS.API_ERROR, { url: String(url), status: res.status });
        }
        return res;
      }).catch(err => {
        NxBus.emit(BUS.API_ERROR, { url: String(url), error: err.message });
        NxState.runtime.set({ wsState: 'error', lastError: err.message });
        throw err;
      });
    };
    window.fetch.__nxShimmed = true;
    _record('fetch', true);
  }

  /* ── Expose shim diagnostics ────────────────────────────────────── */
  window.NxShim = {
    log:      () => _shimLog,
    shimmed:  () => _shimLog.filter(s => s.ok).map(s => s.name),
    missing:  () => _shimLog.filter(s => !s.ok).map(s => s.name),
    report:   () => {
      console.table(_shimLog);
      console.log(`Shimmed: ${_shimLog.filter(s=>s.ok).length}/${_shimLog.length}`);
    },
  };

  if (window.NX && window.NX.recordStage) {
    window.NX.recordStage('nx-shim-applied', { shimmed: NxShim.shimmed().length });
  }
})();
