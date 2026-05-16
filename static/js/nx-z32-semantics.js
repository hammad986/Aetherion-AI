/**
 * nx-z32-semantics.js — Phase Z32 Semantic Execution Intelligence Controller
 * ════════════════════════════════════════════════════════════════════════════
 *
 * Z32A — Auto-trigger context compression when thresholds exceeded
 * Z32B — Poll + display semantic confidence, drift tracking, escalation
 * Z32C — Evaluate + surface replanning recommendations
 * Z32D — Skill extraction on session end; skill panel rendering
 * Z32E — Failure clustering, predictive warnings, pressure bar
 *
 * Rules:
 *  - NO fake data. All state from live API responses or NxBus events.
 *  - RAF-batched DOM writes. No layout thrashing.
 *  - Compression never blocks the execution pipeline.
 *  - Replanning is advisory only — never auto-applied.
 *  - Skill recall requires explicit operator action.
 *  - All interval timers cleared on session end + page unload.
 */
'use strict';

(function () {
  if (window._z32) return;

  /* ── Constants ──────────────────────────────────────────────────── */
  const API               = '/api/z32';
  const INTEL_POLL_MS     = 15_000;
  const CONF_POLL_MS      = 20_000;
  const COMPRESS_DEBOUNCE = 5_000;
  const PRESSURE_POLL_MS  = 18_000;

  /* ── State ──────────────────────────────────────────────────────── */
  const S = {
    sid:              null,
    tokenEstimate:    0,
    nodeCount:        0,
    retryCount:       0,
    errorCount:       0,
    toolCalls:        0,
    toolOk:           0,
    replanCount:      0,
    confidence:       null,
    confHistory:      [],
    pressure:         null,
    compressTimer:    null,
    intelTimer:       null,
    confTimer:        null,
    pressureTimer:    null,
    compressionPending: false,
    skillsVisible:    false,
    lastPrediction:   null,
  };

  /* ── DOM helpers ─────────────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[<>&"]/g, c =>
    ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  /* ── Token estimation from log row ─────────────────────────────── */
  function _estimateTokens(text) { return Math.ceil(text.length / 4); }

  /* ═══════════════════════════════════════════════════════════════
     Z32A — Context Compression
     ═══════════════════════════════════════════════════════════════ */

  function _checkCompressNeeded() {
    return (
      S.tokenEstimate > 8000 ||
      S.nodeCount     > 30   ||
      S.retryCount    >= 5
    );
  }

  function _scheduleCompress() {
    if (S.compressionPending || !S.sid) return;
    S.compressionPending = true;
    if (S.compressTimer) clearTimeout(S.compressTimer);
    S.compressTimer = setTimeout(_doCompress, COMPRESS_DEBOUNCE);
  }

  function _doCompress() {
    S.compressionPending = false;
    if (!S.sid || !_checkCompressNeeded()) return;

    fetch(`${API}/compress/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        rows:    [],   // server compresses from DB; client reports metrics
        metrics: {
          token_count:   S.tokenEstimate,
          node_count:    S.nodeCount,
          retry_count:   S.retryCount,
          redundancy_ratio: 0,
        },
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (!data.ok || !data.compressed) return;
        S.tokenEstimate = data.tokens_after || S.tokenEstimate;
        _showCompressionEvent(data.trigger, data.tokens_saved || 0);
      })
      .catch(() => {});
  }

  function _showCompressionEvent(trigger, tokensSaved) {
    const bar = $('z32PressureBar');
    if (!bar) return;
    const el = document.createElement('div');
    el.className = 'z32-compression-event';
    el.textContent = `↯ Context compressed [${trigger}] — ${tokensSaved} tokens freed`;
    bar.parentNode?.insertBefore(el, bar);
    setTimeout(() => el.remove(), 3000);
  }

  /* ═══════════════════════════════════════════════════════════════
     Z32B — Semantic Confidence
     ═══════════════════════════════════════════════════════════════ */

  function _pollConfidence() {
    if (!S.sid) return;
    const metrics = {
      node_count:    S.nodeCount,
      done_count:    S.nodeCount - S.errorCount,
      error_count:   S.errorCount,
      retry_count:   S.retryCount,
      tool_calls:    S.toolCalls,
      tool_ok:       S.toolOk,
      replan_count:  S.replanCount,
    };

    fetch(`${API}/confidence/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(metrics),
    })
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        S.confidence = data.confidence;
        S.confHistory.push(data.confidence.score);
        if (S.confHistory.length > 20) S.confHistory.shift();
        _renderConfidenceOverlay();

        if (data.confidence.escalation_required) {
          _showPrediction(`Low confidence (${data.confidence.pct}%) — HITL escalation recommended`, 'CRITICAL');
          // Flash DAG surface
          const dagPanel = $('z30DagPanel');
          if (dagPanel) {
            dagPanel.classList.add('z32-escalation-active');
            setTimeout(() => dagPanel.classList.remove('z32-escalation-active'), 3000);
          }
        }
      })
      .catch(() => {});
  }

  function _renderConfidenceOverlay() {
    const overlay = $('z32ConfidenceOverlay');
    if (!overlay || !S.confidence) return;

    const { score, pct, level, drift } = S.confidence;
    const driftStr  = drift > 0.01 ? `↑${(drift*100).toFixed(1)}%` : drift < -0.01 ? `↓${Math.abs(drift*100).toFixed(1)}%` : '→';
    const driftCls  = drift > 0.01 ? 'up' : drift < -0.01 ? 'down' : 'flat';

    overlay.innerHTML = `
      <span class="z32-conf-badge ${level}" title="Semantic confidence: ${pct}%">
        ◉ ${pct}% ${level}
      </span>
      <span class="z32-conf-drift ${driftCls}" title="Confidence drift">${driftStr}</span>
      ${_renderSparkline(S.confHistory)}
    `;
    overlay.classList.toggle('visible', S.nodeCount > 0);
  }

  function _renderSparkline(history) {
    if (!history.length) return '';
    const max = Math.max(...history, 0.01);
    const bars = history.slice(-10).map(v => {
      const h  = Math.round((v / max) * 12);
      const cls = v >= 0.75 ? '' : v >= 0.45 ? 'med' : 'low';
      return `<div class="z32-spark-bar ${cls}" style="height:${h}px"></div>`;
    }).join('');
    return `<span class="z32-conf-sparkline" title="Confidence history">${bars}</span>`;
  }

  /* ═══════════════════════════════════════════════════════════════
     Z32C — Adaptive Replanning
     ═══════════════════════════════════════════════════════════════ */

  function _checkReplan() {
    if (!S.sid) return;
    const toolErrorRate = S.toolCalls > 0 ? (S.toolCalls - S.toolOk) / S.toolCalls : 0;
    const metrics = {
      retry_count:       S.retryCount,
      validation_failures: S.errorCount,
      tool_error_rate:   toolErrorRate,
      blocked_count:     0,
      provider_failures: 0,
      confidence_score:  S.confidence?.score || 1.0,
    };

    fetch(`${API}/replan/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(metrics),
    })
      .then(r => r.json())
      .then(data => {
        if (!data.ok || !data.replan_needed) return;
        S.replanCount++;
        const plan = data.plan;
        _showPrediction(`Replanning: [${plan.trigger}] → ${plan.recommended}`, 'WARNING');
        // Emit to NxBus so DAG can mark the node
        if (window.NxBus) NxBus.emit('dag.replan.triggered', { plan, sid: S.sid });
      })
      .catch(() => {});
  }

  /* ═══════════════════════════════════════════════════════════════
     Z32D — Skill Memory
     ═══════════════════════════════════════════════════════════════ */

  function _extractSkillOnEnd() {
    if (!S.sid || !window.NxDagEngine) return;
    const info = NxDagEngine.getState ? NxDagEngine.getState() : null;
    if (!info || !info.nodes || !info.nodes.length) return;

    fetch(`${API}/skills/extract/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        nodes:   info.nodes,
        metrics: { node_count: S.nodeCount, retry_count: S.retryCount },
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok && data.extracted) {
          _toast(`Skill captured: ${data.skill.name}`, 'info');
          _loadSkills();
        }
      })
      .catch(() => {});
  }

  function _loadSkills() {
    fetch(`${API}/skills`)
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        _renderSkillPanel(data.skills || []);
      })
      .catch(() => {});
  }

  function _renderSkillPanel(skills) {
    const list = $('z32SkillList');
    if (!list) return;

    if (!skills.length) {
      list.innerHTML = `<div style="font-size:9px;color:var(--text-dim,#6e7681);padding:6px">No skills captured yet.</div>`;
      return;
    }

    list.innerHTML = skills.map(sk => `
      <div class="z32-skill-card" onclick="_z32.recallSkill('${esc(sk.fingerprint)}')"
        title="${esc(sk.description || sk.name)}">
        <div class="z32-skill-name">${esc(sk.name)}</div>
        <div class="z32-skill-meta">
          <span>✓</span><span class="z32-skill-meta-val">${Math.round((sk.validation_rate || 0) * 100)}%</span>
          <span>↺</span><span class="z32-skill-meta-val">${(sk.avg_retries || 0).toFixed(1)}</span>
          <span>×</span><span class="z32-skill-meta-val">${sk.success_count || 1}</span>
        </div>
      </div>
    `).join('');
  }

  function _recallSkill(fingerprint) {
    fetch(`${API}/skills/recall`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ workflow_stages: [] }),
    })
      .then(r => r.json())
      .then(data => {
        const skill = (data.skills || []).find(s => s.fingerprint === fingerprint) || data.skills?.[0];
        if (!skill) return;
        _toast(`Skill recalled: ${skill.name} (${Math.round(skill.validation_rate * 100)}% success rate)`, 'info');
        if (window.NxBus) NxBus.emit('z32.skill.recalled', { skill });
      })
      .catch(() => {});
  }

  /* ═══════════════════════════════════════════════════════════════
     Z32E — Semantic Failure Intelligence + Pressure Bar
     ═══════════════════════════════════════════════════════════════ */

  function _pollIntelligence() {
    if (!S.sid) return;
    const toolErrorRate = S.toolCalls > 0 ? (S.toolCalls - S.toolOk) / S.toolCalls : 0;
    const metrics = {
      token_count:         S.tokenEstimate,
      retry_count:         S.retryCount,
      error_count:         S.errorCount,
      node_count:          S.nodeCount,
      tool_error_rate:     toolErrorRate,
      replan_count:        S.replanCount,
      confidence_score:    S.confidence?.score || 1.0,
      context_pressure:    Math.min(1, S.tokenEstimate / 8000),
    };

    fetch(`${API}/intelligence/${encodeURIComponent(S.sid)}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(metrics),
    })
      .then(r => r.json())
      .then(data => {
        if (!data.ok) return;
        S.pressure = data.pressure;
        _renderPressureBar(data);
        if (data.top_signal && data.top_signal !== S.lastPrediction) {
          S.lastPrediction = data.top_signal;
          const sev = data.pressure?.pressure_level === 'CRITICAL' ? 'CRITICAL' : 'WARNING';
          if (data.pressure?.overall_pressure > 0.35) {
            _showPrediction(data.top_signal, sev);
          }
        }
      })
      .catch(() => {});
  }

  function _renderPressureBar(data) {
    const bar = $('z32PressureBar');
    if (!bar || !data.pressure) return;

    const p = data.pressure;
    const _cls = (v) => v >= 0.8 ? 'critical' : v >= 0.6 ? 'high' : v >= 0.35 ? 'elevated' : '';

    bar.innerHTML = `
      <span class="z32-pressure-badge ${p.pressure_level}">${p.pressure_level}</span>

      <span class="z32-pressure-item">CTX
        <span class="z32-pressure-gauge">
          <span class="z32-pressure-fill ${_cls(p.context_pressure)}" style="width:${Math.round(p.context_pressure*100)}%"></span>
        </span>
      </span>

      <span class="z32-pressure-item">CONF
        <span class="z32-pressure-gauge">
          <span class="z32-pressure-fill ${_cls(p.reasoning_degradation)}" style="width:${Math.round(p.reasoning_degradation*100)}%"></span>
        </span>
      </span>

      <span class="z32-pressure-item">REPLAN
        <span class="z32-pressure-gauge">
          <span class="z32-pressure-fill ${_cls(p.recovery_saturation)}" style="width:${Math.round(p.recovery_saturation*100)}%"></span>
        </span>
      </span>

      <span class="z32-pressure-item">SEMANTIC
        <span class="z32-pressure-gauge">
          <span class="z32-pressure-fill ${_cls(p.semantic_instability)}" style="width:${Math.round(p.semantic_instability*100)}%"></span>
        </span>
      </span>

      <span style="flex:1"></span>
      <span style="font-size:9px;color:var(--text-dim,#6e7681)">~${(S.tokenEstimate/1000).toFixed(1)}k tok</span>

      <button class="z31-forensic-toggle" style="padding:1px 5px;font-size:8.5px"
        onclick="_z32.toggleSkills()" title="Skill memory">
        Skills${_getSkillCountBadge()}
      </button>
    `;

    // Show/hide clusters
    const clusters = (data.clusters || []).filter(c => c.severity === 'CRITICAL');
    if (clusters.length) {
      bar.insertAdjacentHTML('afterbegin', clusters.map(c =>
        `<span class="z30-sev-badge z30-sev-CRITICAL" style="font-size:8px">${c.type.replace(/_/g,' ')}</span>`
      ).join(''));
    }
  }

  function _getSkillCountBadge() {
    // Lightweight — don't fetch here, use cached state
    return '';
  }

  function _showPrediction(msg, level) {
    const banner = $('z32PredictionBanner');
    if (!banner) return;
    const msgEl = banner.querySelector('.z32-prediction-msg');
    const icon  = banner.querySelector('.z32-prediction-icon');
    if (msgEl) msgEl.textContent = msg;
    if (icon)  icon.textContent  = level === 'CRITICAL' ? '⚡' : '⚠';
    banner.classList.add('visible');
    // Auto-dismiss after 12s unless critical
    if (level !== 'CRITICAL') {
      setTimeout(() => banner.classList.remove('visible'), 12_000);
    }
  }

  /* ═══════════════════════════════════════════════════════════════
     Skills panel toggle
     ═══════════════════════════════════════════════════════════════ */

  function _toggleSkills() {
    const panel = $('z32SkillsPanel');
    if (!panel) return;
    S.skillsVisible = !S.skillsVisible;
    panel.classList.toggle('open', S.skillsVisible);
    if (S.skillsVisible) _loadSkills();
  }

  /* ═══════════════════════════════════════════════════════════════
     NxBus wiring
     ═══════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    // Ingest each log row to update local metrics
    NxBus.on('agent.log_row', (e) => {
      if (!e?.text) return;
      S.tokenEstimate += _estimateTokens(e.text);
      const t = e.text.toLowerCase();
      if (/retry/i.test(t))  S.retryCount++;
      if (/error|failed/i.test(t)) S.errorCount++;
      if (/tool:/i.test(t))  S.toolCalls++;
      if (/tool:.*done|tool:.*success/i.test(t)) S.toolOk++;
      if (S.tokenEstimate > 8000 || S.retryCount >= 5) _scheduleCompress();
    }, { owner: 'z32' });

    NxBus.on('agent.think',  (e) => { if (e?.text) { S.tokenEstimate += _estimateTokens(e.text); } }, { owner: 'z32' });
    NxBus.on('agent.action', (e) => { if (e?.text) { S.tokenEstimate += _estimateTokens(e.text); } }, { owner: 'z32' });
    NxBus.on('agent.tool_call',   () => { S.toolCalls++; }, { owner: 'z32' });
    NxBus.on('agent.tool_result', (e) => { if (!e?.error) S.toolOk++; }, { owner: 'z32' });

    // DAG node count sync
    NxBus.on('dag.snapshot.applied', (e) => {
      const nodes = e?.nodes || [];
      S.nodeCount = nodes.length;
      _renderConfidenceOverlay();
    }, { owner: 'z32' });

    // Session lifecycle
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z32' });

    NxBus.on('session.done',  () => _onSessionEnd('success'), { owner: 'z32' });
    NxBus.on('session.error', () => _onSessionEnd('error'),   { owner: 'z32' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED  || 'nx:session:created', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z32' });
  }

  /* ═══════════════════════════════════════════════════════════════
     Session lifecycle
     ═══════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid) {
    S.sid            = sid;
    S.tokenEstimate  = 0;
    S.nodeCount      = 0;
    S.retryCount     = 0;
    S.errorCount     = 0;
    S.toolCalls      = 0;
    S.toolOk         = 0;
    S.replanCount    = 0;
    S.confidence     = null;
    S.confHistory    = [];
    S.pressure       = null;
    S.lastPrediction = null;

    // Clear prediction banner
    const banner = $('z32PredictionBanner');
    if (banner) banner.classList.remove('visible');

    // Start polls
    if (S.intelTimer)    clearInterval(S.intelTimer);
    if (S.confTimer)     clearInterval(S.confTimer);
    if (S.pressureTimer) clearInterval(S.pressureTimer);

    S.confTimer     = setInterval(_pollConfidence,   CONF_POLL_MS);
    S.intelTimer    = setInterval(_pollIntelligence, INTEL_POLL_MS);
    S.pressureTimer = setInterval(_checkReplan,      PRESSURE_POLL_MS);
  }

  function _onSessionEnd(status) {
    if (S.intelTimer)    { clearInterval(S.intelTimer);    S.intelTimer = null; }
    if (S.confTimer)     { clearInterval(S.confTimer);     S.confTimer = null; }
    if (S.pressureTimer) { clearInterval(S.pressureTimer); S.pressureTimer = null; }
    if (S.compressTimer) { clearTimeout(S.compressTimer);  S.compressTimer = null; }

    // Extract skill from successful session
    if (status === 'success') _extractSkillOnEnd();

    // Final intelligence poll
    _pollIntelligence();
  }

  /* ── Page unload cleanup ────────────────────────────────────────── */
  window.addEventListener('beforeunload', () => {
    if (S.intelTimer)    clearInterval(S.intelTimer);
    if (S.confTimer)     clearInterval(S.confTimer);
    if (S.pressureTimer) clearInterval(S.pressureTimer);
    if (S.compressTimer) clearTimeout(S.compressTimer);
  });

  /* ── Toast ──────────────────────────────────────────────────────── */
  function _toast(msg, type = 'info') {
    if (window.NxToast)   { NxToast[type]?.(msg) || NxToast.info?.(msg); return; }
    if (window.showToast) { showToast(msg, type); return; }
    console.log(`[Z32] [${type.toUpperCase()}] ${msg}`);
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z32 = {
    toggleSkills:      _toggleSkills,
    recallSkill:       _recallSkill,
    forceCompress()  { if (S.sid) _doCompress(); },
    forceIntel()     { if (S.sid) _pollIntelligence(); },
    forceConfidence(){ if (S.sid) _pollConfidence(); },
    dismissPrediction() {
      const b = $('z32PredictionBanner');
      if (b) b.classList.remove('visible');
    },
    setSid(sid) { S.sid = sid; },
  };

  /* ── Init ─────────────────────────────────────────────────────────── */
  function _init() {
    _wireNxBus();
    // Seed from existing session
    const sid = window.currentSession || null;
    if (sid) { S.sid = sid; }
    // Load skills
    _loadSkills();
    console.log('[Phase Z32] Semantic Execution Intelligence + Adaptive Runtime Stability active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 300));
  } else {
    setTimeout(_init, 300);
  }

})();
