/**
 * nx-trust-ui.js — Aetherion Trust Engine Frontend Layer v1
 * ═══════════════════════════════════════════════════════════
 * Consumes:  NxBus 'nx:trust:signal', NxBus AGENT_DONE (with confidence)
 * Renders:   confidence badges, assumption banners, verification badges,
 *            honest completion reports, contradiction warnings, memory cautions
 *
 * Zero DOM assumptions — all elements are created dynamically and injected
 * into the observability panel's trust section if it exists, or as a
 * floating overlay if not.
 *
 * Rules:
 *   - NEVER shows fake 100% confidence
 *   - NEVER hides uncertainty or partial failures
 *   - All UI disappears when agent is idle (no persistent clutter)
 */
(function () {
  'use strict';

  /* ── Constants ─────────────────────────────────────────────────────── */
  const TRUST_PANEL_ID   = 'nxTrustPanel';
  const TRUST_TIMELINE_ID = 'nxTrustTimeline';
  const MAX_TIMELINE_ITEMS = 30;

  /* ── Confidence → visual mapping ──────────────────────────────────── */
  const CONFIDENCE_LEVELS = [
    { threshold: 0.85, label: 'HIGH',     color: '#22c55e', icon: '✓' },
    { threshold: 0.65, label: 'PROBABLE', color: '#f59e0b', icon: '~' },
    { threshold: 0.40, label: 'UNCERTAIN',color: '#f97316', icon: '?' },
    { threshold: 0.00, label: 'LOW',      color: '#ef4444', icon: '!' },
  ];

  const TRUST_TYPE_STYLES = {
    'assumption':          { color: '#8b5cf6', icon: '◎', label: 'Assuming' },
    'verification':        { color: '#f59e0b', icon: '⏳', label: 'Needs Verify' },
    'action_success':      { color: '#22c55e', icon: '✓', label: 'Verified' },
    'completion':          { color: '#06b6d4', icon: '■', label: 'Complete' },
    'memory_caution':      { color: '#f97316', icon: '⚠', label: 'Memory Caution' },
    'contradiction':       { color: '#ef4444', icon: '✗', label: 'Contradiction' },
    'semantic_validation': { color: '#06b6d4', icon: '◆', label: 'Semantic Check' },
  };

  function _getConfidenceLevel(score) {
    return CONFIDENCE_LEVELS.find(l => score >= l.threshold) || CONFIDENCE_LEVELS[3];
  }

  /* ── Panel bootstrap ──────────────────────────────────────────────── */
  function _ensurePanel() {
    let panel = document.getElementById(TRUST_PANEL_ID);
    if (panel) return panel;

    panel = document.createElement('div');
    panel.id = TRUST_PANEL_ID;
    panel.style.cssText = `
      position: fixed;
      bottom: 80px;
      right: 16px;
      width: 320px;
      max-height: 380px;
      background: rgba(15, 18, 25, 0.96);
      border: 1px solid rgba(139, 92, 246, 0.3);
      border-radius: 10px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
      z-index: 9990;
      display: flex;
      flex-direction: column;
      font-family: 'Inter', 'SF Pro Display', system-ui, sans-serif;
      overflow: hidden;
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.25s ease, transform 0.25s ease;
      pointer-events: none;
    `;

    panel.innerHTML = `
      <div style="
        padding: 8px 12px;
        border-bottom: 1px solid rgba(139,92,246,0.2);
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 11px;
        color: #a78bfa;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      ">
        <span>⬡</span>
        <span>Trust Engine</span>
        <span id="nxTrustConfidenceBadge" style="
          margin-left: auto;
          padding: 2px 8px;
          border-radius: 12px;
          font-size: 10px;
          background: rgba(139,92,246,0.15);
          color: #a78bfa;
        "></span>
      </div>
      <!-- trust timeline rendered below (scrollable feed) -->
      <div id="nxTrustMilestones" style="
        display: none;
        padding: 6px 12px;
        border-bottom: 1px solid rgba(139,92,246,0.15);
        font-size: 11px;
      "></div>
      <div id="nxTrustDAG" style="
        display: none;
        padding: 6px 12px;
        border-bottom: 1px solid rgba(139,92,246,0.15);
        max-height: 120px;
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: rgba(139,92,246,0.3) transparent;
        font-size: 11px;
      "></div>
      <div id="${TRUST_TIMELINE_ID}" style="
        overflow-y: auto;
        flex: 1;
        padding: 6px 0;
        scrollbar-width: thin;
        scrollbar-color: rgba(139,92,246,0.3) transparent;
      "></div>
      <div id="nxTrustCompletionBar" style="
        display: none;
        padding: 8px 12px;
        border-top: 1px solid rgba(139,92,246,0.15);
        font-size: 11px;
        color: #94a3b8;
      "></div>
    `;

    document.body.appendChild(panel);
    return panel;
  }

  function _showPanel() {
    const panel = _ensurePanel();
    panel.style.opacity = '1';
    panel.style.transform = 'translateY(0)';
    panel.style.pointerEvents = 'auto';
  }

  function _hidePanel() {
    const panel = document.getElementById(TRUST_PANEL_ID);
    if (!panel) return;
    panel.style.opacity = '0';
    panel.style.transform = 'translateY(10px)';
    panel.style.pointerEvents = 'none';
    // Clear timeline after hide animation
    setTimeout(() => {
      const tl = document.getElementById(TRUST_TIMELINE_ID);
      if (tl) tl.innerHTML = '';
      const badge = document.getElementById('nxTrustConfidenceBadge');
      if (badge) badge.textContent = '';
      const bar = document.getElementById('nxTrustCompletionBar');
      if (bar) { bar.style.display = 'none'; bar.textContent = ''; }
      const dag = document.getElementById('nxTrustDAG');
      if (dag) { dag.style.display = 'none'; dag.innerHTML = ''; }
      const ms = document.getElementById('nxTrustMilestones');
      if (ms) { ms.style.display = 'none'; ms.innerHTML = ''; }
    }, 400);
  }

  /* ── Planner UI Renderers ────────────────────────────────────────── */

  function _renderDAG(payload) {
    const container = document.getElementById('nxTrustDAG');
    if (!container) return;
    
    const nodes = payload.dag || [];
    if (nodes.length === 0) {
      container.style.display = 'none';
      return;
    }

    container.style.display = 'block';
    
    let html = `<div style="color:#94a3b8;margin-bottom:4px;font-weight:600;display:flex;justify-content:space-between;">
      <span>Execution Plan</span>
      <span>${payload.replan_count > 0 ? `<span style="color:#f59e0b">Replans: ${payload.replan_count}</span>` : ''}</span>
    </div>`;

    const grouped = {};
    nodes.forEach(n => {
      const stg = n.stage || 'Execution';
      if (!grouped[stg]) grouped[stg] = [];
      grouped[stg].push(n);
    });

    Object.keys(grouped).forEach(stageName => {
      html += `<div style="color:#cbd5e1;font-size:10px;text-transform:uppercase;margin-top:6px;border-bottom:1px solid #334155;padding-bottom:2px;margin-bottom:2px;">${_esc(stageName)}</div>`;
      
      grouped[stageName].forEach(n => {
        let color = '#64748b'; // PENDING
        let icon = '○';
        if (n.state === 'running') { color = '#3b82f6'; icon = '▶'; }
        else if (n.state === 'done') { color = '#22c55e'; icon = '✓'; }
        else if (n.state === 'failed') { color = '#ef4444'; icon = '✗'; }
        else if (n.state === 'blocked') { color = '#f59e0b'; icon = '⏸'; }
        else if (n.state === 'skipped') { color = '#64748b'; icon = '⏭'; }
        
        const retryTag = n.retry_count > 0 ? `<span style="color:#ef4444;margin-left:4px;">(Retry ${n.retry_count}/${n.retry_budget})</span>` : '';
        const critTag = n.is_critical_path ? `<span style="color:#8b5cf6;margin-left:4px;font-size:9px;">CRITICAL</span>` : '';
        const elapsed = n.elapsed_s ? `<span style="color:#475569;margin-left:4px;font-size:9px;">${n.elapsed_s}s</span>` : '';
        
        html += `
          <div style="display:flex;align-items:flex-start;gap:6px;margin-top:2px;color:${color};">
            <span style="flex-shrink:0;">${icon}</span>
            <div style="min-width:0;line-height:1.2;">
              <span style="${n.state==='done'?'text-decoration:line-through;opacity:0.7;':''}">${_esc(n.step_text)}</span>
              ${retryTag}${critTag}${elapsed}
            </div>
          </div>
        `;
      });
    });
    
    container.innerHTML = html;
  }

  function _renderMilestones(payload) {
    const container = document.getElementById('nxTrustMilestones');
    if (!container) return;

    const ms = payload.milestones || {};
    const names = Object.keys(ms);
    if (names.length === 0) {
      container.style.display = 'none';
      return;
    }

    container.style.display = 'block';
    
    let html = `<div style="display:flex;gap:4px;flex-wrap:wrap;">`;
    names.forEach(name => {
      const data = ms[name];
      const label = name.replace(/_/g, ' ').toUpperCase();
      const bg = data.achieved ? 'rgba(34,197,94,0.1)' : 'rgba(255,255,255,0.05)';
      const fg = data.achieved ? '#22c55e' : '#64748b';
      const border = data.achieved ? 'rgba(34,197,94,0.3)' : 'rgba(255,255,255,0.1)';
      
      html += `
        <div title="${data.evidence ? _esc(data.evidence) : 'Pending'}" style="
          padding: 2px 6px;
          border-radius: 4px;
          background: ${bg};
          border: 1px solid ${border};
          color: ${fg};
          font-size: 9px;
          font-weight: 600;
          cursor: help;
        ">
          ${data.achieved ? '✓' : '○'} ${label}
        </div>
      `;
    });
    html += `</div>`;
    container.innerHTML = html;
  }
  /* ── Multi-Agent Coordination Panel Renderer ──────────────────────── */
  const COORD_PANEL_ID = 'nxCoordinationPanel';

  function _renderCoordinationPanel(payload) {
    let panel = document.getElementById(COORD_PANEL_ID);
    if (!panel) {
      panel = document.createElement('div');
      panel.id = COORD_PANEL_ID;
      panel.style.cssText = `
        position: fixed;
        bottom: 470px;
        right: 16px;
        width: 340px;
        max-height: 280px;
        overflow-y: auto;
        background: rgba(10, 14, 22, 0.97);
        border: 1px solid rgba(59, 130, 246, 0.35);
        border-radius: 10px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.6);
        z-index: 9989;
        font-family: 'Inter', system-ui, monospace;
        font-size: 10px;
        padding: 8px;
        color: #94a3b8;
      `;
      document.body.appendChild(panel);
    }

    const agents = payload.active_agents || [];
    const res    = payload.resource_usage || {};
    const locks  = payload.active_locks  || [];
    const delegs = payload.delegation_graph || [];

    const agentStatusColor = { active: '#22c55e', blocked: '#f59e0b', idle: '#64748b', completed: '#3b82f6' };
    const roleIcon = { primary:'⬡', planner:'📐', coding:'💻', browser:'🌐', testing:'🧪', validation:'✓', recovery:'↩', memory_curator:'🧠', governance:'🏛' };

    let html = `<div style="color:#3b82f6;font-weight:700;font-size:11px;margin-bottom:6px;border-bottom:1px solid #1e3a5f;padding-bottom:4px;">
      ⚡ Agent Coordination — ${agents.length} agent${agents.length !== 1 ? 's' : ''} active
    </div>`;

    // Active agents roster
    if (agents.length > 0) {
      html += `<div style="margin-bottom:6px;">`;
      agents.forEach(a => {
        const col = agentStatusColor[a.status] || '#64748b';
        const icon = roleIcon[a.role] || '●';
        html += `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
            <span style="color:${col};">${icon} <span style="color:#cbd5e1;">${a.role}</span></span>
            <span style="color:${col};font-size:9px;text-transform:uppercase;">${a.status}</span>
          </div>
          ${a.current_step ? `<div style="color:#475569;font-size:9px;padding-left:14px;margin-bottom:2px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${_esc(a.current_step)}</div>` : ''}
        `;
      });
      html += `</div>`;
    }

    // Resource usage bar
    const globalTokenPct = res.global_token_limit
      ? Math.round((res.global_tokens_used || 0) / res.global_token_limit * 100)
      : 0;
    const tokenColor = globalTokenPct > 80 ? '#ef4444' : globalTokenPct > 60 ? '#f59e0b' : '#22c55e';
    html += `
      <div style="margin-bottom:4px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
          <span>Tokens</span><span style="color:${tokenColor};">${globalTokenPct}%</span>
        </div>
        <div style="background:rgba(255,255,255,0.05);border-radius:3px;height:4px;">
          <div style="background:${tokenColor};height:4px;border-radius:3px;width:${Math.min(globalTokenPct,100)}%;transition:width 0.3s;"></div>
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:4px;font-size:9px;">
        <span>🌐 ${res.active_browsers||0}/${res.max_browsers||2} browsers</span>
        <span>🖥 ${res.active_terminals||0}/${res.max_terminals||4} terminals</span>
        ${locks.length > 0 ? `<span style="color:#f59e0b;">🔒 ${locks.length} lock${locks.length>1?'s':''}</span>` : ''}
      </div>
    `;

    // Delegation graph mini-view
    if (delegs.length > 0) {
      html += `<div style="color:#7c3aed;font-size:9px;text-transform:uppercase;margin-top:4px;margin-bottom:2px;">Delegation Graph</div>`;
      delegs.slice(0, 5).forEach(d => {
        const statusColors = { completed:'#22c55e', verified:'#06b6d4', failed:'#ef4444', cancelled:'#64748b', running:'#3b82f6', pending:'#94a3b8', blocked:'#f59e0b', hitl_wait:'#8b5cf6' };
        const sc = statusColors[d.status] || '#64748b';
        html += `
          <div style="display:flex;justify-content:space-between;padding:1px 0;">
            <span style="color:#94a3b8;">${_esc((d.purpose || '').slice(0,40))}</span>
            <span style="color:${sc};font-size:9px;">${d.status}</span>
          </div>`;
      });
    }

    panel.innerHTML = html;
  }

  /* ── Timeline item renderer ───────────────────────────────────────── */

  function _appendTrustItem(signal) {
    const tl = document.getElementById(TRUST_TIMELINE_ID);
    if (!tl) return;

    const style   = TRUST_TYPE_STYLES[signal.type] || TRUST_TYPE_STYLES['assumption'];
    const confLvl = _getConfidenceLevel(signal.confidence ?? 0);
    const pct     = signal.confidence != null ? Math.round(signal.confidence * 100) : null;

    const item = document.createElement('div');
    item.style.cssText = `
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 5px 12px;
      border-bottom: 1px solid rgba(255,255,255,0.03);
      animation: nxTrustFadeIn 0.2s ease;
    `;

    item.innerHTML = `
      <span style="
        font-size: 12px;
        color: ${style.color};
        flex-shrink: 0;
        margin-top: 1px;
      ">${style.icon}</span>
      <div style="flex: 1; min-width: 0;">
        <div style="
          font-size: 10px;
          color: ${style.color};
          font-weight: 600;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          margin-bottom: 2px;
        ">${style.label}${pct != null ? ` <span style="color:${confLvl.color};margin-left:4px">${pct}%</span>` : ''}</div>
        <div style="
          font-size: 11px;
          color: #94a3b8;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        " title="${_esc(signal.message || '')}">${_esc(signal.message || '')}</div>
      </div>
      ${signal.verified === false && signal.type !== 'assumption'
        ? `<span style="font-size:9px;color:#f97316;flex-shrink:0;margin-top:1px">UNVERIFIED</span>`
        : signal.verified === true
        ? `<span style="font-size:9px;color:#22c55e;flex-shrink:0;margin-top:1px">OK</span>`
        : ''}
    `;

    tl.appendChild(item);

    // If this is a semantic_validation, append expandable evidence checklist
    if (signal.type === 'semantic_validation' && signal.evidence) {
      _appendEvidenceChecklist(tl, signal.evidence);
    }

    // Trim to max
    while (tl.children.length > MAX_TIMELINE_ITEMS) {
      tl.removeChild(tl.firstChild);
    }
    tl.scrollTop = tl.scrollHeight;
  }

  /* ── Evidence checklist renderer ─────────────────────────────────── */
  function _appendEvidenceChecklist(container, evidence) {
    if (!evidence || typeof evidence !== 'object') return;
    const entries = Object.entries(evidence);
    if (!entries.length) return;

    const wrapper = document.createElement('div');
    wrapper.style.cssText = `
      padding: 0 12px 6px 28px;
      border-bottom: 1px solid rgba(255,255,255,0.03);
    `;

    entries.slice(0, 6).forEach(([name, check]) => {
      const passed = check.passed;
      const ev     = (check.evidence || '').substring(0, 80);
      const row    = document.createElement('div');
      row.style.cssText = `
        display: flex;
        align-items: flex-start;
        gap: 6px;
        margin-top: 2px;
        font-size: 10px;
      `;
      row.innerHTML = `
        <span style="color:${ passed ? '#22c55e' : '#ef4444' };flex-shrink:0;">${ passed ? '✓' : '✗' }</span>
        <div style="min-width:0;">
          <span style="color:#64748b;">${_esc(name.replace(/_/g,' '))}</span>
          ${ ev ? `<span style="color:#475569;margin-left:4px;font-size:9px;">${_esc(ev)}</span>` : '' }
        </div>
      `;
      wrapper.appendChild(row);
    });
    container.appendChild(wrapper);
  }

  /* ── Confidence badge updater ─────────────────────────────────────── */
  function _updateConfidenceBadge(confidence) {
    const badge = document.getElementById('nxTrustConfidenceBadge');
    if (!badge || confidence == null) return;
    const lvl = _getConfidenceLevel(confidence);
    const pct  = Math.round(confidence * 100);
    badge.textContent = `${lvl.icon} ${pct}% ${lvl.label}`;
    badge.style.color = lvl.color;
    badge.style.background = `${lvl.color}18`;
  }

  /* ── Completion report renderer ───────────────────────────────────── */
  function _renderCompletionReport(payload) {
    const bar = document.getElementById('nxTrustCompletionBar');
    if (!bar) return;

    const confidence = payload.confidence ?? 0;
    const lvl        = _getConfidenceLevel(confidence);
    const pct        = Math.round(confidence * 100);
    const done       = payload.completed_steps ?? '?';
    const total      = payload.total_steps ?? '?';
    const status     = (payload.status || 'done').toUpperCase();

    bar.style.display = 'block';
    bar.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
        <span style="color:${lvl.color};font-weight:600;">${lvl.icon} ${status}</span>
        <span style="color:#64748b;">${done}/${total} steps</span>
        <span style="color:${lvl.color};">${pct}% confidence</span>
      </div>
      <div style="
        height: 3px;
        background: rgba(255,255,255,0.06);
        border-radius: 2px;
        overflow: hidden;
      ">
        <div style="
          height:100%;
          width:${pct}%;
          background: ${lvl.color};
          border-radius:2px;
          transition: width 0.6s ease;
        "></div>
      </div>
      ${ confidence < 0.70 ? `
      <div style="margin-top:6px;display:flex;align-items:center;justify-content:space-between;">
        <div style="font-size:10px;color:#f97316;">⚠ Low confidence — manual review recommended</div>
        <button id="nxTrustExplainBtn" onclick="NxTrustUI.showExplain()" style="
          padding: 3px 10px;
          background: rgba(249,115,22,0.12);
          border: 1px solid rgba(249,115,22,0.35);
          border-radius: 6px;
          color: #f97316;
          font-size: 10px;
          font-weight: 600;
          cursor: pointer;
          font-family: inherit;
          white-space: nowrap;
        ">⚡ Why? →</button>
      </div>` : '' }
      <div style="
        margin-top: 6px;
        font-size: 10px;
        color: #475569;
        display: flex;
        justify-content: space-between;
      ">
        <span>Source: execution metadata + semantic checks</span>
        <span style="color:#334155;">NOT LLM self-opinion</span>
      </div>
    `;
  }

  /* ── Helpers ──────────────────────────────────────────────────────── */
  function _esc(s) {
    return String(s)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  /* ── CSS animation injection ──────────────────────────────────────── */
  function _injectStyles() {
    if (document.getElementById('nxTrustStyles')) return;
    const style = document.createElement('style');
    style.id = 'nxTrustStyles';
    style.textContent = `
      @keyframes nxTrustFadeIn {
        from { opacity: 0; transform: translateX(8px); }
        to   { opacity: 1; transform: translateX(0); }
      }
    `;
    document.head.appendChild(style);
  }

  /* ── Session-bound signal accumulator (isolation + explainability) ── */
  let _currentSessionSignals = [];  // cleared on every session change
  let _lastCompletionPayload = null;

  /* ── NxBus event subscriptions ────────────────────────────────────── */
  function _init() {
    if (!window.NxBus) {
      setTimeout(_init, 200);
      return;
    }
    _injectStyles();

    // Trust signals from the backend Trust Engine
    NxBus.on('nx:trust:signal', (signal) => {
      _showPanel();
      _ensurePanel();
      _appendTrustItem(signal);
      // Accumulate for this session's explainability record
      _currentSessionSignals.push({ ...signal, _ts: Date.now() });
      if (_currentSessionSignals.length > 100) _currentSessionSignals.shift();
      if (signal.confidence != null) {
        _updateConfidenceBadge(signal.confidence);
      }
    });

    // Completion events — show honest report + confidence
    NxBus.on('nx:agent:done', (payload) => {
      _lastCompletionPayload = payload;  // Store for explainability
      if (payload.confidence != null) {
        _renderCompletionReport(payload);
        _updateConfidenceBadge(payload.confidence);
        _appendTrustItem({
          type: 'completion',
          verified: payload.confidence >= 0.70,
          confidence: payload.confidence,
          message: payload.output || `Task ${payload.status || 'done'}`,
        });
      }
      if (payload.confidence == null || payload.confidence >= 0.70) {
        setTimeout(_hidePanel, 30000);
      }
    });

    // Execution Planner: DAG updates
    NxBus.on('nx:dag:update', (payload) => {
      _showPanel();
      _ensurePanel();
      _renderDAG(payload);
      if (payload.avg_confidence != null) {
        _updateConfidenceBadge(payload.avg_confidence);
      }
    });

    // Execution Planner: Milestone updates
    NxBus.on('nx:milestone:update', (payload) => {
      _showPanel();
      _ensurePanel();
      _renderMilestones(payload);
    });

    // HITL clarification — surface as uncertainty signal
    NxBus.on('nx:hitl:required', (payload) => {
      if (payload.hitl_type === 'clarification') {
        _showPanel();
        _appendTrustItem({
          type: 'contradiction',
          verified: false,
          confidence: 0.30,
          message: `Paused: ${payload.prompt || 'Awaiting clarification'}`,
        });
      }
    });

    // Task start — show panel and reset session signals
    NxBus.on('nx:agent:start', () => {
      _currentSessionSignals = [];
      _lastCompletionPayload = null;
      _showPanel();
    });

    // Session cleared/created — purge cross-session contamination
    NxBus.on('nx:session:cleared', () => {
      _currentSessionSignals = [];
      _lastCompletionPayload = null;
      _hidePanel();
    });
    NxBus.on('nx:session:created', () => {
      _currentSessionSignals = [];
      _lastCompletionPayload = null;
    });

    // Agent stop — hide after brief delay
    NxBus.on('nx:agent:stop', () => {
      _appendTrustItem({
        type: 'contradiction',
        verified: false,
        confidence: 0,
        message: 'Task cancelled by user',
      });
      setTimeout(_hidePanel, 8000);
    });

    // ─── Multi-Agent Coordination Panel ──────────────────────────────────
    NxBus.on('nx:coordination:update', (payload) => {
      _showPanel();
      _renderCoordinationPanel(payload);
    });

    NxBus.on('nx:delegation:update', (payload) => {
      _showPanel();
      _appendTrustItem({
        type: 'semantic_validation',
        verified: false,
        confidence: payload.node?.confidence ?? 0.5,
        message: `Delegation [${payload.node?.agent_role}]: ${payload.node?.status} — ${payload.node?.purpose || ''}`,
      });
    });

    console.log('[NxTrustUI] Trust Engine UI initialized');
  }

  /* ── Bootstrap ────────────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  // Public API
  window.NxTrustUI = {
    show:   _showPanel,
    hide:   _hidePanel,
    signal: (sig) => { _showPanel(); _appendTrustItem(sig); },

    /**
     * showExplain() — Confidence Breakdown Explainability Overlay
     * Surfaces WHY confidence was low, WHICH signals contributed,
     * and WHAT the operator should do next.
     */
    showExplain() {
      const signals  = _currentSessionSignals;
      const payload  = _lastCompletionPayload;
      if (!signals.length && !payload) {
        return;
      }

      // Remove existing overlay
      const existing = document.getElementById('nxTrustExplainOverlay');
      if (existing) { existing.remove(); return; }

      const overlay = document.createElement('div');
      overlay.id = 'nxTrustExplainOverlay';
      overlay.style.cssText = [
        'position:fixed;bottom:480px;right:16px;width:360px;max-height:420px',
        'background:rgba(10,12,20,0.98);border:1px solid rgba(249,115,22,0.35)',
        'border-radius:10px;z-index:9995;overflow:hidden',
        'box-shadow:0 8px 40px rgba(0,0,0,0.7)',
        'display:flex;flex-direction:column',
        'font-family:\'Inter\',system-ui,sans-serif',
        'animation:nxTrustFadeIn 0.2s ease',
      ].join(';');

      const confidence = payload?.confidence ?? null;
      const pct = confidence != null ? Math.round(confidence * 100) : null;

      // --- Header ---
      const header = document.createElement('div');
      header.style.cssText = 'padding:10px 14px;border-bottom:1px solid rgba(249,115,22,0.2);display:flex;align-items:center;gap:8px;';
      header.innerHTML = `
        <span style="color:#f97316;font-size:13px;">⚡</span>
        <span style="color:#f97316;font-weight:700;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;flex:1;">Confidence Breakdown</span>
        ${pct != null ? `<span style="color:#f97316;font-weight:700;font-size:12px;">${pct}%</span>` : ''}
        <button onclick="document.getElementById('nxTrustExplainOverlay').remove()" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:14px;padding:0 4px;">✕</button>
      `;
      overlay.appendChild(header);

      // --- Body ---
      const body = document.createElement('div');
      body.style.cssText = 'flex:1;overflow-y:auto;padding:10px 14px;scrollbar-width:thin;scrollbar-color:rgba(249,115,22,0.3) transparent;';

      let html = '';

      // Summary
      if (payload) {
        const done  = payload.completed_steps ?? '?';
        const total = payload.total_steps ?? '?';
        html += `
          <div style="margin-bottom:10px;padding:8px;background:rgba(249,115,22,0.06);border-radius:6px;border:1px solid rgba(249,115,22,0.15);">
            <div style="color:#94a3b8;font-size:10px;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em;">Task Summary</div>
            <div style="color:#cbd5e1;font-size:11px;">Steps: ${done}/${total} completed</div>
            ${payload.status ? `<div style="color:#94a3b8;font-size:10px;margin-top:2px;">Status: ${_esc(payload.status)}</div>` : ''}
          </div>
        `;
      }

      // Low-confidence signals
      const lowConf = signals.filter(s => s.confidence != null && s.confidence < 0.70);
      if (lowConf.length) {
        html += `<div style="color:#f59e0b;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">⚠ Low-Confidence Signals (${lowConf.length})</div>`;
        lowConf.slice(-8).forEach(s => {
          const c = Math.round((s.confidence ?? 0) * 100);
          const style = TRUST_TYPE_STYLES[s.type] || { color: '#94a3b8', icon: '●', label: s.type };
          html += `
            <div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:5px;padding:5px 8px;background:rgba(255,255,255,0.02);border-radius:5px;border-left:2px solid ${style.color};">
              <span style="color:${style.color};font-size:11px;flex-shrink:0;">${style.icon}</span>
              <div style="min-width:0;flex:1;">
                <div style="color:${style.color};font-size:9px;font-weight:700;text-transform:uppercase;">${style.label} — ${c}%</div>
                <div style="color:#94a3b8;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${_esc(s.message||'')}">${_esc((s.message||'').slice(0, 100))}</div>
              </div>
            </div>`;
        });
      }

      // Unverified signals
      const unverified = signals.filter(s => s.verified === false && s.type !== 'assumption');
      if (unverified.length) {
        html += `<div style="color:#ef4444;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;margin:8px 0 6px;">✗ Unverified Steps (${unverified.length})</div>`;
        unverified.slice(-5).forEach(s => {
          html += `<div style="color:#94a3b8;font-size:10px;padding:3px 0 3px 10px;border-left:1px solid rgba(239,68,68,0.3);margin-bottom:3px;">${_esc((s.message||'').slice(0,90))}</div>`;
        });
      }

      // Recommendations
      html += `
        <div style="margin-top:10px;padding:8px;background:rgba(99,102,241,0.06);border-radius:6px;border:1px solid rgba(99,102,241,0.15);">
          <div style="color:#a78bfa;font-size:10px;font-weight:700;text-transform:uppercase;margin-bottom:6px;">💡 Recommended Actions</div>
          <div style="color:#94a3b8;font-size:11px;line-height:1.6;">
            ${confidence != null && confidence < 0.40 ? '⚠ Confidence is critically low. Manual review and task restart is strongly recommended.' :
              confidence != null && confidence < 0.70 ? '• Review flagged steps above and manually verify the outputs.<br>• Consider re-running the task with more specific instructions.' :
              '• Task completed with acceptable confidence. Spot-check key outputs.'}
          </div>
        </div>
      `;

      body.innerHTML = html;
      overlay.appendChild(body);
      document.body.appendChild(overlay);
    },
  };
})();
