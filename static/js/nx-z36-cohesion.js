/**
 * nx-z36-cohesion.js — Phase Z36 Runtime Cohesion + Execution Intelligence
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Z36A — DAG Node Identity + Execution Cohesion
 *         (stable node id, lineage, cross-surface sync, execution pulse)
 * Z36B — Timeline Intelligence Evolution
 *         (semantic grouping, pressure indicators, replay reconstruction)
 * Z36C — Forensic Execution Intelligence
 *         (decision chain, failure pressure, recovery intelligence)
 * Z36D — Execution Immersion + Spatial Density
 *         (spatial depth layers, density governance, focus steering)
 * Z36E — Continuity + Long-Session Presence
 *         (runtime threads, pressure memory, drift awareness)
 *
 * Rules:
 *  - NO new agents. NO new orchestration.
 *  - All logic operational — zero decorative.
 *  - RAF-batched DOM writes only.
 *  - Every surface syncs via the shared NodeRegistry.
 */
'use strict';

(function () {
  if (window._z36) return;

  /* ═══════════════════════════════════════════════════════════════════
     Z36A — NODE REGISTRY
     Single source of truth for all node identity, state, and lineage.
     Every Z30/Z34/Z35 system references this.
     ═══════════════════════════════════════════════════════════════════ */

  const NodeRegistry = (function () {
    const _nodes   = {};  // nodeId → NodeRecord
    const _lineage = {};  // nodeId → parentId
    const _gens    = {};  // nodeId → execution generation (retry count)
    const _listeners = [];

    function upsert(nodeId, patch) {
      if (!_nodes[nodeId]) {
        _nodes[nodeId] = {
          id:         nodeId,
          lineageId:  nodeId,               // stable across retries
          replayId:   `${nodeId}:0`,        // changes with generation
          parentId:   null,
          generation: 0,
          state:      'pending',
          heat:       0,
          confidence: null,
          provider:   null,
          tokens:     0,
          retries:    0,
          errors:     0,
          ts_start:   null,
          ts_end:     null,
          dur_ms:     null,
          lastLog:    null,
          decisionChain: [],                // Z36C: why this node ran
          failureReasons: [],               // Z36C: why it failed
          recoveryHistory: [],              // Z36C: past recovery outcomes
          pressureTrace:  [],               // Z36E: pressure history
        };
      }

      const n = _nodes[nodeId];
      if (patch.state && patch.state !== n.state) {
        n.decisionChain.push({ from: n.state, to: patch.state, ts: Date.now() });
        if (n.decisionChain.length > 20) n.decisionChain.shift();
      }
      Object.assign(n, patch);

      // Generation tracking
      if (patch._retry) {
        n.generation++;
        n.retries++;
        n.replayId = `${nodeId}:${n.generation}`;
        _gens[nodeId] = n.generation;
      }
      if (patch._failure) {
        n.errors++;
        n.failureReasons.push({ reason: patch._failure, ts: Date.now(), gen: n.generation });
        if (n.failureReasons.length > 8) n.failureReasons.shift();
      }
      if (patch._recovery) {
        n.recoveryHistory.push({ action: patch._recovery, success: patch._recoverySuccess !== false, ts: Date.now() });
        if (n.recoveryHistory.length > 8) n.recoveryHistory.shift();
      }
      if (patch.pressure != null) {
        n.pressureTrace.push({ p: patch.pressure, ts: Date.now() });
        if (n.pressureTrace.length > 30) n.pressureTrace.shift();
      }

      _notify(nodeId, n);
      return n;
    }

    function get(nodeId)  { return _nodes[nodeId] || null; }
    function all()        { return Object.values(_nodes); }
    function setParent(childId, parentId) { _lineage[childId] = parentId; if (_nodes[childId]) _nodes[childId].parentId = parentId; }
    function getLineage(nodeId) {
      const chain = [];
      let cur = nodeId;
      while (cur && chain.length < 12) { chain.unshift(cur); cur = _lineage[cur]; }
      return chain;
    }
    function clear() {
      Object.keys(_nodes).forEach(k => delete _nodes[k]);
      Object.keys(_lineage).forEach(k => delete _lineage[k]);
      Object.keys(_gens).forEach(k => delete _gens[k]);
    }

    function onChange(fn) { _listeners.push(fn); }
    function _notify(id, node) {
      _listeners.forEach(fn => { try { fn(id, node); } catch (_) {} });
      if (window.NxBus) NxBus.emit('z36.node.updated', { id, node });
    }

    return { upsert, get, all, setParent, getLineage, clear, onChange };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     PRESSURE MEMORY (Z36E)
     Tracks regional instability hotspots across retries/replans
     ═══════════════════════════════════════════════════════════════════ */

  const PressureMemory = (function () {
    const _hotspots = {};  // nodeId → {count, lastTs, peakPressure}
    const _driftLog = [];  // [{ts, pressure, phase}]

    function record(nodeId, pressure, type) {
      if (!_hotspots[nodeId]) _hotspots[nodeId] = { count: 0, lastTs: 0, peakPressure: 0, type };
      _hotspots[nodeId].count++;
      _hotspots[nodeId].lastTs = Date.now();
      _hotspots[nodeId].peakPressure = Math.max(_hotspots[nodeId].peakPressure, pressure);
    }

    function recordDrift(pressure, phase) {
      _driftLog.push({ ts: Date.now(), pressure, phase });
      if (_driftLog.length > 60) _driftLog.shift();
    }

    function getHotspots() {
      return Object.entries(_hotspots)
        .map(([id, data]) => ({ id, ...data }))
        .sort((a, b) => b.peakPressure - a.peakPressure);
    }

    function getDriftTrend() {
      if (_driftLog.length < 6) return 'stable';
      const recent = _driftLog.slice(-6).map(d => d.pressure);
      const avg5 = recent.slice(0, 5).reduce((s, v) => s + v, 0) / 5;
      const last  = recent[5];
      if (last - avg5 > 0.15) return 'rising';
      if (avg5 - last > 0.15) return 'falling';
      return 'stable';
    }

    function getPressureScore(nodeId) { return _hotspots[nodeId]?.peakPressure || 0; }
    function getLog() { return _driftLog.slice(); }
    function clear() {
      Object.keys(_hotspots).forEach(k => delete _hotspots[k]);
      _driftLog.length = 0;
    }

    return { record, recordDrift, getHotspots, getDriftTrend, getPressureScore, getLog, clear };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     STATE
     ═══════════════════════════════════════════════════════════════════ */
  const S = {
    sid:            null,
    focusedNodeId:  null,
    phase:          'idle',
    tlGroups:       [],    // Z36B: grouped timeline events
    driftTrend:     'stable',
    rafPending:     false,
    pulseNodeId:    null,  // current execution pulse target
    initialized:    false,
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z36A — DATA-NODE-ID EMITTER
     Ensures every Z30 DAG node has a stable data-node-id attribute.
     Runs on MutationObserver watching the DAG surface.
     ═══════════════════════════════════════════════════════════════════ */

  let _nodeObserver = null;

  function _startNodeObserver() {
    if (_nodeObserver) return;
    const surface = document.getElementById('z30DagSurface');
    if (!surface) return;

    _nodeObserver = new MutationObserver(_onDomMutation);
    _nodeObserver.observe(surface, { childList: true, subtree: true, attributes: false });

    // Initial pass on existing nodes
    _auditNodeIds(surface);
  }

  function _onDomMutation(mutations) {
    for (const m of mutations) {
      if (m.addedNodes.length) {
        for (const node of m.addedNodes) {
          if (node.nodeType === 1) _auditNodeIds(node);
        }
      }
    }
  }

  /* Ensure every renderable node element has data-node-id */
  function _auditNodeIds(root) {
    // Z30 renders nodes with class names containing the node id
    // Common patterns: .dag-node, .nx-dag-node, [class*="node"], <g data-id="...">
    const candidates = root.querySelectorAll
      ? root.querySelectorAll('.dag-node, .nx-dag-node, [data-id], [data-nodeid], .z30-node, g[id]')
      : [];

    for (const el of candidates) {
      if (el.hasAttribute('data-node-id')) continue;
      const id = el.getAttribute('data-id')
               || el.getAttribute('data-nodeid')
               || el.getAttribute('id')
               || null;
      if (id) {
        el.setAttribute('data-node-id', id);
        // Register in NodeRegistry if not yet known
        if (!NodeRegistry.get(id)) NodeRegistry.upsert(id, { ts_start: Date.now() });
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36A — CROSS-SURFACE HOVER SYNCHRONIZATION
     Hover on any surface triggers highlight on all others
     ═══════════════════════════════════════════════════════════════════ */

  function _wireHoverSync() {
    // DAG surface hover
    const dagSurface = document.getElementById('z30DagSurface');
    if (dagSurface) {
      dagSurface.addEventListener('mouseover', _onDagHover, { passive: true });
      dagSurface.addEventListener('mouseleave', _clearHoverFocus, { passive: true });
    }

    // Timeline dock hover
    const tlDock = document.getElementById('z33TimelineDock');
    if (tlDock) {
      tlDock.addEventListener('mouseover', _onTimelineHover, { passive: true });
      tlDock.addEventListener('mouseleave', _clearHoverFocus, { passive: true });
    }
  }

  function _onDagHover(e) {
    const el = e.target.closest('[data-node-id]');
    if (!el) return;
    const nodeId = el.getAttribute('data-node-id');
    if (nodeId !== S.focusedNodeId) {
      S.focusedNodeId = nodeId;
      _propagateFocus(nodeId, 'dag');
    }
  }

  function _onTimelineHover(e) {
    const row = e.target.closest('[data-z36-node-id]');
    if (!row) return;
    const nodeId = row.getAttribute('data-z36-node-id');
    if (nodeId !== S.focusedNodeId) {
      S.focusedNodeId = nodeId;
      _propagateFocus(nodeId, 'timeline');
    }
  }

  function _clearHoverFocus() {
    S.focusedNodeId = null;
    _clearAllFocusHighlights();
    if (window.NxBus) NxBus.emit('z36.node.focus', { id: null });
  }

  function _propagateFocus(nodeId, source) {
    _clearAllFocusHighlights();

    // Highlight DAG node
    if (source !== 'dag') {
      const dagEl = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
      if (dagEl) dagEl.classList.add('z36-focus-ring');
    }

    // Highlight timeline row
    if (source !== 'timeline') {
      document.querySelectorAll(`[data-z36-node-id="${CSS.escape(nodeId)}"]`).forEach(el => {
        el.classList.add('z36-timeline-focus');
      });
    }

    if (window.NxBus) NxBus.emit('z36.node.focus', { id: nodeId, source });
  }

  function _clearAllFocusHighlights() {
    document.querySelectorAll('.z36-focus-ring').forEach(el => el.classList.remove('z36-focus-ring'));
    document.querySelectorAll('.z36-timeline-focus').forEach(el => el.classList.remove('z36-timeline-focus'));
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36A — EXECUTION PULSE
     Active execution node emits a 1.4s subtle pulse across linked surfaces
     ═══════════════════════════════════════════════════════════════════ */

  let _pulseTimer = null;

  function _emitExecutionPulse(nodeId) {
    if (_pulseTimer) { clearTimeout(_pulseTimer); _pulseTimer = null; }

    // Remove old pulse
    document.querySelectorAll('.z36-pulse-active').forEach(el => el.classList.remove('z36-pulse-active'));

    if (!nodeId) return;
    S.pulseNodeId = nodeId;

    const dagEl = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
    if (dagEl) dagEl.classList.add('z36-pulse-active');

    document.querySelectorAll(`[data-z36-node-id="${CSS.escape(nodeId)}"]`).forEach(el => {
      el.classList.add('z36-pulse-active');
    });

    _pulseTimer = setTimeout(() => {
      document.querySelectorAll('.z36-pulse-active').forEach(el => el.classList.remove('z36-pulse-active'));
      S.pulseNodeId = null;
    }, 1400);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36B — TIMELINE INTELLIGENCE
     Annotate timeline rows with node id, pressure class, and semantic group
     ═══════════════════════════════════════════════════════════════════ */

  function _enrichTimelineRows() {
    const dock = document.getElementById('z33TimelineDock');
    if (!dock) return;

    const rows = dock.querySelectorAll('.z33-tl-event, .z33-tl-group');
    let groupIdx   = null;
    let groupColor = null;

    rows.forEach((row, idx) => {
      if (row._z36enriched) return;
      row._z36enriched = true;

      // Extract node id from existing row data or text
      const nodeId = _extractNodeIdFromRow(row, idx);
      if (nodeId) row.setAttribute('data-z36-node-id', nodeId);

      // Pressure class from NodeRegistry
      if (nodeId) {
        const node   = NodeRegistry.get(nodeId);
        const hotspot = PressureMemory.getPressureScore(nodeId);
        const pressure = Math.max(node?.heat || 0, hotspot);
        if (pressure >= 0.75) row.classList.add('z36-tl-pressure-critical');
        else if (pressure >= 0.5) row.classList.add('z36-tl-pressure-high');
        else if (pressure >= 0.25) row.classList.add('z36-tl-pressure-med');

        // Recovery/retry indicators
        if (node?.retries > 0) row.classList.add('z36-tl-has-retries');
        if (node?.errors > 0)  row.classList.add('z36-tl-has-errors');

        // Confidence decay indicator
        if (node?.confidence != null && node.confidence < 0.45) {
          row.classList.add('z36-tl-conf-low');
        }
      }

      // Semantic group colouring — group consecutive rows for the same nodeId
      const evType = _extractEventType(row);
      if (evType === 'replan' || evType === 'recovery') row.classList.add('z36-tl-recovery-chain');
      if (evType === 'retry')    row.classList.add('z36-tl-retry-chain');
      if (evType === 'node-done') row.classList.add('z36-tl-completed');
    });
  }

  function _extractNodeIdFromRow(row, idx) {
    // Try data attributes set by Z34
    const existing = row.getAttribute('data-z36-node-id') || row.getAttribute('data-node-id');
    if (existing) return existing;

    // Try text content pattern matching
    const text = row.textContent || '';
    const m = /\[(plan|code|debug|tool|done|review|test)\]/i.exec(text);
    if (m) return m[1].toLowerCase();

    // Try Z34 timeline events array
    if (window._z34) {
      const events = _z34.getTimelineEvents();
      if (events[idx]) return events[idx].nodeId || null;
    }
    return null;
  }

  function _extractEventType(row) {
    const text = row.textContent || '';
    if (/retry|retrying/i.test(text))  return 'retry';
    if (/replan|replann/i.test(text))  return 'replan';
    if (/recover/i.test(text))         return 'recovery';
    if (/done|success|✓|✅/i.test(text)) return 'node-done';
    return 'general';
  }

  /* Replay reconstruction: rebuild DAG + inspector on scroll-hover */
  function _wireTimelineReplayReconstruction() {
    const dock = document.getElementById('z33TimelineDock');
    if (!dock || dock._z36replayWired) return;
    dock._z36replayWired = true;

    dock.addEventListener('mouseover', (e) => {
      const row = e.target.closest('[data-z36-node-id]');
      if (!row) return;
      const nodeId = row.getAttribute('data-z36-node-id');
      if (!nodeId) return;

      // Soft-reconstruct inspector for this node without seeking cursor
      _softOpenInspector(nodeId);

      // Pulse the DAG node
      _emitExecutionPulse(nodeId);
    }, { passive: true });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36C — FORENSIC EXECUTION INTELLIGENCE
     Decision chain + failure pressure analysis in inspector
     ═══════════════════════════════════════════════════════════════════ */

  function _injectForensicPanel() {
    if (document.getElementById('z36ForensicSection')) return;

    const inspBody = document.getElementById('z34InspectorBody');
    if (!inspBody) return;

    const sec = document.createElement('div');
    sec.id = 'z36ForensicSection';
    sec.className = 'z36-forensic-section';
    inspBody.appendChild(sec);
  }

  function _updateForensicSection(nodeId) {
    const sec = document.getElementById('z36ForensicSection');
    if (!sec) return;

    const node = NodeRegistry.get(nodeId);
    if (!node) { sec.innerHTML = ''; return; }

    const hotspots = PressureMemory.getHotspots();
    const isCriticalHotspot = hotspots.slice(0, 3).some(h => h.id === nodeId);

    /* Decision chain */
    const chainHtml = node.decisionChain.length
      ? `<div class="z36-forensic-block">
           <div class="z36-forensic-title">Decision Chain</div>
           <div class="z36-chain">
             ${node.decisionChain.slice(-6).map(c =>
               `<div class="z36-chain-step">
                  <span class="z36-chain-from z36-state-${c.from}">${_esc(c.from)}</span>
                  <span class="z36-chain-arrow">→</span>
                  <span class="z36-chain-to z36-state-${c.to}">${_esc(c.to)}</span>
                  <span class="z36-chain-ts">${_fmtTs(c.ts)}</span>
                </div>`
             ).join('')}
           </div>
         </div>`
      : '';

    /* Failure pressure analysis */
    let failureHtml = '';
    if (node.failureReasons.length) {
      const cascadeRisk = node.errors >= 3 ? 'high' : node.errors >= 1 ? 'medium' : 'none';
      const retryAmp    = node.retries >= 4 ? 'amplified' : node.retries >= 1 ? 'active' : 'none';
      failureHtml = `<div class="z36-forensic-block">
        <div class="z36-forensic-title">Failure Pressure</div>
        <div class="z36-pressure-analysis">
          <div class="z36-pa-row">
            <span class="z36-pa-label">Cascade Risk</span>
            <span class="z36-pa-val z36-risk-${cascadeRisk}">${cascadeRisk}</span>
          </div>
          <div class="z36-pa-row">
            <span class="z36-pa-label">Retry Amplification</span>
            <span class="z36-pa-val z36-risk-${retryAmp === 'amplified' ? 'high' : retryAmp === 'active' ? 'medium' : 'none'}">${retryAmp}</span>
          </div>
          <div class="z36-pa-row">
            <span class="z36-pa-label">Hotspot</span>
            <span class="z36-pa-val ${isCriticalHotspot ? 'z36-risk-high' : ''}">${isCriticalHotspot ? 'yes' : 'no'}</span>
          </div>
        </div>
        ${node.failureReasons.slice(-3).map(f =>
          `<div class="z36-failure-row">${_esc(f.reason.slice(0, 80))}<span class="z36-chain-ts">${_fmtTs(f.ts)}</span></div>`
        ).join('')}
      </div>`;
    }

    /* Recovery intelligence */
    let recoveryHtml = '';
    if (node.recoveryHistory.length) {
      const successes  = node.recoveryHistory.filter(r => r.success).length;
      const total      = node.recoveryHistory.length;
      const successRate = Math.round((successes / total) * 100);
      const stabilizationConf = successRate >= 75 ? 'high' : successRate >= 40 ? 'moderate' : 'low';
      recoveryHtml = `<div class="z36-forensic-block">
        <div class="z36-forensic-title">Recovery Intelligence</div>
        <div class="z36-pressure-analysis">
          <div class="z36-pa-row">
            <span class="z36-pa-label">Recovery Rate</span>
            <span class="z36-pa-val">${successRate}%</span>
          </div>
          <div class="z36-pa-row">
            <span class="z36-pa-label">Stabilization Confidence</span>
            <span class="z36-pa-val z36-conf-${stabilizationConf}">${stabilizationConf}</span>
          </div>
        </div>
        ${node.recoveryHistory.slice(-3).map(r =>
          `<div class="z36-recovery-row ${r.success ? 'success' : 'fail'}">
             <span class="z36-recovery-dot"></span>
             ${_esc(r.action.slice(0, 60))}
           </div>`
        ).join('')}
      </div>`;
    }

    /* Drift awareness for this node */
    const pressureTrace = node.pressureTrace;
    let driftHtml = '';
    if (pressureTrace.length >= 4) {
      const recent = pressureTrace.slice(-4).map(p => p.p);
      const rising = recent[recent.length - 1] > recent[0] + 0.1;
      const falling = recent[0] > recent[recent.length - 1] + 0.1;
      const trend = rising ? '↑ rising' : falling ? '↓ falling' : '→ stable';
      const trendCls = rising ? 'warning' : falling ? 'good' : '';
      driftHtml = `<div class="z36-forensic-block">
        <div class="z36-forensic-title">Pressure Trend</div>
        <span class="z36-drift-trend ${trendCls}">${trend}</span>
      </div>`;
    }

    sec.innerHTML = chainHtml + failureHtml + recoveryHtml + driftHtml;
  }

  /* Soft-open inspector for a node during timeline hover (no cursor seek) */
  function _softOpenInspector(nodeId) {
    if (window._z34 && _z34.openInspector) {
      _z34.openInspector(nodeId, null);
    }
    _updateForensicSection(nodeId);
  }

  /* Full open with forensic section updated */
  function _openFullInspector(nodeId) {
    _softOpenInspector(nodeId);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36D — SPATIAL DEPTH LAYERS
     Assigns data-z36-depth to surfaces based on current state
     ═══════════════════════════════════════════════════════════════════ */

  function _updateSpatialDepth() {
    const phase = S.phase;
    const dag   = document.getElementById('z30DagSurface');
    const tl    = document.getElementById('z33TimelineDock');
    const insp  = document.getElementById('z34InspectorPanel');
    const mem   = document.getElementById('z31ForensicPanel');

    const states = {
      dag:  phase === 'executing' || phase === 'planning'   ? 'active' :
            phase === 'replay'                              ? 'replayed' :
            phase === 'recovering'                          ? 'unstable' :
            phase === 'idle'                                ? 'dormant' : 'active',
      tl:   phase === 'replay'                              ? 'active' :
            phase === 'idle'                                ? 'dormant' : 'background',
      insp: phase === 'recovering' || phase === 'escalating' ? 'active' :
            phase === 'forensic'                            ? 'active' : 'background',
      mem:  phase === 'recovering'                          ? 'unstable' :
            phase === 'idle'                                ? 'dormant' : 'background',
    };

    if (dag)  dag.setAttribute('data-z36-depth',  states.dag);
    if (tl)   tl.setAttribute('data-z36-depth',   states.tl);
    if (insp) insp.setAttribute('data-z36-depth', states.insp);
    if (mem)  mem.setAttribute('data-z36-depth',  states.mem);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36D — DENSITY GOVERNANCE
     Adjusts CSS density var based on complexity signals
     ═══════════════════════════════════════════════════════════════════ */

  function _updateDensityGovernance() {
    const nodeCount = NodeRegistry.all().length;
    const driftTrend = PressureMemory.getDriftTrend();

    // Density scale: 0=compact, 1=normal, 2=spacious
    let density;
    if      (nodeCount >= 12 || driftTrend === 'rising')  density = 'compact';
    else if (nodeCount >= 6)                              density = 'normal';
    else                                                  density = 'spacious';

    document.documentElement.setAttribute('data-z36-density', density);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36D — FOCUS STEERING
     Guide operator attention toward unstable nodes
     ═══════════════════════════════════════════════════════════════════ */

  function _steerAttention() {
    // Clear old steered classes
    document.querySelectorAll('.z36-steered').forEach(el => el.classList.remove('z36-steered'));

    const hotspots = PressureMemory.getHotspots();
    const topHot   = hotspots.slice(0, 2);

    for (const hot of topHot) {
      if (hot.peakPressure < 0.5) continue;
      const dagEl = document.querySelector(`[data-node-id="${CSS.escape(hot.id)}"]`);
      if (dagEl) dagEl.classList.add('z36-steered');
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z36E — CONTINUITY THREAD RENDERING
     Visual execution branches in the DAG surface header
     ═══════════════════════════════════════════════════════════════════ */

  function _injectContinuityThreads() {
    if (document.getElementById('z36ThreadBar')) return;

    const bar = document.createElement('div');
    bar.id = 'z36ThreadBar';
    bar.className = 'z36-thread-bar';
    bar.innerHTML = `
      <div class="z36-thread-label">THREADS</div>
      <div id="z36ThreadList" class="z36-thread-list"></div>
      <div class="z36-drift-indicator" id="z36DriftIndicator">
        <span class="z36-drift-label">DRIFT</span>
        <span id="z36DriftText" class="z36-drift-val z36-drift-stable">stable</span>
      </div>
    `;

    const dagHdr = document.querySelector('.z30-dag-panel-hdr');
    if (dagHdr) {
      dagHdr.appendChild(bar);
    }
  }

  function _updateContinuityThreads() {
    const list = document.getElementById('z36ThreadList');
    if (!list) return;

    const nodes = NodeRegistry.all();
    if (!nodes.length) { list.innerHTML = ''; return; }

    // Group by state into execution threads
    const threads = {
      done:      nodes.filter(n => n.state === 'done'),
      running:   nodes.filter(n => n.state === 'running'),
      error:     nodes.filter(n => n.state === 'error'),
      pending:   nodes.filter(n => n.state === 'pending'),
    };

    const parts = [];
    if (threads.running.length)  parts.push(`<span class="z36-thr-dot running" title="${threads.running.length} running">${threads.running.length}</span>`);
    if (threads.error.length)    parts.push(`<span class="z36-thr-dot error"   title="${threads.error.length} failed">${threads.error.length}</span>`);
    if (threads.done.length)     parts.push(`<span class="z36-thr-dot done"    title="${threads.done.length} done">${threads.done.length}</span>`);
    if (threads.pending.length)  parts.push(`<span class="z36-thr-dot pending" title="${threads.pending.length} pending">${threads.pending.length}</span>`);

    list.innerHTML = parts.join('');

    // Drift
    const driftTrend = PressureMemory.getDriftTrend();
    S.driftTrend = driftTrend;
    const driftEl = document.getElementById('z36DriftText');
    if (driftEl) {
      driftEl.textContent = driftTrend;
      driftEl.className = `z36-drift-val z36-drift-${driftTrend}`;
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     SCHEDULED RAF UPDATE
     ═══════════════════════════════════════════════════════════════════ */

  function _scheduleUpdate() {
    if (S.rafPending) return;
    S.rafPending = true;
    requestAnimationFrame(_applyUpdate);
  }

  function _applyUpdate() {
    S.rafPending = false;
    _updateSpatialDepth();
    _updateDensityGovernance();
    _steerAttention();
    _updateContinuityThreads();
    _enrichTimelineRows();
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
    }, { owner: 'z36' });

    NxBus.on('session.done',  () => _onSessionEnd('success'), { owner: 'z36' });
    NxBus.on('session.error', () => _onSessionEnd('error'),   { owner: 'z36' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED  || 'nx:session:created',  (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z36' });

    /* DAG node events → NodeRegistry */
    NxBus.on('dag.node.selected', (e) => {
      if (e?.node?.id) {
        const node = e.node;
        NodeRegistry.upsert(node.id, {
          state:      node.state || 'running',
          confidence: node.semantic_confidence ?? null,
          retries:    node.retries || 0,
          provider:   node.provider || null,
        });
        _openFullInspector(node.id);
        _emitExecutionPulse(node.id);
        _scheduleUpdate();
      }
    }, { owner: 'z36' });

    NxBus.on('dag.node.done', (e) => {
      if (e?.id) {
        NodeRegistry.upsert(e.id, { state: 'done', ts_end: Date.now() });
        PressureMemory.record(e.id, 0, 'done');
        _scheduleUpdate();
      }
    }, { owner: 'z36' });

    NxBus.on('dag.node.error', (e) => {
      if (e?.id) {
        NodeRegistry.upsert(e.id, {
          state:    'error',
          _failure: e.error || 'Execution error',
        });
        PressureMemory.record(e.id, 0.8, 'error');
        S.phase = 'recovering';
        _emitExecutionPulse(e.id);
        _scheduleUpdate();
      }
    }, { owner: 'z36' });

    /* Z36 node focus events (cross-surface) */
    NxBus.on('z36.node.focus', (e) => {
      if (e?.id) {
        _updateForensicSection(e.id);
      }
    }, { owner: 'z36-inspector' });

    /* Log rows → node extraction + pulse */
    NxBus.on('agent.log_row', (e) => {
      if (!e?.text) return;
      const phase = _detectPhase(e.text);
      if (phase) {
        S.phase = phase;
        NodeRegistry.upsert(phase, { state: 'running', lastLog: e.text.slice(0, 120) });
        _emitExecutionPulse(phase);
      }

      if (/retry|retrying/i.test(e.text) && phase) {
        NodeRegistry.upsert(phase, { _retry: true });
        PressureMemory.record(phase, 0.6, 'retry');
      }

      if (/error|failed|traceback/i.test(e.text) && phase) {
        NodeRegistry.upsert(phase, { _failure: e.text.slice(0, 80) });
        PressureMemory.record(phase, 0.8, 'error');
      }

      _scheduleUpdate();
    }, { owner: 'z36' });

    /* Z32 semantic confidence */
    NxBus.on('z32.confidence.update', (e) => {
      const nodeId = e?.nodeId;
      if (nodeId && e?.confidence != null) {
        NodeRegistry.upsert(nodeId, { confidence: e.confidence, pressure: 1 - e.confidence });
        PressureMemory.record(nodeId, 1 - e.confidence, 'confidence');
      }
      _scheduleUpdate();
    }, { owner: 'z36' });

    /* Z32 replan applied → record recovery */
    NxBus.on('z32.replan.applied', (e) => {
      const nodeId = e?.nodeId;
      if (nodeId) {
        NodeRegistry.upsert(nodeId, {
          _recovery: e?.action || 'Replan applied',
          _recoverySuccess: true,
        });
        PressureMemory.record(nodeId, 0.3, 'recovery');
      }
      _scheduleUpdate();
    }, { owner: 'z36' });

    /* Z35 phase changes */
    NxBus.on('z36.node.updated', (e) => {
      // Z36 listens to its own updates for forensic section refresh
      if (e?.id && e.id === S.focusedNodeId) {
        _updateForensicSection(e.id);
      }
    }, { owner: 'z36-self' });

    /* Pressure drift tracking */
    setInterval(() => {
      if (window._z35) {
        const state = _z35.getState();
        PressureMemory.recordDrift(state.pressure || 0, state.phase || 'idle');
        const trend = PressureMemory.getDriftTrend();
        if (trend !== S.driftTrend) {
          S.driftTrend = trend;
          _scheduleUpdate();
        }
      }
    }, 8000);

    /* Timeline rendered → enrich + wire */
    NxBus.on('z33.timeline.rendered', () => {
      setTimeout(() => {
        _enrichTimelineRows();
        _wireTimelineReplayReconstruction();
      }, 100);
    }, { owner: 'z36' });

    /* Replay mode */
    NxBus.on('dag.replay.started', () => { S.phase = 'replay'; _scheduleUpdate(); }, { owner: 'z36' });
    NxBus.on('dag.replay.stopped', () => { S.phase = 'idle';   _scheduleUpdate(); }, { owner: 'z36' });
  }

  /* ═══════════════════════════════════════════════════════════════════
     SESSION LIFECYCLE
     ═══════════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid) {
    S.sid   = sid;
    S.phase = 'planning';
    NodeRegistry.clear();
    PressureMemory.clear();
    S.focusedNodeId = null;
    S.driftTrend = 'stable';
    _scheduleUpdate();
  }

  function _onSessionEnd(status) {
    S.phase = status === 'error' ? 'recovering' : 'idle';
    _scheduleUpdate();
  }

  /* ═══════════════════════════════════════════════════════════════════
     PHASE DETECTION
     ═══════════════════════════════════════════════════════════════════ */

  function _detectPhase(text) {
    if (/\b(plan|think|decompos|analyz)\b/i.test(text))         return 'plan';
    if (/\b(cod|impl|writ|generat|build)\b/i.test(text))        return 'code';
    if (/\b(debug|test|fix|verif|patch)\b/i.test(text))        return 'debug';
    if (/\btool:|calling\s+tool|\[TOOL\]/i.test(text))          return 'tool';
    if (/task finished|status=success|✅/i.test(text))          return 'done';
    return null;
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITY
     ═══════════════════════════════════════════════════════════════════ */

  function _esc(s) {
    return String(s ?? '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
  }

  function _fmtTs(ms) {
    if (!ms) return '';
    const d = new Date(ms);
    return `${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }

  /* ═══════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════ */

  window._z36 = {
    registry:        NodeRegistry,
    pressureMemory:  PressureMemory,
    getState:        () => ({ ...S }),
    openInspector:   _openFullInspector,
    emitPulse:       _emitExecutionPulse,
    propagateFocus:  _propagateFocus,
    enrichTimeline:  _enrichTimelineRows,
    update:          _scheduleUpdate,
  };

  /* ═══════════════════════════════════════════════════════════════════
     INIT
     ═══════════════════════════════════════════════════════════════════ */

  function _init() {
    _wireNxBus();

    setTimeout(() => {
      _startNodeObserver();
      _wireHoverSync();
      _injectContinuityThreads();
      _injectForensicPanel();
      _wireTimelineReplayReconstruction();
      _scheduleUpdate();
    }, 1000);

    S.initialized = true;
    console.log('[Phase Z36] Runtime Cohesion + Execution Intelligence active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 1000));
  } else {
    setTimeout(_init, 1000);
  }
})();
