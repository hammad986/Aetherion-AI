/**
 * nx-devtools.js — Nexora Internal Developer Diagnostics Panel v1
 * ══════════════════════════════════════════════════════════════════════
 * Activated by: Ctrl+Shift+D or ?nx_devtools in URL
 * Renders a floating, collapsible panel showing live runtime state.
 *
 * Sections:
 *   - Boot stages & errors
 *   - NxBus: listener counts + event history
 *   - NxState: live workspace / runtime / UI slices
 *   - NxShim: migration coverage
 *   - DOM: node count, observer count
 *   - Performance: long tasks, animation budget
 *   - Active streams
 *   - Warnings
 *
 * Ownership: frontend/devtools (never shipped to users)
 * Production safety: panel only renders if NX.debugFlags or ?nx_devtools
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const ENABLED = window.location.search.includes('nx_devtools') ||
                  (window.NX && window.NX.debugFlags && window.NX.debugFlags.disable &&
                   window.location.hostname === 'localhost');

  if (!ENABLED) {
    // Still register hotkey to activate lazily
    document.addEventListener('keydown', e => {
      if (e.ctrlKey && e.shiftKey && e.key === 'D') { e.preventDefault(); _init(); }
    }, { once: true });
    return;
  }

  let _panel = null;
  let _timer = null;
  let _longTasks = [];

  /* ── Long Task Observer ─────────────────────────────────────────── */
  try {
    const po = new PerformanceObserver(list => {
      list.getEntries().forEach(e => {
        _longTasks.push({ dur: Math.round(e.duration), at: Date.now() });
        if (_longTasks.length > 20) _longTasks.shift();
      });
    });
    po.observe({ entryTypes: ['longtask'] });
  } catch(_) {}

  /* ── Panel CSS (injected once) ──────────────────────────────────── */
  function _injectCSS() {
    if (document.getElementById('nxDevtoolsCSS')) return;
    const s = document.createElement('style');
    s.id = 'nxDevtoolsCSS';
    s.textContent = `
      #nxDevtools {
        position:fixed; bottom:28px; right:12px; width:360px; max-height:70vh;
        background:rgba(10,10,18,0.97); border:1px solid #32324e;
        border-radius:10px; box-shadow:0 8px 32px rgba(0,0,0,0.7);
        font:12px/1.4 'JetBrains Mono','Fira Code',monospace;
        color:#a0a0c0; z-index:9998; overflow:hidden; display:flex;
        flex-direction:column;
      }
      #nxDevtools.nxDt-collapsed { max-height:36px; }
      #nxDtHeader {
        display:flex; align-items:center; justify-content:space-between;
        padding:0 10px; height:36px; background:#12121c;
        border-bottom:1px solid #22223a; cursor:pointer; flex-shrink:0;
        font-size:11px; font-weight:700; color:#6366f1; letter-spacing:.04em;
        user-select:none;
      }
      #nxDtHeader span { color:#3c3c60; font-weight:400; font-size:10px; }
      #nxDtBody { overflow-y:auto; flex:1; }
      .nxdt-section { border-bottom:1px solid #1d1d2e; }
      .nxdt-section-hdr {
        padding:5px 10px; font-size:10px; font-weight:700; color:#3c3c60;
        text-transform:uppercase; letter-spacing:.07em; cursor:pointer;
        display:flex; align-items:center; justify-content:space-between;
        background:#0e0e17;
      }
      .nxdt-section-hdr:hover { color:#6868a0; }
      .nxdt-section-body { padding:6px 10px; }
      .nxdt-row { display:flex; justify-content:space-between; padding:2px 0; font-size:11px; }
      .nxdt-key { color:#6868a0; }
      .nxdt-val { color:#ebebf5; }
      .nxdt-val.ok  { color:#22c55e; }
      .nxdt-val.warn{ color:#f0a030; }
      .nxdt-val.err { color:#f05149; }
      .nxdt-event { font-size:10px; color:#6868a0; padding:1px 0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .nxdt-event strong { color:#a0a0c0; }
      .nxdt-warn { color:#f0a030; font-size:10px; padding:1px 0; }
      .nxdt-btn { background:#1d1d2e; border:1px solid #32324e; border-radius:4px; color:#6868a0; font:10px monospace; padding:2px 7px; cursor:pointer; }
      .nxdt-btn:hover { color:#ebebf5; border-color:#6366f1; }
    `;
    document.head.appendChild(s);
  }

  /* ── Data collectors ─────────────────────────────────────────────── */
  function _domInfo() {
    return {
      nodes: document.querySelectorAll('*').length,
      observers: window.NdsPerf ? NdsPerf.getObserverCount() : '?',
    };
  }

  function _busInfo() {
    if (!window.NxBus) return null;
    const counts = NxBus.listenerCounts();
    const total = Object.values(counts).reduce((a,b)=>a+b, 0);
    const hist = NxBus.history(8);
    const leaks = NxBus.leakReport();
    return { counts, total, hist, leaks };
  }

  function _stateInfo() {
    if (!window.NxState) return null;
    return NxState.snapshot();
  }

  function _shimInfo() {
    if (!window.NxShim) return null;
    return { shimmed: NxShim.shimmed(), missing: NxShim.missing() };
  }

  function _perfInfo() {
    return {
      longTasks: _longTasks,
      budget: window.NdsPerf ? NdsPerf.BUDGET : {},
    };
  }

  function _bootInfo() {
    const nx = window.NX;
    if (!nx) return null;
    const d = nx.diagnostics || {};
    return {
      state: nx.state,
      stages: (d.stages || []).slice(-5),
      errors: (d.errors || []).slice(-5),
      degraded: d.degradedModules || [],
    };
  }

  /* ── HTML renderer ───────────────────────────────────────────────── */
  function _row(key, val, cls) {
    return `<div class="nxdt-row"><span class="nxdt-key">${key}</span><span class="nxdt-val ${cls||''}">${val}</span></div>`;
  }

  function _section(title, content, id) {
    return `<div class="nxdt-section">
      <div class="nxdt-section-hdr" onclick="document.getElementById('nxdts-${id}').style.display = document.getElementById('nxdts-${id}').style.display==='none'?'':'none'">
        ${title}<span>▾</span></div>
      <div class="nxdt-section-body" id="nxdts-${id}">${content}</div>
    </div>`;
  }

  function _render() {
    const bus   = _busInfo();
    const state = _stateInfo();
    const shim  = _shimInfo();
    const perf  = _perfInfo();
    const boot  = _bootInfo();
    const dom   = _domInfo();

    const sections = [];

    /* Boot */
    if (boot) {
      const errCls = boot.errors.length ? 'err' : 'ok';
      sections.push(_section('Boot', [
        _row('state', boot.state, boot.state === 'interactive' ? 'ok' : 'warn'),
        _row('stages', boot.stages.map(s=>s.name).join(' → '), ''),
        _row('errors', boot.errors.length || 0, errCls),
        _row('degraded', boot.degraded.length ? boot.degraded.map(d=>d.module).join(', ') : 'none', boot.degraded.length ? 'warn' : 'ok'),
      ].join(''), 'boot'));
    }

    /* NxBus */
    if (bus) {
      const busHTML = [
        _row('total listeners', bus.total, bus.total > 30 ? 'warn' : 'ok'),
        ...Object.entries(bus.counts).slice(0,8).map(([e,n]) =>
          `<div class="nxdt-event"><strong>${n}</strong> ${e.replace('nx:','')}</div>`
        ),
        '<div style="margin-top:4px;font-size:10px;color:#3c3c60">Recent events:</div>',
        ...bus.hist.slice().reverse().map(h =>
          `<div class="nxdt-event" title="${JSON.stringify(h.data||{})}"><strong>${h.event.replace('nx:','')}</strong></div>`
        ),
        bus.leaks[0] !== 'No obvious leaks'
          ? bus.leaks.map(l => `<div class="nxdt-warn">⚠ ${l}</div>`).join('')
          : '',
      ].join('');
      sections.push(_section('NxBus', busHTML, 'bus'));
    }

    /* NxState */
    if (state) {
      const ws = state.workspace;
      const rt = state.runtime;
      const wsHTML = [
        _row('leftOpen', ws.leftOpen, ''),
        _row('rightOpen', ws.rightOpen, ''),
        _row('leftW/rightW', `${ws.leftW}/${ws.rightW}`, ''),
        _row('preset', ws.activePreset || 'none', ''),
        _row('activeTab', ws.activeTab, ''),
        '<div style="margin-top:4px;color:#3c3c60;font-size:10px">Runtime:</div>',
        _row('agentStatus', rt.agentStatus, rt.agentStatus==='running'?'ok':rt.agentStatus==='error'?'err':''),
        _row('wsState', rt.wsState, rt.wsState==='connected'?'ok':rt.wsState==='error'?'err':'warn'),
        _row('model', rt.model || '—', ''),
        _row('tokens', rt.tokensUsed || 0, ''),
        _row('streamOpen', rt.streamOpen, rt.streamOpen ? 'ok' : ''),
      ].join('');
      sections.push(_section('NxState', wsHTML, 'state'));
    }

    /* DOM */
    sections.push(_section('DOM', [
      _row('nodes', dom.nodes, dom.nodes > 3500 ? 'err' : dom.nodes > 2500 ? 'warn' : 'ok'),
      _row('observers', dom.observers, Number(dom.observers) > 8 ? 'warn' : 'ok'),
    ].join(''), 'dom'));

    /* Perf */
    const longWarn = perf.longTasks.length ? perf.longTasks.slice(-3).map(t=>`<div class="nxdt-warn">⚠ long task: ${t.dur}ms</div>`).join('') : '<div style="color:#22c55e;font-size:10px">No long tasks</div>';
    sections.push(_section('Performance', longWarn, 'perf'));

    /* Shim */
    if (shim) {
      sections.push(_section('Migration Shim', [
        _row('shimmed', `${shim.shimmed.length}`, shim.shimmed.length > 5 ? 'ok' : 'warn'),
        _row('missing', shim.missing.join(', ') || 'none', shim.missing.length ? 'warn' : 'ok'),
      ].join(''), 'shim'));
    }

    /* Orchestration */
    if (_orchData) {
      const q = _orchData.queue || {};
      const a = _orchData.analytics || {};
      const m = _orchData.metrics || {};
      const orchHTML = [
        _row('active/queued', `${q.running||0} / ${q.queued||0}`, q.queued > 10 ? 'warn' : 'ok'),
        _row('tokens', a.total_tokens_consumed || 0, ''),
        _row('throughput (ev/s)', a.stream_throughput_events_sec || 0, ''),
        _row('sse_connections', m.active_sse_connections || 0, ''),
        _row('db_size', `${m.sqlite_db_size_kb||0} KB`, ''),
        '<div style="margin-top:4px;color:#3c3c60;font-size:10px">HITL Operations:</div>',
        `<button class="nxdt-btn" onclick="fetch('/api/v2/admin/hitl/exec_A/pause',{method:'POST'})" style="margin-right:4px">Pause Exec_A</button>`,
        `<button class="nxdt-btn" onclick="fetch('/api/v2/admin/hitl/exec_A/resume',{method:'POST'})">Resume Exec_A</button>`,
      ].join('');
      sections.push(_section('Orchestration', orchHTML, 'orch'));
    }

    /* Policy & Operator Controls */
    const policyHTML = `
      <div class="nxdt-row" style="margin-bottom: 6px;">
        <span class="nxdt-key">Max Tokens Policy</span>
        <input type="number" id="nx_dt_max_tokens" value="50000" style="background:#1d1d2e;color:#ebebf5;border:1px solid #32324e;width:60px;font-size:10px;">
        <button class="nxdt-btn" onclick="fetch('/api/v2/admin/policy/update', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_tokens_per_task: parseInt(document.getElementById('nx_dt_max_tokens').value)})})">Set</button>
      </div>
      <div class="nxdt-row" style="margin-bottom: 6px;">
        <span class="nxdt-key">Emergency Quarantine</span>
        <input type="text" id="nx_dt_quarantine_id" placeholder="exec_id" style="background:#1d1d2e;color:#ebebf5;border:1px solid #32324e;width:80px;font-size:10px;">
        <button class="nxdt-btn" style="color:#f05149;border-color:#f05149" onclick="fetch('/api/v2/admin/execution/'+document.getElementById('nx_dt_quarantine_id').value+'/quarantine', {method:'POST'})">Quarantine</button>
      </div>
      <div class="nxdt-row">
        <button class="nxdt-btn" onclick="window.NxTimeline && new window.NxTimeline('nxDtBody').loadExecution(document.getElementById('nx_dt_quarantine_id').value || 'exec_1')">View Replay UI (Timeline)</button>
        <button class="nxdt-btn" onclick="window.NxDAGVisualizer && new window.NxDAGVisualizer('nxDtBody').refresh('exec_1')">View Live DAG</button>
        <button class="nxdt-btn" onclick="window.open('/api/v2/admin/export/' + (document.getElementById('nx_dt_quarantine_id').value || 'exec_1'), '_blank')">Export Audit</button>
      </div>
    `;
    sections.push(_section('Runtime Policy Center', policyHTML, 'policy'));

    /* Footer actions */
    const footer = `<div class="nxdt-section-body" style="display:flex;gap:6px;flex-wrap:wrap;padding:8px 10px;">
      <button class="nxdt-btn" onclick="console.log(JSON.stringify(NxState.snapshot(),null,2))">Log State</button>
      <button class="nxdt-btn" onclick="console.log(JSON.stringify(NxBus.history(),null,2))">Log Bus History</button>
      <button class="nxdt-btn" onclick="NxShim&&NxShim.report()">Shim Report</button>
      <button class="nxdt-btn" onclick="window.NX&&console.log(nxDiagnosticsReport())">Boot Report</button>
      <button class="nxdt-btn" onclick="NxState.resetAll()">Reset State</button>
    </div>`;

    return sections.join('') + footer;
  }

  /* ── Panel lifecycle ─────────────────────────────────────────────── */
  let _collapsed = false;
  let _orchData = null;

  function _toggle() {
    _collapsed = !_collapsed;
    if (_panel) _panel.classList.toggle('nxDt-collapsed', _collapsed);
  }

  function _fetchOrchData() {
    fetch('/api/v2/admin/diagnostics')
      .then(r => r.json())
      .then(res => { if (res.ok) _orchData = res.data; })
      .catch(() => {});
  }

  function _startPolling() {
    if (_timer) return;
    _timer = setInterval(() => {
      const body = document.getElementById('nxDtBody');
      if (body && _panel && !_collapsed) {
        _fetchOrchData();
        body.innerHTML = _render();
      }
    }, 1500);
  }

  function _init() {
    if (_panel) { _panel.style.display = _panel.style.display === 'none' ? '' : 'none'; return; }
    _injectCSS();

    _panel = document.createElement('div');
    _panel.id = 'nxDevtools';
    _panel.setAttribute('role', 'complementary');
    _panel.setAttribute('aria-label', 'Developer diagnostics');
    _panel.innerHTML = `
      <div id="nxDtHeader" onclick="document.getElementById('nxDevtools').classList.toggle('nxDt-collapsed')">
        ◆ NX Devtools <span>Ctrl+Shift+D to toggle</span>
        <button class="nxdt-btn" onclick="event.stopPropagation();document.getElementById('nxDevtools').style.display='none'" title="Close">✕</button>
      </div>
      <div id="nxDtBody">${_render()}</div>`;

    document.body.appendChild(_panel);
    _startPolling();
  }

  /* ── Keyboard activation ─────────────────────────────────────────── */
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.shiftKey && e.key === 'D') { e.preventDefault(); _init(); }
  });

  /* Auto-init if ?nx_devtools in URL */
  if (window.location.search.includes('nx_devtools')) {
    if (Array.isArray(window.NX_BOOT_TASKS)) {
      window.NX_BOOT_TASKS.push(_init);
    } else {
      document.addEventListener('DOMContentLoaded', _init);
    }
  }

  window.NxDevtools = { init: _init, toggle: _toggle };
})();
