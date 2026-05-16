/**
 * nx-agi-surface.js — AGI-Native Runtime Surface Controller
 * ══════════════════════════════════════════════════════════
 * Drives: cognition stream, trust bar, DAG nodes,
 *         HITL escalation overlay, memory visibility,
 *         and execution intelligence panels.
 *
 * CRITICAL RULE: Every visual element maps to REAL NxBus events.
 * No fake streaming. No decorative AI animations.
 * All state derived from SSE runtime events via NxBus.
 */
'use strict';

(function () {

  if (window.NxAgiSurface) return;

  /* ── Constants ──────────────────────────────────────────────────────── */
  const MAX_COG_ENTRIES    = 120;
  const MAX_DAG_NODES      = 60;
  const MAX_MEMORY_ITEMS   = 20;
  const MAX_TRUST_HISTORY  = 60;  // sparkline data points

  const TYPE_ICON = {
    thought:  '◈',
    decision: '✓',
    tool:     '⚙',
    error:    '✕',
    verify:   '◆',
    recall:   '↩',
    plan:     '▸',
  };

  /* ── Trust History (for sparkline) ─────────────────────────────────── */
  const _trustHistory = [];  // [{pct, ts}]

  /* ── State ──────────────────────────────────────────────────────────── */
  const _cogEntries  = [];   // { id, type, text, ts }
  const _dagNodes    = new Map(); // taskId → { label, state, dur, retries }
  const _memoryItems = [];
  let _trustPct      = 100;
  let _trustLevel    = 'high';
  let _orchState     = 'idle';
  let _orchStep      = '';
  let _streamingId   = null; // id of currently-streaming cog entry
  let _mounted       = false;
  let _activeRightTab = 'cog';

  /* ── DOM refs ───────────────────────────────────────────────────────── */
  const $  = (id) => document.getElementById(id);
  const $c = (cls, ctx = document) => ctx.querySelector('.' + cls);

  /* ── Mount ──────────────────────────────────────────────────────────── */
  function _mount() {
    if (_mounted) return;

    /* Inject right-panel tab system above the existing obs panel */
    const rightBody = $('nxRightBody');
    if (!rightBody) return;

    /* Build tab bar */
    const tabBar = document.createElement('div');
    tabBar.className = 'agi-right-tabs';
    tabBar.id = 'agiRightTabs';
    tabBar.innerHTML = `
      <button class="agi-right-tab active" data-tab="cog"    onclick="NxAgiSurface.switchTab('cog')">Cognition</button>
      <button class="agi-right-tab"        data-tab="trust"  onclick="NxAgiSurface.switchTab('trust')">Trust</button>
      <button class="agi-right-tab"        data-tab="dag"    onclick="NxAgiSurface.switchTab('dag')">DAG</button>
      <button class="agi-right-tab"        data-tab="memory" onclick="NxAgiSurface.switchTab('memory')">Memory</button>
    `;

    /* Build panes */
    const panesWrap = document.createElement('div');
    panesWrap.id = 'agiPanesWrap';
    panesWrap.style.cssText = 'flex:1;overflow:hidden;display:flex;flex-direction:column;min-height:0';

    panesWrap.innerHTML = `
      <!-- Cognition Stream Pane -->
      <div class="agi-right-tab-pane active" id="agiPane-cog">
        <div class="agi-section-hdr">
          <span><span class="agi-live-dot idle" id="agiCogDot"></span>Reasoning Stream</span>
          <button class="agi-section-hdr-action" onclick="NxAgiSurface.clearCog()" title="Clear">✕</button>
        </div>
        <div class="agi-cog-stream" id="agiCogStream">
          <div class="agi-cog-entry" data-type="thought">
            <span class="agi-cog-icon">◈</span>
            <span class="agi-cog-text" style="color:var(--nds-text-dim)">Waiting for agent activity…</span>
          </div>
        </div>
        <!-- Orchestration status footer -->
        <div id="agi-orch-status">
          <span class="agi-live-dot idle" id="agiOrchDot"></span>
          <span class="agi-orch-state idle" id="agiOrchState">Idle</span>
          <span class="agi-orch-step" id="agiOrchStep"></span>
        </div>
      </div>

      <!-- Trust & Explainability Pane -->
      <div class="agi-right-tab-pane" id="agiPane-trust">
        <div class="agi-section-hdr">Trust &amp; Validation</div>

        <div class="agi-trust-bar">
          <span class="agi-trust-label">Confidence</span>
          <div class="agi-trust-track">
            <div class="agi-trust-fill" id="agiTrustFill" data-level="high" style="width:100%"></div>
          </div>
          <span class="agi-trust-pct" id="agiTrustPct">100%</span>
        </div>

        <!-- Trust sparkline history -->
        <canvas id="agiTrustSparkline" height="28"
          style="display:block;width:100%;margin:2px 0 4px;border-radius:3px;background:var(--nds-surface-1)"
          title="Confidence history"></canvas>

        <div class="agi-section-hdr" style="margin-top:4px">Semantic Validation</div>
        <div class="agi-validation-row" id="agiValRow">
          <span class="agi-val-chip pending">Waiting…</span>
        </div>

        <div class="agi-section-hdr" style="margin-top:4px">Assumptions Detected</div>
        <div id="agiAssumptionsArea" style="padding:0 0 6px">
          <div style="padding:6px 10px;font-size:11px;color:var(--nds-text-dim)">None detected.</div>
        </div>

        <!-- HITL Escalation slot -->
        <div class="agi-section-hdr" style="margin-top:4px">Intervention Required</div>
        <div id="agiHitlArea">
          <div style="padding:6px 10px;font-size:11px;color:var(--nds-text-dim)">No escalations.</div>
        </div>
      </div>

      <!-- DAG Visualization Pane -->
      <div class="agi-right-tab-pane" id="agiPane-dag">
        <div class="agi-section-hdr">
          <span>Execution DAG</span>
          <span style="display:flex;gap:4px">
            <button class="agi-section-hdr-action" id="agiDagReplayBtn" onclick="NxAgiSurface.dagReplay()" title="Replay">⏮</button>
            <button class="agi-section-hdr-action" id="agiDagPrevBtn" onclick="NxAgiSurface.dagStep(-1)" title="Prev">‹</button>
            <button class="agi-section-hdr-action" id="agiDagNextBtn" onclick="NxAgiSurface.dagStep(1)" title="Next">›</button>
            <button class="agi-section-hdr-action" id="agiDagLiveBtn" onclick="NxAgiSurface.dagLive()" title="Live">●</button>
            <button class="agi-section-hdr-action" onclick="NxAgiSurface.clearDag()">✕</button>
          </span>
        </div>
        <div id="agiDagReplayInfo" style="display:none;padding:2px 10px;font-size:9px;color:var(--nds-yellow)">REPLAY MODE — step <span id="agiDagReplayIdx">0</span>/<span id="agiDagReplayTotal">0</span></div>
        <div class="agi-dag-surface" id="agiDagSurface" style="flex:1;min-height:100px">
          <div style="padding:8px;font-size:11px;color:var(--nds-text-dim)">No tasks running.</div>
        </div>
        <!-- Resource budget bars -->
        <div class="agi-section-hdr" style="padding-top:8px">Resource Budget</div>
        <div id="agiBudgetBars">
          <div class="agi-budget-bar">
            <span class="agi-budget-label">Tokens</span>
            <div class="agi-budget-track"><div class="agi-budget-fill" id="agiBudgetTokens" style="width:0%"></div></div>
            <span class="agi-budget-val" id="agiBudgetTokensVal">0</span>
          </div>
          <div class="agi-budget-bar">
            <span class="agi-budget-label">Steps</span>
            <div class="agi-budget-track"><div class="agi-budget-fill" id="agiBudgetSteps" style="width:0%"></div></div>
            <span class="agi-budget-val" id="agiBudgetStepsVal">0</span>
          </div>
        </div>
      </div>

      <!-- Memory Visibility Pane -->
      <div class="agi-right-tab-pane" id="agiPane-memory">
        <div class="agi-section-hdr">
          <span>Active Context / Memory</span>
          <button class="agi-section-hdr-action" onclick="NxAgiSurface.clearMemory()">✕</button>
        </div>
        <div class="agi-memory-section" id="agiMemoryList">
          <div style="font-size:11px;color:var(--nds-text-dim)">No memory retrievals.</div>
        </div>
      </div>
    `;


    /* Prepend tab bar + panes before existing obs panel */
    const obsPanelEl = $('nx-obs-panel');
    if (obsPanelEl) {
      rightBody.insertBefore(panesWrap, obsPanelEl);
      rightBody.insertBefore(tabBar, panesWrap);
    } else {
      rightBody.prepend(panesWrap);
      rightBody.prepend(tabBar);
    }

    _mounted = true;

    /* Mount SVG DAG engine if available */
    setTimeout(() => {
      if (window.NxDagEngine) NxDagEngine.mount('agiDagSurface');
    }, 150);
  }

  /* ── Tab Switching ──────────────────────────────────────────────────── */
  function switchTab(name) {
    _activeRightTab = name;
    document.querySelectorAll('.agi-right-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === name);
    });
    document.querySelectorAll('.agi-right-tab-pane').forEach(p => {
      p.classList.toggle('active', p.id === 'agiPane-' + name);
    });
  }

  /* ── Cognition Stream ────────────────────────────────────────────────── */
  function _addCogEntry(type, text, streaming = false) {
    const stream = $('agiCogStream');
    if (!stream) return;

    /* Remove placeholder */
    const ph = stream.querySelector('[data-placeholder]');
    if (ph) ph.remove();

    const id = 'cog_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    const ts = new Date().toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

    _cogEntries.push({ id, type, text, ts });
    if (_cogEntries.length > MAX_COG_ENTRIES) {
      _cogEntries.shift();
      stream.firstChild?.remove();
    }

    const el = document.createElement('div');
    el.className = 'agi-cog-entry' + (streaming ? ' streaming' : '');
    el.dataset.type = type;
    el.dataset.id   = id;
    el.innerHTML = `<span class="agi-cog-icon">${TYPE_ICON[type] || '·'}</span>
      <span class="agi-cog-text">${_esc(text)}</span>`;

    stream.appendChild(el);
    stream.scrollTop = stream.scrollHeight;

    if (streaming) _streamingId = id;
    return id;
  }

  function _appendCogStream(id, chunk) {
    const el = document.querySelector(`[data-id="${id}"] .agi-cog-text`);
    if (!el) return;
    el.textContent += chunk;
    const stream = $('agiCogStream');
    if (stream) stream.scrollTop = stream.scrollHeight;
  }

  function _finalizeCogStream(id) {
    const el = document.querySelector(`[data-id="${id}"]`);
    if (el) el.classList.remove('streaming');
    if (_streamingId === id) _streamingId = null;
  }

  function clearCog() {
    const stream = $('agiCogStream');
    if (stream) stream.innerHTML = '<div class="agi-cog-entry" data-type="thought" data-placeholder><span class="agi-cog-icon">◈</span><span class="agi-cog-text" style="color:var(--nds-text-dim)">Waiting for agent activity…</span></div>';
    _cogEntries.length = 0;
  }

  /* ── Orchestration Status ───────────────────────────────────────────── */
  function _setOrchState(state, step = '') {
    _orchState = state;
    _orchStep  = step;
    const stEl   = $('agiOrchState');
    const stepEl = $('agiOrchStep');
    const dotEl  = $('agiOrchDot');
    const cogDot = $('agiCogDot');

    if (stEl)   { stEl.className = 'agi-orch-state ' + state; stEl.textContent = _capitalize(state); }
    if (stepEl) stepEl.textContent = step;
    if (dotEl) {
      dotEl.className = 'agi-live-dot';
      if (state === 'running') dotEl.classList.add('streaming');
      else if (state === 'error' || state === 'blocked') dotEl.classList.add(state);
      else if (state === 'idle') dotEl.classList.add('idle');
    }
    if (cogDot) {
      cogDot.className = 'agi-live-dot';
      if (state === 'running') cogDot.classList.add('streaming');
      else if (state === 'error') cogDot.classList.add('error');
      else if (state === 'idle') cogDot.classList.add('idle');
    }
  }

  /* ── Trust Bar + Sparkline ─────────────────────────────────────────── */
  function _setTrust(pct, level) {
    _trustPct   = pct;
    _trustLevel = level || (pct >= 75 ? 'high' : pct >= 40 ? 'medium' : 'low');
    const fill  = $('agiTrustFill');
    const pctEl = $('agiTrustPct');
    if (fill)  { fill.style.width = pct + '%'; fill.dataset.level = _trustLevel; }
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    /* Record history and redraw sparkline */
    _trustHistory.push({ pct, ts: Date.now() });
    if (_trustHistory.length > MAX_TRUST_HISTORY) _trustHistory.shift();
    _renderSparkline();
  }

  function _renderSparkline() {
    const canvas = $('agiTrustSparkline');
    if (!canvas || !canvas.getContext) return;
    const dpr = window.devicePixelRatio || 1;
    const W   = canvas.offsetWidth  || canvas.parentElement?.offsetWidth || 200;
    const H   = 28;
    canvas.width  = W * dpr;
    canvas.height = H * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    if (_trustHistory.length < 2) return;

    const pts = _trustHistory;
    const xStep = W / (pts.length - 1);

    /* Fill area */
    ctx.beginPath();
    ctx.moveTo(0, H);
    pts.forEach((p, i) => ctx.lineTo(i * xStep, H - (p.pct / 100) * (H - 4) - 2));
    ctx.lineTo((pts.length - 1) * xStep, H);
    ctx.closePath();
    ctx.fillStyle = 'rgba(99,179,237,0.10)';
    ctx.fill();

    /* Line */
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = i * xStep;
      const y = H - (p.pct / 100) * (H - 4) - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    const last = pts[pts.length - 1];
    const lineColor = last.pct >= 75 ? '#3fb950' : last.pct >= 40 ? '#d29922' : '#f85149';
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    /* Current value dot */
    const lx = (pts.length - 1) * xStep;
    const ly = H - (last.pct / 100) * (H - 4) - 2;
    ctx.beginPath();
    ctx.arc(lx, ly, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = lineColor;
    ctx.fill();
  }

  /* ── Semantic Validation ────────────────────────────────────────────── */
  function _setValidation(checks) {
    // checks: [{ label, status }]  status: pass|fail|warn|pending
    const row = $('agiValRow');
    if (!row) return;
    row.innerHTML = checks.map(c =>
      `<span class="agi-val-chip ${c.status}">${c.label}</span>`
    ).join('');
  }

  /* ── Assumptions ────────────────────────────────────────────────────── */
  function _addAssumption(text) {
    const area = $('agiAssumptionsArea');
    if (!area) return;
    const placeholder = area.querySelector('[style*="text-dim"]');
    if (placeholder) placeholder.remove();

    const el = document.createElement('div');
    el.className = 'agi-assumption-block';
    el.innerHTML = `<div class="agi-assumption-label">⚠ Assumption</div>${_esc(text)}`;
    area.appendChild(el);
  }

  /* ── HITL Escalation ────────────────────────────────────────────────── */
  function _showHitl(reason, actions = ['approve', 'reject', 'retry']) {
    const area = $('agiHitlArea');
    if (!area) return;

    switchTab('trust'); // auto-focus trust tab

    const actionHtml = actions.map(a => {
      const labels = { approve: '✓ Approve', reject: '✕ Reject', retry: '↻ Retry' };
      return `<button class="agi-hitl-btn ${a}" onclick="NxAgiSurface._resolveHitl('${a}')">${labels[a] || a}</button>`;
    }).join('');

    area.innerHTML = `
      <div class="agi-hitl-escalation" id="agiHitlBlock">
        <div class="agi-hitl-header">
          <span class="agi-hitl-icon">⛔</span>
          <span class="agi-hitl-title">Agent Blocked — Intervention Required</span>
        </div>
        <div class="agi-hitl-reason">${_esc(reason)}</div>
        <div class="agi-hitl-actions">${actionHtml}</div>
      </div>`;
  }

  function _resolveHitl(action) {
    const block = $('agiHitlBlock');
    if (block) {
      block.classList.add('resolved');
      block.querySelector('.agi-hitl-title').textContent = 'Resolved — ' + _capitalize(action);
      block.querySelector('.agi-hitl-actions').innerHTML = '';
      block.querySelector('.agi-hitl-icon').textContent = '✓';
    }
    /* Publish resolution to runtime */
    if (window.NxBus) NxBus.emit('hitl.response', { action });
  }

  /* ── DAG Nodes ──────────────────────────────────────────────────────── */
  function _upsertDagNode(taskId, label, state, dur = '', retries = 0) {
    _dagNodes.set(taskId, { label, state, dur, retries });
    _renderDag();
  }

  function _renderDag() {
    const surf = $('agiDagSurface');
    if (!surf) return;
    if (_dagNodes.size === 0) {
      surf.innerHTML = '<div style="padding:8px;font-size:11px;color:var(--nds-text-dim)">No tasks running.</div>';
      return;
    }
    surf.innerHTML = '';
    let first = true;
    for (const [id, node] of _dagNodes) {
      if (!first) {
        const edge = document.createElement('div');
        edge.className = 'agi-dag-edge';
        surf.appendChild(edge);
      }
      first = false;

      const el = document.createElement('div');
      el.className = 'agi-dag-node';
      el.dataset.state = node.state;
      el.dataset.taskId = id;
      el.innerHTML = `
        <div class="agi-dag-node-dot"></div>
        <span class="agi-dag-node-label" title="${_esc(node.label)}">${_esc(node.label)}</span>
        <span class="agi-dag-node-dur">
          ${node.retries > 0 ? `<span class="agi-retry-badge">×${node.retries}</span> ` : ''}
          ${node.dur}
        </span>`;
      surf.appendChild(el);
    }
  }

  function clearDag() {
    _dagNodes.clear();
    _renderDag();
  }

  /* ── Memory (with explainability) ──────────────────────────────────── */
  function _addMemoryItem(type, content, trustLevel = 'medium', opts = {}) {
    const list = $('agiMemoryList');
    if (!list) return;
    const ph = list.querySelector('[style*="text-dim"]');
    if (ph) ph.remove();

    _memoryItems.push({ type, content, trustLevel });
    if (_memoryItems.length > MAX_MEMORY_ITEMS) {
      _memoryItems.shift();
      list.firstChild?.remove();
    }

    /* Staleness label */
    let staleLabel = '';
    if (opts.staleness_h != null) {
      const h = opts.staleness_h;
      staleLabel = h < 1 ? 'fresh' : h < 24 ? `${Math.round(h)}h ago` : `${Math.round(h/24)}d ago`;
    }

    const el = document.createElement('div');
    el.className = 'agi-memory-item';
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
        <span class="agi-memory-type ${_esc(type)}">${_esc(type.toUpperCase())}</span>
        <span class="agi-memory-trust-tag ${_esc(trustLevel)}">${_esc(trustLevel)}</span>
        ${staleLabel ? `<span style="font-size:9px;color:var(--nds-text-dim)">${_esc(staleLabel)}</span>` : ''}
        ${opts.confidence != null ? `<span style="font-size:9px;color:var(--nds-accent)">${Math.round(opts.confidence*100)}% rel</span>` : ''}
      </div>
      <span class="agi-memory-content">${_esc(content)}</span>
      ${opts.why ? `<div style="font-size:9px;color:var(--nds-text-dim);margin-top:2px;font-style:italic">Why: ${_esc(opts.why)}</div>` : ''}
      ${opts.source ? `<div style="font-size:9px;color:var(--nds-text-dim)">Source: ${_esc(opts.source)}</div>` : ''}`;
    list.appendChild(el);
  }

  function clearMemory() {
    _memoryItems.length = 0;
    const list = $('agiMemoryList');
    if (list) list.innerHTML = '<div style="font-size:11px;color:var(--nds-text-dim)">No memory retrievals.</div>';
  }

  /* ── Budget Bars ────────────────────────────────────────────────────── */
  function _updateBudget(tokens, tokenMax, steps, stepMax) {
    const tokFill = $('agiBudgetTokens');
    const tokVal  = $('agiBudgetTokensVal');
    const stFill  = $('agiBudgetSteps');
    const stVal   = $('agiBudgetStepsVal');

    const tokPct = tokenMax > 0 ? Math.min(100, (tokens / tokenMax) * 100) : 0;
    const stPct  = stepMax  > 0 ? Math.min(100, (steps  / stepMax)  * 100) : 0;

    if (tokFill) { tokFill.style.width = tokPct + '%'; tokFill.className = 'agi-budget-fill' + (tokPct > 85 ? ' crit' : tokPct > 65 ? ' warn' : ''); }
    if (tokVal)  tokVal.textContent = _fmtNum(tokens);
    if (stFill)  { stFill.style.width = stPct + '%'; stFill.className = 'agi-budget-fill' + (stPct > 85 ? ' crit' : stPct > 65 ? ' warn' : ''); }
    if (stVal)   stVal.textContent = steps;
  }

  /* ── NxBus Wiring ────────────────────────────────────────────────────── */
  function _wireEvents() {
    if (!window.NxBus) return;

    NxBus.on('agent.thought', e => {
      _setOrchState('running', 'thinking');
      _addCogEntry('thought', e.text || e.thought || String(e));
    });

    NxBus.on('agent.decision', e => {
      _addCogEntry('decision', e.text || e.decision || String(e));
    });

    NxBus.on('agent.plan', e => {
      _addCogEntry('plan', e.text || e.plan || String(e));
    });

    NxBus.on('agent.tool_call', e => {
      _setOrchState('running', 'tool:' + (e.name || '?'));
      _addCogEntry('tool', (e.name || 'tool') + (e.args ? ' — ' + JSON.stringify(e.args).slice(0, 80) : ''));
      _upsertDagNode('tool_' + (e.name || 'unknown'), e.name || 'tool', 'running');
    });

    NxBus.on('agent.tool_result', e => {
      const id = 'tool_' + (e.name || 'unknown');
      _upsertDagNode(id, e.name || 'tool', e.error ? 'error' : 'done');
    });

    NxBus.on('agent.task_start', e => {
      _setOrchState('running', e.label || 'executing');
      _upsertDagNode(e.id || e.task_id || Date.now(), e.label || 'Task', 'running');
    });

    NxBus.on('agent.task_done', e => {
      _upsertDagNode(e.id || e.task_id, e.label || 'Task', 'done', e.duration || '');
      _setOrchState('running', 'next');
    });

    NxBus.on('agent.task_error', e => {
      _upsertDagNode(e.id || e.task_id, e.label || 'Task', 'error');
      _addCogEntry('error', e.error || e.message || 'Task failed');
      _setOrchState('error', 'task_error');
    });

    NxBus.on('agent.task_retry', e => {
      const node = _dagNodes.get(e.id);
      if (node) _upsertDagNode(e.id, node.label, 'running', '', (node.retries || 0) + 1);
    });

    NxBus.on('session.done', () => {
      _setOrchState('done');
      _setTrust(_trustPct, _trustLevel);
    });

    NxBus.on('session.error', e => {
      _setOrchState('error');
      _addCogEntry('error', e.message || 'Session error');
    });

    NxBus.on('session.idle', () => {
      _setOrchState('idle');
    });

    /* Trust events */
    NxBus.on('trust.score', e => {
      _setTrust(e.pct ?? e.score ?? 100, e.level);
    });

    NxBus.on('trust.validation', e => {
      if (Array.isArray(e.checks)) _setValidation(e.checks);
    });

    NxBus.on('trust.assumption', e => {
      _addAssumption(e.text || e.assumption || String(e));
    });

    /* HITL */
    NxBus.on('hitl.required', e => {
      _showHitl(e.reason || e.message || 'Agent requires human confirmation.', e.actions);
    });

    NxBus.on('hitl.resolved', e => {
      _resolveHitl(e.action || 'resolved');
    });

    /* Memory with full explainability payload */
    NxBus.on('memory.retrieved', e => {
      _addMemoryItem(
        e.type || 'episodic',
        e.content || String(e),
        e.trust || 'medium',
        { why: e.why, source: e.source, staleness_h: e.staleness_h, confidence: e.confidence }
      );
    });

    /* Token budget */
    NxBus.on('budget.update', e => {
      _updateBudget(e.tokens || 0, e.token_max || 100000, e.steps || 0, e.step_max || 50);
    });

    /* Streaming thought chunks */
    NxBus.on('agent.thought_chunk', e => {
      if (_streamingId && e.id === _streamingId) {
        _appendCogStream(_streamingId, e.chunk);
      } else {
        const id = _addCogEntry('thought', e.chunk || '', true);
        if (e.id) {
          /* store mapping for future chunks */
          document.querySelector(`[data-id="${id}"]`)?.setAttribute('data-stream-id', e.id);
          _streamingId = id;
        }
      }
    });

    NxBus.on('agent.thought_end', e => {
      _finalizeCogStream(_streamingId);
    });
  }

  /* ── Utils ──────────────────────────────────────────────────────────── */
  function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function _capitalize(s) { return s ? s[0].toUpperCase() + s.slice(1) : ''; }
  function _fmtNum(n) { return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); }

  /* ── Init ───────────────────────────────────────────────────────────── */
  function init() {
    _mount();
    _wireEvents();
    /* Retry wiring once NxBus is available if it wasn't on load */
    if (!window.NxBus) {
      const t = setInterval(() => {
        if (window.NxBus) { _wireEvents(); clearInterval(t); }
      }, 300);
    }
  }

  /* ── DAG Replay Controls ────────────────────────────────────────────── */
  function _dagReplay() {
    if (window.NxDagEngine) {
      NxDagEngine.replayStart();
      _updateReplayInfo();
    }
  }
  function _dagStep(d) {
    if (window.NxDagEngine) {
      NxDagEngine.replayStep(d);
      _updateReplayInfo();
    }
  }
  function _dagLive() {
    if (window.NxDagEngine) {
      NxDagEngine.replayStop();
      const info = document.getElementById('agiDagReplayInfo');
      if (info) info.style.display = 'none';
    }
  }
  function _updateReplayInfo() {
    if (!window.NxDagEngine) return;
    const info = NxDagEngine.getReplayInfo();
    const bar  = document.getElementById('agiDagReplayInfo');
    if (bar) {
      bar.style.display = info.mode ? '' : 'none';
      const idxEl = document.getElementById('agiDagReplayIdx');
      const totEl = document.getElementById('agiDagReplayTotal');
      if (idxEl) idxEl.textContent = info.index + 1;
      if (totEl) totEl.textContent = info.total;
    }
  }

  /* ── Public API ─────────────────────────────────────────────────────── */
  window.NxAgiSurface = {
    init,
    switchTab,
    clearCog,
    clearDag,
    clearMemory,
    addCogEntry:    _addCogEntry,
    setTrust:       _setTrust,
    setValidation:  _setValidation,
    addAssumption:  _addAssumption,
    showHitl:       _showHitl,
    _resolveHitl,
    upsertDagNode:  _upsertDagNode,
    addMemoryItem:  _addMemoryItem,
    updateBudget:   _updateBudget,
    setOrchState:   _setOrchState,
    dagReplay:      _dagReplay,
    dagStep:        _dagStep,
    dagLive:        _dagLive,
  };

  /* Auto-init on DOM ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    /* Defer one tick to let workspace.js initialize first */
    setTimeout(init, 80);
  }

})();
