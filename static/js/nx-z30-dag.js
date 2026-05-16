/**
 * nx-z30-dag.js — Phase Z30 Execution Graph Controller
 * ══════════════════════════════════════════════════════════════════
 * Mounts and drives the live DAG visualization inside the Live tab.
 *
 * Responsibilities:
 *  - Mount NxDagEngine into z30DagSurface
 *  - Synthesize DAG nodes from live SSE log stream (no fake data)
 *  - Build node intelligence overlay (provider, model, retries, tokens, conf)
 *  - Detect runtime instability (retry storms, stuck nodes, context pressure)
 *  - Runtime heatmap overlays
 *  - Replay navigation controls (step/scrub through snapshot history)
 *  - Timeline ↔ DAG bidirectional sync
 *  - Forensic audit panel for failure branch inspection
 *
 * Rules:
 *  NO fake data. All state derives from live NxBus events + SSE log rows.
 *  RAF-batched DOM writes only. Zero layout thrashing.
 *  Replay integrity above visual effects.
 */
'use strict';

(function () {
  if (window._z30) return;

  /* ── Constants ──────────────────────────────────────────────────── */
  const RETRY_STORM_THRESHOLD  = 4;
  const STUCK_TIMEOUT_MS       = 90_000;
  const POLL_INTERVAL_MS       = 8_000;
  const INSTABILITY_POLL_MS    = 12_000;
  const DAG_HEIGHT_EXPANDED    = 220;
  const DAG_HEIGHT_COLLAPSED   = 32;

  /* ── Phase pattern maps (mirrors execution_graph.js patterns) ─── */
  const PHASE_MAP = {
    plan:  [/\b(plan|think|analyz|design|decompos|understand)\b/i, /\[STAGE\]\s*plan/i],
    code:  [/\b(cod|impl|generat|writ|build|creat)\b/i, /Writing\s+(?:file|to)/i, /Creating\s+file/i],
    debug: [/\b(debug|test|fix|verif|retry|patch)\b/i, /\[RETRY/i, /\[ERROR\]/i, /\[FALLBACK\]/i],
    tool:  [/\btool:/i, /calling\s+tool/i, /executing\s+tool/i, /\[TOOL\]/i],
    done:  [/\[Task finished/i, /exit=\d+\s+status=success/i, /Task completed successfully/i, /✅.*(?:done|complete|success)/i],
  };

  const PROVIDER_RE = /\b(openai|groq|anthropic|gemini|mistral|deepseek|grok|together|fireworks|cohere|nvidia|ollama)\b/i;
  const MODEL_RE    = /\b(gpt-4[^\s,]|gpt-3[^\s,]|claude[^\s,]|gemini[^\s,]|llama[^\s,]|mistral[^\s,]|deepseek[^\s,]|qwen[^\s,]|mixtral[^\s,])/i;
  const TOKEN_RE    = /(\d+)\s*(?:tokens?|tok)\b/i;
  const CONF_RE     = /confidence[:\s]+([0-9.]+)/i;

  /* ── Internal State ─────────────────────────────────────────────── */
  const S = {
    sid:           null,
    nodes:         {},    // { [phase]: nodeState }
    nodeOrder:     [],    // phase keys in insertion order
    nodeStartTs:   {},
    retryCount:    {},
    instability:   { overall: 'STABLE', retry_storm: false, stuck_node: false },
    selectedNode:  null,
    replayMode:    false,
    lastActivityTs: null,
    stuckTimer:    null,
    pollTimer:     null,
    instTimer:     null,
    rafPending:    false,
    mounted:       false,
    intelLogs:     {},    // { [phase]: [logLine, ...] }
  };

  /* ── DOM helpers ─────────────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  /* ── Phase detection ────────────────────────────────────────────── */
  function detectPhase(text) {
    for (const [phase, patterns] of Object.entries(PHASE_MAP)) {
      if (patterns.some(re => re.test(text))) return phase;
    }
    return null;
  }

  /* ── Severity from text+level ───────────────────────────────────── */
  function detectSeverity(text, level) {
    const t = text.toLowerCase();
    if (level === 'error' || /error|failed|traceback|exception/i.test(t)) return 'CRITICAL';
    if (/retry|fallback|timeout/i.test(t))  return 'WARNING';
    if (/warn|slow|pressure|compres/i.test(t)) return 'DEGRADED';
    if (level === 'success' || /✅|success/.test(t)) return 'INFO';
    return 'INFO';
  }

  /* ── Node upsert from a log row ─────────────────────────────────── */
  function _ingestRow(text, level, ts) {
    const phase = detectPhase(text);
    if (!phase) return;

    S.lastActivityTs = ts || Date.now();

    if (!S.nodes[phase]) {
      S.nodes[phase] = {
        id:         phase,
        label:      phase.charAt(0).toUpperCase() + phase.slice(1),
        state:      'running',
        stage:      phase,
        retries:    0,
        dur_ms:     0,
        start_ts:   ts || Date.now(),
        last_ts:    ts || Date.now(),
        severity:   'INFO',
        provider:   null,
        model:      null,
        tokens:     0,
        confidence: null,
        verified:   false,
        lines:      0,
        is_critical: phase === 'code' || phase === 'debug',
      };
      S.nodeOrder.push(phase);
      S.intelLogs[phase] = [];
    }

    const n = S.nodes[phase];
    n.lines++;
    n.last_ts  = ts || Date.now();
    n.dur_ms   = Math.round(n.last_ts - n.start_ts);
    n.severity = detectSeverity(text, level);

    // Provider/model extraction
    const pm = PROVIDER_RE.exec(text);
    if (pm) n.provider = pm[1].toLowerCase();
    const mm = MODEL_RE.exec(text);
    if (mm) n.model = mm[1].toLowerCase();

    // Token extraction
    const tm = TOKEN_RE.exec(text);
    if (tm) n.tokens = (n.tokens || 0) + parseInt(tm[1], 10);

    // Confidence
    const cm = CONF_RE.exec(text);
    if (cm) {
      let c = parseFloat(cm[1]);
      if (c > 1) c /= 100;
      n.confidence = c;
    }

    // Retry detection
    if (/retry|retrying/i.test(text)) {
      n.retries++;
      S.retryCount[phase] = (S.retryCount[phase] || 0) + 1;
      _checkRetryStorm();
    }

    // Terminal state
    if (level === 'error' || /error|failed/i.test(text)) {
      n.state = 'error';
    } else if (phase === 'done' || /completed successfully|exit=0|status=success/i.test(text)) {
      n.state = 'done';
      n.verified = true;
    }

    // Collect intel logs (keep last 20)
    if (text.length > 0) {
      S.intelLogs[phase].push({ ts: ts || Date.now(), text: text.slice(0, 200), level });
      if (S.intelLogs[phase].length > 20) S.intelLogs[phase].shift();
    }

    _scheduleRender();
  }

  /* ── Retry storm detection ──────────────────────────────────────── */
  function _checkRetryStorm() {
    const total = Object.values(S.retryCount).reduce((a, b) => a + b, 0);
    if (total >= RETRY_STORM_THRESHOLD && !S.instability.retry_storm) {
      S.instability.retry_storm = true;
      S.instability.overall = 'CRITICAL';
      _showInstabilityAlert('⚠ Retry storm detected — ' + total + ' retries across execution nodes');
    }
  }

  /* ── Stuck node detection ───────────────────────────────────────── */
  function _startStuckWatcher() {
    if (S.stuckTimer) clearInterval(S.stuckTimer);
    S.stuckTimer = setInterval(() => {
      if (!S.sid) return;
      const now = Date.now();
      if (S.lastActivityTs && (now - S.lastActivityTs) > STUCK_TIMEOUT_MS) {
        if (!S.instability.stuck_node) {
          S.instability.stuck_node = true;
          S.instability.overall = S.instability.overall === 'CRITICAL' ? 'CRITICAL' : 'DEGRADED';
          _showInstabilityAlert('⚠ Stuck node detected — no activity for ' + Math.round((now - S.lastActivityTs) / 1000) + 's');
        }
      }
    }, 15_000);
  }

  /* ── Push DAG snapshot to NxDagEngine ──────────────────────────── */
  function _pushToEngine() {
    if (!window.NxDagEngine) return;
    const nodes = S.nodeOrder.map((phase, i) => ({
      id:              phase,
      index:           i,
      label:           S.nodes[phase].label,
      state:           S.nodes[phase].state,
      stage:           phase,
      is_critical_path: S.nodes[phase].is_critical,
      retries:         S.nodes[phase].retries,
      duration_ms:     S.nodes[phase].dur_ms,
      semantic_confidence: S.nodes[phase].confidence,
      verified:        S.nodes[phase].verified,
      parent_id:       i > 0 ? S.nodeOrder[i - 1] : null,
    }));

    const edges = S.nodeOrder
      .slice(1)
      .map((phase, i) => ({ from: S.nodeOrder[i], to: phase }));

    NxDagEngine.applySnapshot({ nodes, edges });
  }

  /* ── RAF-batched render ─────────────────────────────────────────── */
  function _scheduleRender() {
    if (S.rafPending) return;
    S.rafPending = true;
    requestAnimationFrame(() => {
      S.rafPending = false;
      _pushToEngine();
      _renderHealthBar();
    });
  }

  /* ── Mount DAG engine into z30DagSurface ───────────────────────── */
  function _mountDagEngine() {
    const surface = $('z30DagSurface');
    if (!surface || !window.NxDagEngine) return;
    NxDagEngine.mount(surface);

    // Listen for node clicks from NxDagEngine → show intel panel
    if (window.NxBus) {
      NxBus.on('dag.node.selected', (e) => {
        if (e && e.node) _showIntelPanel(e.node.id);
      }, { owner: 'z30' });
    }
  }

  /* ── Intel Panel ────────────────────────────────────────────────── */
  function _showIntelPanel(nodeId) {
    const node = S.nodes[nodeId];
    if (!node) return;
    S.selectedNode = nodeId;

    const panel = $('z30IntelPanel');
    const title  = $('z30IntelTitle');
    const body   = $('z30IntelBody');
    if (!panel || !body) return;

    title.textContent = node.label;
    panel.classList.add('open');

    const dur = node.dur_ms >= 1000
      ? (node.dur_ms / 1000).toFixed(1) + 's'
      : node.dur_ms + 'ms';

    const confPct = node.confidence != null ? Math.round(node.confidence * 100) + '%' : '—';
    const confClass = node.confidence == null ? '' : node.confidence >= 0.75 ? '' : node.confidence >= 0.45 ? 'med' : 'low';

    const rows = [
      ['State',    `<span class="z30-intel-state-dot ${node.state}"></span>${node.state}`],
      ['Phase',    node.stage],
      ['Duration', dur],
      ['Retries',  node.retries],
      ['Provider', node.provider || '—'],
      ['Model',    node.model || '—'],
      ['Tokens',   node.tokens > 0 ? node.tokens.toLocaleString() : '—'],
      ['Severity', `<span class="z30-sev-badge z30-sev-${node.severity}">${node.severity}</span>`],
      ['Lines',    node.lines],
    ];

    if (node.confidence != null) {
      rows.push(['Confidence', `
        ${confPct}
        <div class="z30-conf-bar"><div class="z30-conf-fill ${confClass}" style="width:${Math.round(node.confidence*100)}%"></div></div>
      `]);
    }

    body.innerHTML = rows.map(([k, v]) => `
      <div class="z30-intel-row">
        <span class="z30-intel-key">${esc(k)}</span>
        <span class="z30-intel-val">${v}</span>
      </div>
    `).join('');

    // Append node logs
    const logs = S.intelLogs[nodeId] || [];
    if (logs.length) {
      body.insertAdjacentHTML('beforeend', `
        <div class="z30-node-log-section">
          <div class="z30-node-log-title">Recent Logs (${logs.length})</div>
          ${logs.slice(-10).map(l => `<div class="z30-node-log-line ${l.level === 'error' ? 'z30-audit-text CRITICAL' : ''}">${esc(l.text)}</div>`).join('')}
        </div>
      `);
    }
  }

  function _closeIntelPanel() {
    const panel = $('z30IntelPanel');
    if (panel) panel.classList.remove('open');
    S.selectedNode = null;
  }

  /* ── Health/Instability Bar render ──────────────────────────────── */
  function _renderHealthBar() {
    const bar = $('z30HealthBar');
    if (!bar) return;

    const totalRetries = Object.values(S.retryCount).reduce((a, b) => a + b, 0);
    const totalErrors  = Object.values(S.nodes).filter(n => n.state === 'error').length;

    const retryHeat = Math.min(1, totalRetries / 10);
    const errorHeat = Math.min(1, totalErrors  / 5);

    const _fillClass = (v) => v >= 0.7 ? 'danger' : v >= 0.35 ? 'warn' : '';

    bar.innerHTML = `
      <span class="z30-health-item">
        <span class="z30-sev-badge z30-sev-${S.instability.overall}">${S.instability.overall}</span>
      </span>
      <span class="z30-health-item">Retries
        <span class="z30-health-heatbar"><span class="z30-health-heatbar-fill ${_fillClass(retryHeat)}" style="width:${Math.round(retryHeat*100)}%"></span></span>
        ${totalRetries}
      </span>
      <span class="z30-health-item">Errors
        <span class="z30-health-heatbar"><span class="z30-health-heatbar-fill ${_fillClass(errorHeat)}" style="width:${Math.round(errorHeat*100)}%"></span></span>
        ${totalErrors}
      </span>
      <span class="z30-health-item">Nodes: <strong style="color:var(--text,#e6edf3)">${S.nodeOrder.length}</strong></span>
      ${S.instability.stuck_node ? '<span class="z30-sev-badge z30-sev-DEGRADED">STUCK</span>' : ''}
      ${S.instability.retry_storm ? '<span class="z30-sev-badge z30-sev-CRITICAL">RETRY STORM</span>' : ''}
      <span style="flex:1"></span>
      <span class="z30-timeline-sync-dot ${S.lastActivityTs ? 'active' : ''}" id="z30TimelineSyncDot" title="Timeline sync"></span>
    `;
  }

  /* ── Instability alert banner ────────────────────────────────────── */
  function _showInstabilityAlert(msg) {
    const el = $('z30InstabilityAlert');
    if (!el) return;
    el.querySelector('.z30-alert-msg').textContent = msg;
    el.classList.add('visible');
    setTimeout(() => el.classList.remove('visible'), 12_000);
  }

  /* ── Replay Controls ─────────────────────────────────────────────── */
  function _updateReplayBar() {
    if (!window.NxDagEngine) return;
    const info = NxDagEngine.getReplayInfo();
    const bar  = $('z30ReplayBar');
    const ctr  = $('z30ReplayCounter');
    const scrubber = $('z30ReplayScrubber');
    const btnPrev  = $('z30ReplayPrev');
    const btnNext  = $('z30ReplayNext');
    const btnStop  = $('z30ReplayStop');

    if (!bar || !info.total) return;

    if (info.total > 1) bar.classList.add('visible');

    if (ctr) ctr.textContent = `${info.index + 1} / ${info.total}`;
    if (scrubber) {
      scrubber.max   = Math.max(0, info.total - 1);
      scrubber.value = info.index;
    }
    if (btnPrev) btnPrev.disabled = info.index <= 0;
    if (btnNext) btnNext.disabled = info.index >= info.total - 1;
    if (btnStop) btnStop.style.display = info.mode ? 'inline-block' : 'none';

    const modeEl = $('z30ReplayMode');
    if (modeEl) modeEl.textContent = info.mode ? 'REPLAY' : 'LIVE';
  }

  /* ── DAG panel collapse/expand ──────────────────────────────────── */
  function toggleDagPanel() {
    const panel = $('z30DagPanel');
    if (!panel) return;
    const collapsed = panel.classList.toggle('collapsed');
    panel.style.height = collapsed ? DAG_HEIGHT_COLLAPSED + 'px' : DAG_HEIGHT_EXPANDED + 'px';
    const icon = $('z30DagToggleIcon');
    if (icon) icon.textContent = collapsed ? '▶' : '▼';
  }

  /* ── Backend polling for instability + DAG state ────────────────── */
  function _pollBackend() {
    if (!S.sid) return;
    fetch(`/api/z30/instability/${encodeURIComponent(S.sid)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        const inst = data.instability;
        S.instability.overall = inst.overall || 'STABLE';
        S.instability.retry_storm = inst.retry_storm || false;
        S.instability.stuck_node  = inst.stuck_node  || false;
        if (inst.retry_storm && !$('z30InstabilityAlert')?.classList.contains('visible')) {
          _showInstabilityAlert('⚠ Backend: Retry storm detected — ' + inst.retry_count + ' retries');
        }
        _renderHealthBar();
      })
      .catch(() => {});
  }

  function _pollDagBackend() {
    if (!S.sid) return;
    fetch(`/api/z30/dag/${encodeURIComponent(S.sid)}`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok || !data.dag) return;
        const { nodes, edges } = data.dag;
        if (!nodes || !nodes.length) return;
        // Only apply backend snapshot if we have no live nodes yet
        if (S.nodeOrder.length === 0 && window.NxDagEngine) {
          NxDagEngine.applySnapshot({ nodes, edges });
        }
      })
      .catch(() => {});
  }

  /* ── Session lifecycle ──────────────────────────────────────────── */
  function _onSessionStart(sid) {
    S.sid           = sid;
    S.nodes         = {};
    S.nodeOrder     = [];
    S.nodeStartTs   = {};
    S.retryCount    = {};
    S.intelLogs     = {};
    S.instability   = { overall: 'STABLE', retry_storm: false, stuck_node: false };
    S.lastActivityTs = Date.now();
    S.selectedNode  = null;
    S.replayMode    = false;

    _closeIntelPanel();
    _renderHealthBar();

    const alert = $('z30InstabilityAlert');
    if (alert) alert.classList.remove('visible');

    if (window.NxDagEngine) {
      NxDagEngine.clearNodes();
      NxDagEngine.setSession(sid);
    }

    // Expand DAG panel
    const panel = $('z30DagPanel');
    if (panel) {
      panel.classList.remove('collapsed');
      panel.style.height = DAG_HEIGHT_EXPANDED + 'px';
    }

    // Start instability poll
    if (S.instTimer) clearInterval(S.instTimer);
    S.instTimer = setInterval(_pollBackend, INSTABILITY_POLL_MS);

    _startStuckWatcher();

    // Initial backend DAG seed after 2s (gives live events priority)
    setTimeout(_pollDagBackend, 2000);
  }

  function _onSessionEnd(status) {
    // Finalize node states
    for (const phase of S.nodeOrder) {
      const n = S.nodes[phase];
      if (n.state === 'running') {
        n.state = status === 'success' ? 'done' : 'error';
        if (status === 'success') n.verified = true;
      }
    }
    _pushToEngine();
    _renderHealthBar();

    if (S.instTimer) { clearInterval(S.instTimer); S.instTimer = null; }
    if (S.stuckTimer) { clearInterval(S.stuckTimer); S.stuckTimer = null; }

    if (window.NxDagEngine) NxDagEngine.replaySave(S.sid);
    _updateReplayBar();
  }

  /* ── NxBus wiring ───────────────────────────────────────────────── */
  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    // Ingest log rows from the global event bus
    NxBus.on('agent.log_row', (e) => {
      if (e && e.text) _ingestRow(e.text, e.level || 'log', e.ts || Date.now());
    }, { owner: 'z30' });

    // SSE stream events from nx-sse-runtime.js
    NxBus.on('agent.think',   (e) => { if (e?.text) _ingestRow(e.text, 'log', Date.now()); }, { owner: 'z30' });
    NxBus.on('agent.action',  (e) => { if (e?.text) _ingestRow(e.text, 'log', Date.now()); }, { owner: 'z30' });
    NxBus.on('agent.output',  (e) => { if (e?.text) _ingestRow(e.text, 'log', Date.now()); }, { owner: 'z30' });
    NxBus.on('agent.tool_call',   (e) => { if (e?.name) _ingestRow(`[TOOL] tool:${e.name}`, 'log', Date.now()); }, { owner: 'z30' });
    NxBus.on('agent.tool_result', (e) => {
      if (e?.name) _ingestRow(`[TOOL] tool:${e.name} ${e.error ? 'failed' : 'done'}`, e.error ? 'error' : 'success', Date.now());
    }, { owner: 'z30' });

    // Session lifecycle
    NxBus.on('session.started', (e) => { _onSessionStart(e?.sid || e?.session_id || S.sid); }, { owner: 'z30' });
    NxBus.on('session.done',    ()  => { _onSessionEnd('success'); }, { owner: 'z30' });
    NxBus.on('session.error',   ()  => { _onSessionEnd('error'); }, { owner: 'z30' });

    const EVENTS = NxBus.EVENTS || {};
    NxBus.on(EVENTS.SESSION_CREATED  || 'nx:session:created',  (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z30' });
    NxBus.on(EVENTS.SESSION_RESTORED || 'nx:session:restored', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) { S.sid = sid; if (window.NxDagEngine) NxDagEngine.setSession(sid); _pollDagBackend(); }
    }, { owner: 'z30' });

    // DAG replay availability
    NxBus.on('dag.replay.available', (e) => {
      if (e?.count > 1) {
        const bar = $('z30ReplayBar');
        if (bar) bar.classList.add('visible');
        _updateReplayBar();
      }
    }, { owner: 'z30' });
  }

  /* ── Hook into ingestLogRow (fallback for legacy data path) ──────── */
  function _hookLegacyLog() {
    if (typeof ingestLogRow !== 'function') { setTimeout(_hookLegacyLog, 300); return; }
    if (ingestLogRow._z30Hooked) return;
    const _orig = ingestLogRow;
    window.ingestLogRow = function (row, area) {
      _orig(row, area);
      try {
        if (row && row.text) {
          _ingestRow(row.text, row.level || 'log', row.ts || Date.now());
        }
      } catch (_) {}
    };
    window.ingestLogRow._z30Hooked = true;
  }

  /* ── Timeline sync: clicking timeline row highlights DAG node ───── */
  function _wireTimelineSync() {
    document.addEventListener('click', (e) => {
      const row = e.target.closest('[data-timeline-phase]');
      if (!row) return;
      const phase = row.getAttribute('data-timeline-phase');
      if (phase && S.nodes[phase]) {
        _showIntelPanel(phase);
        // Pulse sync dot
        const dot = $('z30TimelineSyncDot');
        if (dot) { dot.classList.add('active'); setTimeout(() => dot && dot.classList.remove('active'), 2000); }
      }
    });
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z30 = {
    /* Called externally when user clicks a node (e.g., from DAG tooltip) */
    showNodeDetail: _showIntelPanel,
    closeIntelPanel: _closeIntelPanel,
    toggleDagPanel,

    /* Replay controls exposed to HTML buttons */
    replayStart() {
      if (!window.NxDagEngine) return;
      NxDagEngine.replayStart();
      S.replayMode = true;
      _updateReplayBar();
    },
    replayPrev() {
      if (!window.NxDagEngine) return;
      NxDagEngine.replayStep(-1);
      _updateReplayBar();
    },
    replayNext() {
      if (!window.NxDagEngine) return;
      NxDagEngine.replayStep(1);
      _updateReplayBar();
    },
    replayStop() {
      if (!window.NxDagEngine) return;
      NxDagEngine.replayStop();
      S.replayMode = false;
      _updateReplayBar();
    },
    replayScrub(val) {
      if (!window.NxDagEngine) return;
      const info = NxDagEngine.getReplayInfo();
      const delta = parseInt(val, 10) - info.index;
      if (delta !== 0) { NxDagEngine.replayStep(delta); _updateReplayBar(); }
    },
    replayExport() {
      if (!window.NxDagEngine) return;
      const json = NxDagEngine.replayExport(S.sid);
      const blob = new Blob([json], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `nx-dag-replay-${S.sid || 'session'}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    },

    /* Called externally to seed session */
    setSid(sid) { S.sid = sid; if (window.NxDagEngine) NxDagEngine.setSession(sid); },

    /* Manual node ingest (for testing/debugging) */
    ingestRow: _ingestRow,
  };

  /* ── Init ─────────────────────────────────────────────────────────── */
  function _init() {
    _mountDagEngine();
    _wireNxBus();
    _wireTimelineSync();
    _hookLegacyLog();
    _renderHealthBar();

    // Try to inherit active session from existing globals
    const sid = window.currentSession || null;
    if (sid) { S.sid = sid; if (window.NxDagEngine) NxDagEngine.setSession(sid); _pollDagBackend(); }

    console.log('[Phase Z30] Execution Graph + Structural Visibility active.');
  }

  // Boot via NX_LOAD_TASKS if available, else DOMContentLoaded
  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 150));
  } else {
    setTimeout(_init, 150);
  }

})();
