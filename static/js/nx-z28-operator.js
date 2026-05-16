/**
 * nx-z28-operator.js — Phase Z28 Operator Intelligence Layer
 * ═══════════════════════════════════════════════════════════
 * Z28A: Live Decision Feed
 * Z28B: Execution Timeline Intelligence
 * Z28C: Context + Memory Pressure Visibility
 * Z28D: Confidence + Execution Health Layer
 *
 * All data is operator-safe: observable facts only.
 * No chain-of-thought. No hidden reasoning. No AGI hype.
 */
(function () {
  'use strict';

  /* ── Constants ──────────────────────────────────────────────────── */
  const POLL_INTERVAL_MS   = 4000;
  const FEED_MAX_ITEMS     = 80;
  const HEALTH_POLL_MS     = 6000;
  const CTX_POLL_MS        = 5000;

  /* ── State ──────────────────────────────────────────────────────── */
  let _sid        = null;
  let _feedItems  = [];
  let _pollTimer  = null;
  let _healthTimer= null;
  let _ctxTimer   = null;
  let _mounted    = false;

  /* ── Decision type metadata ─────────────────────────────────────── */
  const DECISION_META = {
    model_selection:   { icon: '⚙',  label: 'Model Selected',    cls: 'info'    },
    retry:             { icon: '↻',  label: 'Retry',              cls: 'warn'    },
    escalation:        { icon: '⚠',  label: 'Escalated',          cls: 'danger'  },
    replanning:        { icon: '↺',  label: 'Replanned',          cls: 'warn'    },
    execution_pause:   { icon: '⏸',  label: 'Paused',             cls: 'warn'    },
    tool_rejection:    { icon: '✕',  label: 'Tool Rejected',      cls: 'warn'    },
    context_compression:{ icon: '⇲', label: 'Context Compressed', cls: 'info'    },
    provider_switch:   { icon: '⇄',  label: 'Provider Switch',    cls: 'warn'    },
    scheduler:         { icon: '⏱',  label: 'Scheduler',          cls: 'info'    },
  };

  const CONFIDENCE_META = {
    high:     { label: 'Stable',            cls: 'health-high',     dot: '#3fb950' },
    moderate: { label: 'Moderate',          cls: 'health-moderate', dot: '#d29922' },
    low:      { label: 'Uncertain',         cls: 'health-low',      dot: '#f85149' },
    critical: { label: 'Escalation Needed', cls: 'health-critical', dot: '#ff7b72' },
  };

  /* ─────────────────────────────────────────────────────────────────
   * MOUNT — called when Intel tab is first shown
   * ──────────────────────────────────────────────────────────────── */
  function mount(container, sid) {
    _sid = sid || _sid;
    if (_mounted && !sid) return;
    _mounted = true;

    container.innerHTML = _buildLayout();
    _startPolling();
    _refresh();
  }

  function unmount() {
    _clearPolling();
  }

  function setSid(sid) {
    if (sid === _sid) return;
    _sid = sid;
    _feedItems = [];
    if (_mounted) _refresh();
  }

  /* ─────────────────────────────────────────────────────────────────
   * LAYOUT BUILDER
   * ──────────────────────────────────────────────────────────────── */
  function _buildLayout() {
    return `
<div class="z28-root" id="z28Root">

  <!-- ── Z28D: Execution Health Bar ── -->
  <div class="z28-health-bar" id="z28HealthBar">
    <div class="z28-health-cell" id="z28hConfidence">
      <span class="z28-health-dot" id="z28hDot" style="background:#484f58"></span>
      <span class="z28-health-label">Confidence</span>
      <span class="z28-health-val" id="z28hConfVal">—</span>
    </div>
    <div class="z28-health-sep"></div>
    <div class="z28-health-cell" id="z28hRetries">
      <span class="z28-health-label">Retries</span>
      <span class="z28-health-val" id="z28hRetryVal">—</span>
    </div>
    <div class="z28-health-sep"></div>
    <div class="z28-health-cell" id="z28hProvider">
      <span class="z28-health-label">Provider</span>
      <span class="z28-health-val" id="z28hProvVal">—</span>
    </div>
    <div class="z28-health-sep"></div>
    <div class="z28-health-cell" id="z28hMission">
      <span class="z28-health-label">Mission Risk</span>
      <span class="z28-health-val" id="z28hRiskVal">—</span>
    </div>
    <div style="flex:1"></div>
    <div class="z28-health-cell">
      <span class="z28-health-label">Decisions</span>
      <span class="z28-health-val" id="z28hDecCount">0</span>
    </div>
  </div>

  <!-- ── Body: Two-column layout ── -->
  <div class="z28-body">

    <!-- LEFT: Decision Feed + Timeline -->
    <div class="z28-col-left">

      <!-- Z28B: Execution Timeline -->
      <div class="z28-section">
        <div class="z28-section-hdr">
          <span class="z28-section-title">Execution Timeline</span>
          <span class="z28-phase-badge" id="z28PhaseLabel">Idle</span>
        </div>
        <div class="z28-timeline-track" id="z28TimelineTrack">
          ${_buildTimelinePhases()}
        </div>
      </div>

      <!-- Z28A: Live Decision Feed -->
      <div class="z28-section z28-feed-section">
        <div class="z28-section-hdr">
          <span class="z28-section-title">Decision Feed</span>
          <div class="z28-feed-controls">
            <select class="z28-filter-sel" id="z28FeedFilter" onchange="window._z28.filterFeed(this.value)">
              <option value="">All decisions</option>
              <option value="retry">Retries</option>
              <option value="escalation">Escalations</option>
              <option value="provider_switch">Provider Switches</option>
              <option value="context_compression">Compression</option>
              <option value="replanning">Replanning</option>
            </select>
            <button class="z28-clear-btn" onclick="window._z28.clearFeed()" title="Clear feed">✕</button>
          </div>
        </div>
        <div class="z28-feed" id="z28Feed">
          <div class="z28-feed-empty" id="z28FeedEmpty">No decisions recorded yet. Start a task to see live operator intelligence.</div>
        </div>
      </div>

    </div>

    <!-- RIGHT: Context Pressure + Health Details -->
    <div class="z28-col-right">

      <!-- Z28C: Context Pressure -->
      <div class="z28-section">
        <div class="z28-section-hdr">
          <span class="z28-section-title">Context Pressure</span>
        </div>
        <div class="z28-ctx-body" id="z28CtxBody">

          <div class="z28-ctx-bar-wrap">
            <div class="z28-ctx-bar-label">
              <span>Token Load</span>
              <span id="z28CtxPct">—</span>
            </div>
            <div class="z28-ctx-bar-track">
              <div class="z28-ctx-bar-fill" id="z28CtxBarFill" style="width:0%"></div>
            </div>
          </div>

          <div class="z28-ctx-stats" id="z28CtxStats">
            <div class="z28-ctx-stat">
              <span class="z28-ctx-stat-label">Active Tokens</span>
              <span class="z28-ctx-stat-val" id="z28CtxTokens">—</span>
            </div>
            <div class="z28-ctx-stat">
              <span class="z28-ctx-stat-label">Episodes</span>
              <span class="z28-ctx-stat-val" id="z28CtxEpisodes">—</span>
            </div>
            <div class="z28-ctx-stat">
              <span class="z28-ctx-stat-label">Compressions</span>
              <span class="z28-ctx-stat-val" id="z28CtxCompressions">—</span>
            </div>
            <div class="z28-ctx-stat">
              <span class="z28-ctx-stat-label">Critical Notes</span>
              <span class="z28-ctx-stat-val" id="z28CtxNotes">—</span>
            </div>
          </div>

          <div class="z28-ctx-warnings" id="z28CtxWarnings"></div>

        </div>
      </div>

      <!-- Z28D: Confidence Signal History -->
      <div class="z28-section">
        <div class="z28-section-hdr">
          <span class="z28-section-title">Confidence History</span>
        </div>
        <div class="z28-conf-body" id="z28ConfBody">
          <div class="z28-conf-chart-wrap">
            <canvas id="z28ConfChart" class="z28-conf-chart" width="260" height="56"></canvas>
          </div>
          <div class="z28-conf-signals" id="z28ConfSignals">
            <div class="z28-feed-empty">No confidence data yet.</div>
          </div>
        </div>
      </div>

      <!-- Z28B: Runtime State Indicators -->
      <div class="z28-section">
        <div class="z28-section-hdr">
          <span class="z28-section-title">Runtime State</span>
        </div>
        <div class="z28-state-grid" id="z28StateGrid">
          ${_buildStateGrid()}
        </div>
      </div>

    </div>
  </div>
</div>
    `;
  }

  function _buildTimelinePhases() {
    const phases = [
      { id: 'planning',    label: 'Plan'     },
      { id: 'executing',   label: 'Execute'  },
      { id: 'validating',  label: 'Validate' },
      { id: 'retrying',    label: 'Retry'    },
      { id: 'compressing', label: 'Compress' },
      { id: 'escalated',   label: 'Escalate' },
      { id: 'done',        label: 'Done'     },
    ];
    return phases.map(p => `
      <div class="z28-tl-phase" id="z28phase-${p.id}" data-phase="${p.id}">
        <div class="z28-tl-dot"></div>
        <div class="z28-tl-label">${p.label}</div>
      </div>
    `).join('<div class="z28-tl-connector"></div>');
  }

  function _buildStateGrid() {
    const indicators = [
      { id: 'z28si-provider',   label: 'Active Provider',  val: '—' },
      { id: 'z28si-confidence', label: 'Confidence State', val: '—' },
      { id: 'z28si-retry',      label: 'Retry Pressure',   val: 'None' },
      { id: 'z28si-compress',   label: 'Compression',      val: 'Idle' },
      { id: 'z28si-scheduler',  label: 'Scheduler',        val: 'Idle' },
      { id: 'z28si-hitl',       label: 'HITL State',       val: 'Clear' },
    ];
    return indicators.map(i => `
      <div class="z28-si-cell">
        <span class="z28-si-label">${i.label}</span>
        <span class="z28-si-val" id="${i.id}">${i.val}</span>
      </div>
    `).join('');
  }

  /* ─────────────────────────────────────────────────────────────────
   * POLLING + REFRESH
   * ──────────────────────────────────────────────────────────────── */
  function _startPolling() {
    _clearPolling();
    _pollTimer   = setInterval(_pollDecisions, POLL_INTERVAL_MS);
    _healthTimer = setInterval(_pollHealth,    HEALTH_POLL_MS);
    _ctxTimer    = setInterval(_pollContext,   CTX_POLL_MS);
  }

  function _clearPolling() {
    if (_pollTimer)   { clearInterval(_pollTimer);   _pollTimer   = null; }
    if (_healthTimer) { clearInterval(_healthTimer); _healthTimer = null; }
    if (_ctxTimer)    { clearInterval(_ctxTimer);    _ctxTimer    = null; }
  }

  function _refresh() {
    _pollDecisions();
    _pollHealth();
    _pollContext();
  }

  async function _pollDecisions() {
    if (!_sid) return;
    try {
      const url = `/api/z28/decisions?sid=${encodeURIComponent(_sid)}&limit=80`;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.ok && Array.isArray(data.decisions)) {
        _applyDecisions(data.decisions);
      }
    } catch (_) {}
  }

  async function _pollHealth() {
    if (!_sid) return;
    try {
      const url = `/api/z28/health?sid=${encodeURIComponent(_sid)}`;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.ok) _applyHealth(data);
    } catch (_) {}
  }

  async function _pollContext() {
    if (!_sid) return;
    try {
      const url = `/api/z28/context-pressure?sid=${encodeURIComponent(_sid)}`;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.ok) _applyContext(data);
    } catch (_) {}
  }

  /* ─────────────────────────────────────────────────────────────────
   * APPLY: Decision Feed (Z28A)
   * ──────────────────────────────────────────────────────────────── */
  function _applyDecisions(decisions) {
    if (!decisions.length) return;

    const knownIds = new Set(_feedItems.map(i => i.record_id));
    let added = 0;

    for (const d of decisions) {
      if (!knownIds.has(d.record_id)) {
        _feedItems.push(d);
        added++;
      }
    }

    if (added === 0 && _feedItems.length === decisions.length) return;

    // Keep most recent FEED_MAX_ITEMS
    if (_feedItems.length > FEED_MAX_ITEMS) {
      _feedItems = _feedItems.slice(-FEED_MAX_ITEMS);
    }

    _renderFeed();
    _updateDecisionCount(_feedItems.length);
    _derivePhaseFromDecisions(_feedItems);
  }

  function _renderFeed(filterType) {
    const feed = document.getElementById('z28Feed');
    const empty = document.getElementById('z28FeedEmpty');
    if (!feed) return;

    const items = filterType
      ? _feedItems.filter(d => d.decision_type === filterType)
      : _feedItems;

    if (items.length === 0) {
      if (empty) empty.style.display = '';
      // Remove any existing cards
      feed.querySelectorAll('.z28-feed-item').forEach(el => el.remove());
      return;
    }
    if (empty) empty.style.display = 'none';

    // Render newest-first
    const sorted = [...items].reverse();
    const fragment = document.createDocumentFragment();

    sorted.forEach(d => {
      const existing = document.getElementById(`z28fi-${d.record_id}`);
      if (existing) return; // already rendered

      const meta = DECISION_META[d.decision_type] || { icon: '•', label: d.decision_type, cls: 'info' };
      const time = d.ts ? new Date(d.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
      const factors = (d.contributing_factors || []).slice(0, 3).map(f =>
        `<span class="z28-factor">${_escHtml(f)}</span>`
      ).join('');

      const el = document.createElement('div');
      el.className = `z28-feed-item z28-feed-${meta.cls}`;
      el.id = `z28fi-${d.record_id}`;
      el.innerHTML = `
        <div class="z28-fi-header">
          <span class="z28-fi-icon">${meta.icon}</span>
          <span class="z28-fi-type">${meta.label}</span>
          <span class="z28-fi-time">${time}</span>
        </div>
        <div class="z28-fi-summary">${_escHtml(d.summary || '')}</div>
        ${factors ? `<div class="z28-fi-factors">${factors}</div>` : ''}
        ${d.outcome ? `<div class="z28-fi-outcome">${_escHtml(d.outcome)}</div>` : ''}
      `;
      fragment.insertBefore(el, fragment.firstChild);
    });

    feed.insertBefore(fragment, feed.firstChild);
  }

  function filterFeed(type) {
    // Clear rendered items and re-render with filter
    const feed = document.getElementById('z28Feed');
    if (feed) {
      feed.querySelectorAll('.z28-feed-item').forEach(el => el.remove());
    }
    _renderFeed(type || undefined);
  }

  function clearFeed() {
    _feedItems = [];
    const feed = document.getElementById('z28Feed');
    if (feed) feed.querySelectorAll('.z28-feed-item').forEach(el => el.remove());
    const empty = document.getElementById('z28FeedEmpty');
    if (empty) empty.style.display = '';
    _updateDecisionCount(0);
  }

  /* ─────────────────────────────────────────────────────────────────
   * APPLY: Execution Health (Z28D)
   * ──────────────────────────────────────────────────────────────── */
  function _applyHealth(data) {
    const level      = data.confidence_level || 'high';
    const score      = typeof data.confidence_score === 'number' ? data.confidence_score : null;
    const retryCount = data.retry_count || 0;
    const provider   = data.active_provider || '—';
    const escalated  = data.hitl_active || false;
    const meta       = CONFIDENCE_META[level] || CONFIDENCE_META.high;

    // Health bar
    const dot = document.getElementById('z28hDot');
    const confVal = document.getElementById('z28hConfVal');
    if (dot) dot.style.background = meta.dot;
    if (confVal) {
      confVal.textContent = score !== null ? `${meta.label} (${(score * 100).toFixed(0)}%)` : meta.label;
      confVal.className = `z28-health-val ${meta.cls}`;
    }

    _setText('z28hRetryVal', retryCount > 0 ? `${retryCount}×` : 'None',
             retryCount > 2 ? 'health-low' : retryCount > 0 ? 'health-moderate' : '');
    _setText('z28hProvVal', provider);

    const riskLabel = escalated ? 'Escalated' : level === 'critical' ? 'Unstable'
                    : level === 'low' ? 'Risky' : level === 'moderate' ? 'Moderate' : 'Healthy';
    const riskCls   = escalated || level === 'critical' ? 'health-critical'
                    : level === 'low' ? 'health-low' : level === 'moderate' ? 'health-moderate' : 'health-high';
    _setText('z28hRiskVal', riskLabel, riskCls);

    // State indicators
    _setText('z28si-provider', provider);
    _setText('z28si-confidence', meta.label, meta.cls);
    _setText('z28si-retry', retryCount > 0 ? `${retryCount} retries` : 'None',
             retryCount > 2 ? 'health-low' : retryCount > 0 ? 'health-moderate' : '');
    _setText('z28si-hitl', escalated ? 'Waiting' : 'Clear',
             escalated ? 'health-critical' : '');

    // Confidence chart
    if (Array.isArray(data.confidence_history) && data.confidence_history.length > 0) {
      _drawConfChart(data.confidence_history);
      _renderConfSignals(data.signals || []);
    }
  }

  function _drawConfChart(scores) {
    const canvas = document.getElementById('z28ConfChart');
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const pad = 4;
    ctx.clearRect(0, 0, W, H);

    if (scores.length < 2) return;

    const min = 0, max = 1;
    const stepX = (W - pad * 2) / (scores.length - 1);

    // Grid line at 0.35 (HITL threshold) and 0.75
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    [0.35, 0.50, 0.75].forEach(v => {
      const y = pad + (1 - v) * (H - pad * 2);
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(W - pad, y); ctx.stroke();
    });

    // Line
    ctx.beginPath();
    scores.forEach((s, i) => {
      const x = pad + i * stepX;
      const y = pad + (1 - Math.max(0, Math.min(1, s))) * (H - pad * 2);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });

    // Color based on last score
    const last = scores[scores.length - 1];
    ctx.strokeStyle = last >= 0.75 ? '#3fb950' : last >= 0.50 ? '#d29922' : '#f85149';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Dots at key points
    scores.forEach((s, i) => {
      const x = pad + i * stepX;
      const y = pad + (1 - Math.max(0, Math.min(1, s))) * (H - pad * 2);
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fillStyle = s >= 0.75 ? '#3fb950' : s >= 0.50 ? '#d29922' : '#f85149';
      ctx.fill();
    });
  }

  function _renderConfSignals(signals) {
    const el = document.getElementById('z28ConfSignals');
    if (!el || !signals.length) return;
    el.innerHTML = signals.slice(-5).reverse().map(s => `
      <div class="z28-signal-row">
        <span class="z28-signal-type ${s.type === 'retry' ? 'warn' : s.type === 'hallucination' ? 'danger' : 'info'}">${s.type}</span>
        <span class="z28-signal-detail">${_escHtml(s.detail || '')}</span>
        <span class="z28-signal-penalty">-${((s.penalty || 0) * 100).toFixed(0)}%</span>
      </div>
    `).join('');
  }

  /* ─────────────────────────────────────────────────────────────────
   * APPLY: Context Pressure (Z28C)
   * ──────────────────────────────────────────────────────────────── */
  function _applyContext(data) {
    const pct     = data.budget_pct || 0;
    const tokens  = data.total_tokens || 0;
    const episodes= data.episodes || 0;
    const compressions = data.compression_count || 0;
    const notes   = data.critical_notes || 0;

    // Bar
    const fill = document.getElementById('z28CtxBarFill');
    const pctEl = document.getElementById('z28CtxPct');
    if (fill) {
      const pctNum = Math.min(100, pct);
      fill.style.width = `${pctNum}%`;
      fill.className = `z28-ctx-bar-fill${pctNum >= 85 ? ' z28-ctx-critical' : pctNum >= 65 ? ' z28-ctx-warn' : ''}`;
    }
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;

    _setText('z28CtxTokens', tokens > 0 ? tokens.toLocaleString() : '—');
    _setText('z28CtxEpisodes', episodes > 0 ? episodes : '—');
    _setText('z28CtxCompressions', compressions > 0 ? compressions : '—');
    _setText('z28CtxNotes', notes > 0 ? notes : '—');

    // Compression state indicator
    _setText('z28si-compress', episodes > 0 ? `${episodes} episodes` : 'Idle');

    // Context warnings
    const warnings = document.getElementById('z28CtxWarnings');
    if (warnings) {
      const msgs = [];
      if (pct >= 85) msgs.push({ cls: 'danger', text: 'Token budget critical — compression imminent' });
      else if (pct >= 65) msgs.push({ cls: 'warn', text: 'Context pressure elevated — long session detected' });
      if (compressions > 5) msgs.push({ cls: 'warn', text: `${compressions} compressions triggered — session may lose early context` });

      warnings.innerHTML = msgs.map(m =>
        `<div class="z28-ctx-warning z28-ctx-warning-${m.cls}">${m.text}</div>`
      ).join('');
    }
  }

  /* ─────────────────────────────────────────────────────────────────
   * TIMELINE PHASE DERIVATION (Z28B)
   * ──────────────────────────────────────────────────────────────── */
  function _derivePhaseFromDecisions(decisions) {
    if (!decisions.length) {
      _setPhase('idle');
      return;
    }
    const last = decisions[decisions.length - 1];
    const dt = last.decision_type;
    let phase = 'executing';

    if (dt === 'escalation')         phase = 'escalated';
    else if (dt === 'replanning')    phase = 'retrying';
    else if (dt === 'retry')         phase = 'retrying';
    else if (dt === 'context_compression') phase = 'compressing';
    else if (dt === 'model_selection') phase = 'executing';

    _setPhase(phase);
  }

  function _setPhase(phase) {
    const track = document.getElementById('z28TimelineTrack');
    if (!track) return;

    const phaseOrder = ['planning','executing','validating','retrying','compressing','escalated','done'];
    const phaseIdx = phaseOrder.indexOf(phase);

    track.querySelectorAll('.z28-tl-phase').forEach(el => {
      const elPhase = el.dataset.phase;
      const elIdx = phaseOrder.indexOf(elPhase);
      el.classList.remove('active', 'past', 'blocked');
      if (elPhase === phase) el.classList.add('active');
      else if (phaseIdx !== -1 && elIdx < phaseIdx) el.classList.add('past');
      else if (phase === 'escalated') el.classList.add('blocked');
    });

    const badge = document.getElementById('z28PhaseLabel');
    const labels = {
      idle:        'Idle',
      planning:    'Planning',
      executing:   'Executing',
      validating:  'Validating',
      retrying:    'Retrying',
      compressing: 'Compressing',
      escalated:   'Escalated',
      done:        'Complete',
    };
    if (badge) {
      badge.textContent = labels[phase] || phase;
      badge.className = `z28-phase-badge z28-phase-${phase}`;
    }
  }

  /* ─────────────────────────────────────────────────────────────────
   * SSE LIVE PUSH (Z28A real-time)
   * ──────────────────────────────────────────────────────────────── */
  function handleDecisionEvent(payload) {
    if (!payload || !payload.record_id) return;
    if (_feedItems.some(i => i.record_id === payload.record_id)) return;

    _feedItems.push(payload);
    if (_feedItems.length > FEED_MAX_ITEMS) _feedItems.shift();

    _renderFeedItem(payload);
    _updateDecisionCount(_feedItems.length);
    _derivePhaseFromDecisions(_feedItems);
  }

  function handleContextEvent(payload) {
    if (!payload) return;
    _applyContext({
      budget_pct:  payload.token_pct || 0,
      total_tokens: payload.total_tokens || 0,
      episodes:    payload.episodes || 0,
    });
  }

  function handleConfidenceWarning(payload) {
    if (!payload) return;
    const level = payload.level || 'low';
    const score = payload.score || 0;
    const meta  = CONFIDENCE_META[level] || CONFIDENCE_META.low;

    const dot = document.getElementById('z28hDot');
    const confVal = document.getElementById('z28hConfVal');
    if (dot) dot.style.background = meta.dot;
    if (confVal) {
      confVal.textContent = `${meta.label} (${(score * 100).toFixed(0)}%)`;
      confVal.className = `z28-health-val ${meta.cls}`;
    }
    _setText('z28si-confidence', meta.label, meta.cls);
  }

  function handleAgentDone() {
    _setPhase('done');
    _setText('z28si-hitl', 'Clear');
    _setText('z28si-retry', 'None');
  }

  function handleAgentStart() {
    _setPhase('planning');
    _feedItems = [];
    const feed = document.getElementById('z28Feed');
    if (feed) feed.querySelectorAll('.z28-feed-item').forEach(el => el.remove());
    const empty = document.getElementById('z28FeedEmpty');
    if (empty) empty.style.display = 'none';
    _updateDecisionCount(0);
  }

  function handleHitlRequired() {
    _setPhase('escalated');
    _setText('z28si-hitl', 'Waiting', 'health-critical');
  }

  function handleHitlResolved() {
    _setText('z28si-hitl', 'Resolved', 'health-moderate');
    setTimeout(() => _setText('z28si-hitl', 'Clear'), 4000);
  }

  function handleSchedulerEvent(payload) {
    if (!payload) return;
    const state = payload.state || 'active';
    _setText('z28si-scheduler', state === 'running' ? 'Running' : state === 'idle' ? 'Idle' : state);
  }

  /* ─────────────────────────────────────────────────────────────────
   * HELPERS
   * ──────────────────────────────────────────────────────────────── */
  function _renderFeedItem(d) {
    const feed = document.getElementById('z28Feed');
    const empty = document.getElementById('z28FeedEmpty');
    if (!feed) return;
    if (empty) empty.style.display = 'none';

    const existing = document.getElementById(`z28fi-${d.record_id}`);
    if (existing) return;

    const meta = DECISION_META[d.decision_type] || { icon: '•', label: d.decision_type, cls: 'info' };
    const time = d.ts ? new Date(d.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
    const factors = (d.contributing_factors || []).slice(0, 3).map(f =>
      `<span class="z28-factor">${_escHtml(f)}</span>`
    ).join('');

    const el = document.createElement('div');
    el.className = `z28-feed-item z28-feed-${meta.cls} z28-fi-new`;
    el.id = `z28fi-${d.record_id}`;
    el.innerHTML = `
      <div class="z28-fi-header">
        <span class="z28-fi-icon">${meta.icon}</span>
        <span class="z28-fi-type">${meta.label}</span>
        <span class="z28-fi-time">${time}</span>
      </div>
      <div class="z28-fi-summary">${_escHtml(d.summary || '')}</div>
      ${factors ? `<div class="z28-fi-factors">${factors}</div>` : ''}
      ${d.outcome ? `<div class="z28-fi-outcome">${_escHtml(d.outcome)}</div>` : ''}
    `;
    // Insert at top
    feed.insertBefore(el, feed.firstChild);
    // Remove animation class after transition
    setTimeout(() => el.classList.remove('z28-fi-new'), 600);
  }

  function _setText(id, text, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (cls !== undefined) {
      el.className = `z28-health-val ${cls}`;
    }
  }

  function _updateDecisionCount(n) {
    const el = document.getElementById('z28hDecCount');
    if (el) el.textContent = n;
  }

  function _escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ─────────────────────────────────────────────────────────────────
   * NxBus INTEGRATION
   * ──────────────────────────────────────────────────────────────── */
  function _wireBus() {
    if (!window.NxBus) return;

    NxBus.on('nx:z28:decision',     d => handleDecisionEvent(d));
    NxBus.on('nx:z28:context',      d => handleContextEvent(d));
    NxBus.on('nx:z28:health',       d => handleConfidenceWarning(d));
    NxBus.on('nx:hitl:required',    () => handleHitlRequired());
    NxBus.on('nx:hitl:resolved',    () => handleHitlResolved());
    NxBus.on(NxBus.EVENTS.AGENT_DONE,  () => handleAgentDone());
    NxBus.on(NxBus.EVENTS.AGENT_START, () => handleAgentStart());

    NxBus.on(NxBus.EVENTS.SESSION_CREATED, e => {
      if (e && e.session_id) setSid(e.session_id);
    });
  }

  /* ─────────────────────────────────────────────────────────────────
   * PUBLIC API
   * ──────────────────────────────────────────────────────────────── */
  window._z28 = {
    mount,
    unmount,
    setSid,
    filterFeed,
    clearFeed,
    handleDecisionEvent,
    handleContextEvent,
    handleConfidenceWarning,
    handleAgentDone,
    handleAgentStart,
    handleHitlRequired,
    handleHitlResolved,
    handleSchedulerEvent,
    setPhase: _setPhase,
  };

  // Wire NxBus after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireBus);
  } else {
    _wireBus();
  }

  // Sync session when tab is activated
  document.addEventListener('nx:tab:intel', () => {
    const sid = window.currentSession || null;
    if (sid && !_sid) setSid(sid);
    const el = document.getElementById('z28Root');
    if (!el) {
      const container = document.getElementById('nxTab-intel');
      if (container) mount(container, window.currentSession || null);
    } else if (sid !== _sid) {
      setSid(sid);
    }
    _refresh();
  });

})();
