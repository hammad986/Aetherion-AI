/**
 * nx-z38-cognition.js — Phase Z38 Persistent Runtime Cognition + Adaptive Operational Memory
 * ═══════════════════════════════════════════════════════════════════════════════════════════
 *
 * Z38A — Backend persistence: flush NodeRegistry → /api/z38/memory on key events
 * Z38B — Adaptive learning: hydrate NodeRegistry + Z37 predictor from persisted records
 * Z38C — Evolution tracking: post evolution snapshots + display health trend in inspector
 * Z38D — Persistent forensic replay: hydrate replay context with historical node data
 * Z38E — Memory governance: periodic GC calls, guard against amplification
 * Z38F — Historical presence UX: stable/risky/expensive region classes on DAG nodes
 *
 * Rules:
 *  - SQLite via /api/z38/* only. No localStorage. No IndexedDB.
 *  - All writes are fire-and-forget (non-blocking). No await in hot paths.
 *  - RAF-batched DOM writes. Zero layout thrashing.
 *  - Persistence is additive — never blocks or replaces Z36/Z37 in-memory state.
 */
'use strict';

(function () {
  if (window._z38) return;

  /* ═══════════════════════════════════════════════════════════════════
     STATE
     ═══════════════════════════════════════════════════════════════════ */
  const S = {
    sid:            null,
    flushQueue:     [],      // pending write payloads
    flushTimer:     null,
    hydratedNodes:  new Set(),
    evolutionTimer: null,
    gcTimer:        null,
    initialized:    false,
    lastEvolution:  null,    // last evolution snapshot ts
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z38A — PERSISTENCE: WRITE NODE TO BACKEND
     ═══════════════════════════════════════════════════════════════════ */

  /* Queue a node flush — debounced into batches to avoid write storms */
  function _queueNodeFlush(nodeId) {
    if (!window._z36) return;
    const node = _z36.registry.get(nodeId);
    if (!node) return;

    S.flushQueue.push(nodeId);
    if (!S.flushTimer) {
      S.flushTimer = setTimeout(_flushQueue, 2000);
    }
  }

  function _flushQueue() {
    S.flushTimer = null;
    if (!window._z36 || !S.flushQueue.length) return;

    // Deduplicate
    const ids = [...new Set(S.flushQueue.splice(0, S.flushQueue.length))];

    for (const nodeId of ids) {
      const node = _z36.registry.get(nodeId);
      if (!node) continue;
      _persistNode(node);
    }
  }

  function _persistNode(node) {
    const payload = {
      node_id:        node.id,
      session_id:     S.sid || '',
      state:          node.state || 'pending',
      heat:           node.heat || 0,
      retries:        node.retries || 0,
      errors:         node.errors || 0,
      dur_ms:         node.dur_ms || null,
      parent_id:      window._z37 ? _z37.causalGraph.getParent(node.id) : null,
      branch_type:    window._z37 ? _z37.causalGraph.getBranchType(node.id) : 'main',
      confidence:     node.confidence || null,
      provider:       node.provider || null,
      decision_chain: (node.decisionChain || []).slice(-20),
      failure_reasons:(node.failureReasons || []).slice(-8),
      pressure_trace: (node.pressureTrace || []).slice(-60),
    };

    fetch('/api/z38/memory', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    }).catch(() => {}); // fire-and-forget
  }

  function _persistRecovery(nodeId, recoveryType, success, confBefore, confAfter) {
    fetch('/api/z38/recovery', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        node_id:           nodeId,
        session_id:        S.sid || '',
        recovery_type:     recoveryType,
        success:           success,
        confidence_before: confBefore || null,
        confidence_after:  confAfter  || null,
      }),
    }).catch(() => {});
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z38B — HYDRATION: LOAD PERSISTED RECORDS INTO Z36/Z37
     ═══════════════════════════════════════════════════════════════════ */

  function _hydrateNode(nodeId) {
    if (S.hydratedNodes.has(nodeId)) return;
    S.hydratedNodes.add(nodeId);

    fetch(`/api/z38/memory/${encodeURIComponent(nodeId)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data || !data.found) return;
        _applyHydration(nodeId, data);
      })
      .catch(() => {});
  }

  function _applyHydration(nodeId, data) {
    if (!window._z36) return;

    // Merge historical data into NodeRegistry (add to existing, don't overwrite session state)
    const current = _z36.registry.get(nodeId);
    const historicalHeat = Math.max(0, (data.avg_heat || 0) * 0.5);  // 50% weight historical

    const patch = {};
    if (!current || current.retries === 0) {
      patch.retries = data.total_retries || 0;
    }
    if (!current || current.errors === 0) {
      patch.errors = data.total_errors || 0;
    }
    // Blend heat: existing heat takes priority
    if (current && (current.heat || 0) === 0 && historicalHeat > 0) {
      patch.heat = historicalHeat;
    }

    if (Object.keys(patch).length) {
      _z36.registry.upsert(nodeId, patch);
    }

    // Inject historical recovery into ExecutionMemory (Z37D)
    if (window._z37 && data.recovery_by_type) {
      for (const [type, stats] of Object.entries(data.recovery_by_type)) {
        for (let i = 0; i < stats.successes; i++) {
          _z37.memory.recordRecovery(nodeId, type, true);
        }
        const failures = stats.count - stats.successes;
        for (let i = 0; i < failures; i++) {
          _z37.memory.recordRecovery(nodeId, type, false);
        }
      }
      // Inject unstable count
      if (data.unstable_count >= 1) {
        for (let i = 0; i < Math.min(data.unstable_count, 5); i++) {
          _z37.memory.recordCompletion(nodeId, data.avg_dur_ms, true);
        }
      }
    }

    // Apply historical UX class on DAG element
    _applyHistoricalPresence(nodeId, data);
  }

  /* Bulk hydrate all nodes in a replay set */
  function _hydrateReplaySet(nodeIds) {
    if (!nodeIds.length) return;

    fetch('/api/z38/replay/hydrate', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ node_ids: nodeIds }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.nodes) return;
        for (const [nodeId, record] of Object.entries(data.nodes)) {
          S.hydratedNodes.add(nodeId);
          _applyHydration(nodeId, record);
        }
      })
      .catch(() => {});
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z38C — EVOLUTION TRACKING
     ═══════════════════════════════════════════════════════════════════ */

  function _postEvolutionSnapshot() {
    if (!window._z36 || !window._z37) return;
    const now = Date.now();
    if (S.lastEvolution && now - S.lastEvolution < 15000) return;
    S.lastEvolution = now;

    const nodes     = _z36.registry.all();
    const forecast  = _z37.predictor.getSystemForecast();
    const recoveries = nodes.reduce((s, n) => s + (n.recoveryHistory?.length || 0), 0);

    const payload = {
      session_id:       S.sid || '',
      avg_heat:         forecast.pressure || 0,
      total_retries:    forecast.retries  || 0,
      total_errors:     forecast.errors   || 0,
      total_recoveries: recoveries,
      risk_level:       forecast.risk     || 'LOW',
      node_count:       nodes.length,
    };

    fetch('/api/z38/evolution', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    }).catch(() => {});
  }

  /* Load evolution data for the evolution indicator in the inspector */
  function _loadEvolutionTrend(callback) {
    fetch('/api/z38/evolution?limit=20')
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) callback(data); })
      .catch(() => {});
  }

  function _loadPatterns(callback) {
    fetch('/api/z38/patterns?limit=10')
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) callback(data); })
      .catch(() => {});
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z38F — HISTORICAL PRESENCE UX
     Apply stable/risky/expensive classes to DAG node elements
     ═══════════════════════════════════════════════════════════════════ */

  function _applyHistoricalPresence(nodeId, data) {
    requestAnimationFrame(() => {
      const el = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
      if (!el) return;

      // Remove old classes
      el.classList.remove(
        'z38-hist-stable', 'z38-hist-risky', 'z38-hist-expensive',
        'z38-hist-recovery-heavy', 'z38-hist-escalation'
      );

      if (data.unstable_count >= 3 || data.total_errors >= 3) {
        el.classList.add('z38-hist-risky');
        el.setAttribute('data-z38-insight', data.insight || '');
      } else if (data.avg_dur_ms != null && data.avg_dur_ms > 20000) {
        el.classList.add('z38-hist-expensive');
        el.setAttribute('data-z38-insight', data.insight || '');
      } else if (data.recovery_by_type && Object.keys(data.recovery_by_type).length >= 2) {
        el.classList.add('z38-hist-recovery-heavy');
        el.setAttribute('data-z38-insight', data.insight || '');
      } else if (data.unstable_count === 0 && data.occurrences >= 2) {
        el.classList.add('z38-hist-stable');
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z38 EVOLUTION PANEL — appended to Z37 causal section
     ═══════════════════════════════════════════════════════════════════ */

  function _injectEvolutionPanel() {
    if (document.getElementById('z38EvolutionPanel')) return;

    const panel = document.createElement('div');
    panel.id = 'z38EvolutionPanel';
    panel.className = 'z38-evolution-panel';
    panel.innerHTML = `
      <div class="z38-evo-header">
        <span class="z38-evo-title">RUNTIME EVOLUTION</span>
        <span id="z38EvoStatus" class="z38-evo-status">loading…</span>
      </div>
      <div id="z38EvoTrend" class="z38-evo-trend"></div>
      <div id="z38EvoPatterns" class="z38-evo-patterns"></div>
    `;

    const target = document.getElementById('z37CausalSection')
                 || document.getElementById('z36ForensicSection')
                 || document.getElementById('z34InspectorBody');
    if (target) target.appendChild(panel);
  }

  function _refreshEvolutionPanel() {
    _loadEvolutionTrend(data => _renderEvolutionTrend(data));
    _loadPatterns(data => _renderPatterns(data));
  }

  function _renderEvolutionTrend(data) {
    const trendEl  = document.getElementById('z38EvoTrend');
    const statusEl = document.getElementById('z38EvoStatus');
    if (!trendEl || !data) return;

    const rows = data.evolution || [];
    const totals = data.totals || {};

    if (!rows.length) {
      trendEl.innerHTML = '<span class="z38-evo-empty">no evolution data yet</span>';
      if (statusEl) statusEl.textContent = '—';
      return;
    }

    // Compute simple trend from avg_heat over last N rows
    const recentHeat = rows.slice(-5).map(r => r.avg_heat || 0);
    const trend = _computeTrend(recentHeat);
    if (statusEl) {
      statusEl.textContent = trend.label;
      statusEl.className   = `z38-evo-status z38-trend-${trend.dir}`;
    }

    // Mini sparkline (text-based bars)
    const maxHeat = Math.max(...rows.map(r => r.avg_heat || 0), 0.01);
    const sparkHtml = rows.slice(-10).map(r => {
      const pct  = Math.round(((r.avg_heat || 0) / maxHeat) * 5);
      const bars = '█'.repeat(Math.max(0, pct)) + '░'.repeat(Math.max(0, 5 - pct));
      return `<span class="z38-spark-bar z38-risk-color-${r.risk_level}" title="${r.risk_level}">${bars}</span>`;
    }).join('');

    const summary = [
      totals.global_retries ? `${totals.global_retries} retries` : null,
      totals.global_recoveries ? `${totals.global_recoveries} recoveries` : null,
      totals.session_count ? `${totals.session_count} sessions` : null,
    ].filter(Boolean).join(' · ');

    trendEl.innerHTML = `
      <div class="z38-sparkline">${sparkHtml}</div>
      ${summary ? `<div class="z38-evo-summary">${_esc(summary)}</div>` : ''}
    `;
  }

  function _renderPatterns(data) {
    const el = document.getElementById('z38EvoPatterns');
    if (!el || !data) return;

    const unstable = (data.unstable_nodes || []).slice(0, 3);
    const recovery = (data.recovery_stats || []).slice(0, 3);

    if (!unstable.length && !recovery.length) { el.innerHTML = ''; return; }

    let html = '';
    if (unstable.length) {
      html += `<div class="z38-pattern-section">
        <div class="z38-pattern-title">CHRONIC INSTABILITY</div>
        ${unstable.map(n =>
          `<div class="z38-pattern-row">
             <span class="z38-pattern-node">${_esc(n.node_id)}</span>
             <span class="z38-pattern-val">${n.total_errors}e · ${n.total_retries}r</span>
           </div>`
        ).join('')}
      </div>`;
    }
    if (recovery.length) {
      html += `<div class="z38-pattern-section">
        <div class="z38-pattern-title">RECOVERY STRATEGIES</div>
        ${recovery.map(r =>
          `<div class="z38-pattern-row">
             <span class="z38-pattern-node">${_esc(r.recovery_type)}</span>
             <span class="z38-pattern-val ${r.success_rate >= 0.7 ? 'z38-good' : r.success_rate >= 0.4 ? 'z38-warn' : 'z38-bad'}">${Math.round(r.success_rate * 100)}%</span>
           </div>`
        ).join('')}
      </div>`;
    }

    el.innerHTML = html;
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z38E — MEMORY GOVERNANCE
     ═══════════════════════════════════════════════════════════════════ */

  function _scheduleGC() {
    if (S.gcTimer) clearInterval(S.gcTimer);
    // GC every 10 minutes
    S.gcTimer = setInterval(() => {
      fetch('/api/z38/gc', { method: 'POST' }).catch(() => {});
    }, 10 * 60 * 1000);
  }

  /* Recursion / amplification guard on Z37 pressure propagation */
  function _guardPressureAmplification() {
    if (!window._z36) return;
    const nodes = _z36.registry.all();
    for (const node of nodes) {
      if ((node.heat || 0) > 1.0) {
        _z36.registry.upsert(node.id, { heat: 1.0 });
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     NXBUS WIRING
     ═══════════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    /* Session lifecycle */
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z38' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED || 'nx:session:created', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z38' });

    NxBus.on('session.done',  (e) => _onSessionEnd('success', e), { owner: 'z38' });
    NxBus.on('session.error', (e) => _onSessionEnd('error',   e), { owner: 'z38' });

    /* DAG node events → queue persists */
    NxBus.on('dag.node.selected', (e) => {
      if (!e?.node?.id) return;
      const nodeId = e.node.id;
      // Hydrate from persistence if not yet seen
      _hydrateNode(nodeId);
      // Queue a flush for later
      _queueNodeFlush(nodeId);
    }, { owner: 'z38' });

    NxBus.on('dag.node.done', (e) => {
      if (!e?.id) return;
      _queueNodeFlush(e.id);
    }, { owner: 'z38' });

    NxBus.on('dag.node.error', (e) => {
      if (!e?.id) return;
      _queueNodeFlush(e.id);
    }, { owner: 'z38' });

    /* Z32 replan → persist recovery */
    NxBus.on('z32.replan.applied', (e) => {
      const nodeId = e?.nodeId;
      if (nodeId) {
        const node = window._z36 ? _z36.registry.get(nodeId) : null;
        _persistRecovery(
          nodeId, e?.action || 'replan', true,
          node?.confidence, null
        );
        _queueNodeFlush(nodeId);
      }
    }, { owner: 'z38' });

    /* Z36 node focus → hydrate on demand */
    NxBus.on('z36.node.focus', (e) => {
      if (e?.id) _hydrateNode(e.id);
    }, { owner: 'z38' });

    /* Z36 registry updates → debounced persist */
    NxBus.on('z36.node.updated', (e) => {
      if (e?.id) _queueNodeFlush(e.id);
    }, { owner: 'z38' });

    /* Z34 replay started → bulk hydrate */
    NxBus.on('dag.replay.started', () => {
      if (window._z34) {
        const events = _z34.getTimelineEvents();
        const nodeIds = [...new Set(events.map(ev => ev.nodeId).filter(Boolean))];
        if (nodeIds.length) _hydrateReplaySet(nodeIds);
      }
    }, { owner: 'z38' });

    /* Evolution snapshots every 20s during active session */
    NxBus.on('agent.log_row', () => {
      _postEvolutionSnapshot();
      _guardPressureAmplification();
    }, { owner: 'z38' });

    /* Inspector refresh on node focus */
    NxBus.on('z36.node.focus', () => _refreshEvolutionPanel(), { owner: 'z38-evo' });
  }

  /* ═══════════════════════════════════════════════════════════════════
     SESSION LIFECYCLE
     ═══════════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid) {
    S.sid          = sid;
    S.hydratedNodes.clear();
    S.flushQueue   = [];
    S.lastEvolution = null;
    if (S.flushTimer) { clearTimeout(S.flushTimer); S.flushTimer = null; }

    // Post initial evolution snapshot
    setTimeout(_postEvolutionSnapshot, 3000);
  }

  function _onSessionEnd(status) {
    // Flush all queued nodes immediately on session end
    if (S.flushTimer) { clearTimeout(S.flushTimer); S.flushTimer = null; }
    _flushQueue();
    _postEvolutionSnapshot();
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITY
     ═══════════════════════════════════════════════════════════════════ */

  function _computeTrend(values) {
    if (values.length < 2) return { dir: 'stable', label: '→ stable' };
    const avg = values.slice(0, -1).reduce((s, v) => s + v, 0) / (values.length - 1);
    const last = values[values.length - 1];
    if (last > avg + 0.08) return { dir: 'rising',  label: '↑ rising' };
    if (last < avg - 0.08) return { dir: 'falling', label: '↓ improving' };
    return { dir: 'stable', label: '→ stable' };
  }

  function _esc(s) {
    return String(s ?? '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
  }

  /* ═══════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════ */

  window._z38 = {
    hydrateNode:      _hydrateNode,
    hydrateReplaySet: _hydrateReplaySet,
    flushNode:        _queueNodeFlush,
    refreshEvolution: _refreshEvolutionPanel,
    getState:         () => ({ sid: S.sid, hydratedCount: S.hydratedNodes.size }),
  };

  /* ═══════════════════════════════════════════════════════════════════
     INIT
     ═══════════════════════════════════════════════════════════════════ */

  function _init() {
    _wireNxBus();

    setTimeout(() => {
      _injectEvolutionPanel();
      _refreshEvolutionPanel();
      _scheduleGC();
    }, 1200);

    S.initialized = true;
    console.debug('[Phase Z38] Persistent Runtime Cognition + Adaptive Operational Memory active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 1200));
  } else {
    setTimeout(_init, 1200);
  }
})();
