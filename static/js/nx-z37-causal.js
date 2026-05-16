/**
 * nx-z37-causal.js — Phase Z37 Causal Runtime Intelligence + Dependency Cognition
 * ══════════════════════════════════════════════════════════════════════════════════
 *
 * Z37A — True Dependency Lineage Engine
 *         (parent-child graph, ancestor chains, branch divergence)
 * Z37B — Dependency Pressure Propagation
 *         (pressure inheritance, cascade detection, bottleneck, cooling)
 * Z37C — Predictive Failure + Recovery Modeling
 *         (failure prediction, recovery path intelligence, risk forecast)
 * Z37D — Execution Memory Graph
 *         (memory threads, historical patterns, semantic runtime history)
 * Z37E — Causal Replay Immersion
 *         (dependency state replay, time drift, causal focus)
 * Z37F — Operational Visual Maturity
 *         (surface weight, density refinement, calmness)
 *
 * Rules:
 *  - NO new agents. NO new orchestration. NO chatbot elements.
 *  - All logic operational. Causal clarity > feature count.
 *  - RAF-batched DOM writes. Zero layout thrashing.
 *  - Predictive signals are advisory only — never auto-applied.
 */
'use strict';

(function () {
  if (window._z37) return;

  /* ═══════════════════════════════════════════════════════════════════
     Z37A — CAUSAL DEPENDENCY GRAPH
     Extends Z36 NodeRegistry with true parent-child relationships,
     branch tagging, and ancestor chain resolution.
     ═══════════════════════════════════════════════════════════════════ */

  const CausalGraph = (function () {
    // adjacency: parentId → [childId, ...]
    const _edges       = {};  // parentId → Set<childId>
    const _parents     = {};  // childId  → parentId
    const _branches    = {};  // branchId → [nodeId, ...]
    const _nodeBranch  = {};  // nodeId   → branchId
    const _branchTypes = {};  // branchId → 'main'|'retry'|'recovery'|'escalation'

    /* Add a directed edge parent → child */
    function addEdge(parentId, childId, branchType) {
      if (!_edges[parentId])   _edges[parentId]   = new Set();
      _edges[parentId].add(childId);
      _parents[childId] = parentId;

      // Assign branch
      const parentBranch = _nodeBranch[parentId] || 'main';
      let branchId = parentBranch;

      if (branchType && branchType !== 'main') {
        branchId = `${parentId}:${branchType}:${Date.now()}`;
        _branchTypes[branchId] = branchType;
      }
      _nodeBranch[childId] = branchId;
      if (!_branches[branchId]) _branches[branchId] = [];
      if (!_branches[branchId].includes(childId)) _branches[branchId].push(childId);
    }

    /* Register a node as root (no parent) */
    function addRoot(nodeId) {
      _nodeBranch[nodeId] = 'main';
      if (!_branches['main']) _branches['main'] = [];
      if (!_branches['main'].includes(nodeId)) _branches['main'].push(nodeId);
    }

    /* Ancestor chain — ordered from root to nodeId */
    function getAncestors(nodeId) {
      const chain = [];
      let cur = nodeId;
      while (cur && chain.length < 20) {
        chain.unshift(cur);
        cur = _parents[cur];
      }
      return chain;
    }

    /* Immediate children */
    function getChildren(nodeId) {
      return _edges[nodeId] ? Array.from(_edges[nodeId]) : [];
    }

    /* Subtree rooted at nodeId (BFS) */
    function getSubtree(nodeId) {
      const visited = new Set();
      const queue   = [nodeId];
      while (queue.length) {
        const id = queue.shift();
        if (visited.has(id)) continue;
        visited.add(id);
        getChildren(id).forEach(c => queue.push(c));
      }
      return Array.from(visited);
    }

    /* Branch type for a node */
    function getBranchType(nodeId) {
      const branchId = _nodeBranch[nodeId];
      return branchId ? (_branchTypes[branchId] || 'main') : 'main';
    }

    function getBranchId(nodeId) { return _nodeBranch[nodeId] || 'main'; }
    function getParent(nodeId)   { return _parents[nodeId] || null; }
    function getAllBranches()     { return { ...  _branches }; }
    function getBranchNodes(bid) { return (_branches[bid] || []).slice(); }

    /* Dependency trace for inspector: why did nodeId run? */
    function getDependencyTrace(nodeId) {
      const ancestors = getAncestors(nodeId);
      const trace = [];
      for (let i = 0; i < ancestors.length - 1; i++) {
        const from  = ancestors[i];
        const to    = ancestors[i + 1];
        const bType = getBranchType(to);
        trace.push({ from, to, branchType: bType });
      }
      return trace;
    }

    function clear() {
      Object.keys(_edges).forEach(k => delete _edges[k]);
      Object.keys(_parents).forEach(k => delete _parents[k]);
      Object.keys(_branches).forEach(k => delete _branches[k]);
      Object.keys(_nodeBranch).forEach(k => delete _nodeBranch[k]);
      Object.keys(_branchTypes).forEach(k => delete _branchTypes[k]);
    }

    return {
      addEdge, addRoot, getAncestors, getChildren, getSubtree,
      getBranchType, getBranchId, getParent, getDependencyTrace,
      getAllBranches, getBranchNodes, clear,
    };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     Z37B — PRESSURE PROPAGATION ENGINE
     Pressure flows from parent nodes to child nodes with decay.
     ═══════════════════════════════════════════════════════════════════ */

  const PressurePropagation = (function () {
    const INHERIT_FACTOR   = 0.45;  // child inherits 45% of parent pressure
    const COOLING_RATE     = 0.08;  // per cooling cycle
    const CASCADE_THRESHOLD = 0.6;  // above this → cascade detected

    let _coolingTimer = null;

    function propagate(nodeId, sourcePressure) {
      if (!window._z36) return;
      const children = CausalGraph.getChildren(nodeId);
      for (const childId of children) {
        const inheritedPressure = sourcePressure * INHERIT_FACTOR;
        if (inheritedPressure > 0.05) {
          const existing = _z36.registry.get(childId);
          if (existing) {
            const newPressure = Math.min(1, (existing.heat || 0) + inheritedPressure);
            _z36.registry.upsert(childId, { heat: newPressure });
          }
        }
        // Recursive with further decay
        if (inheritedPressure > 0.1) {
          propagate(childId, inheritedPressure);
        }
      }
    }

    function cool(nodeId) {
      if (!window._z36) return;
      const node = _z36.registry.get(nodeId);
      if (!node) return;
      const cooled = Math.max(0, (node.heat || 0) - COOLING_RATE);
      _z36.registry.upsert(nodeId, { heat: cooled });

      // Also cool children proportionally
      const children = CausalGraph.getChildren(nodeId);
      children.forEach(cId => {
        const child = _z36.registry.get(cId);
        if (child && (child.heat || 0) > 0.05) {
          _z36.registry.upsert(cId, { heat: Math.max(0, child.heat - COOLING_RATE * 0.5) });
        }
      });
    }

    function detectCascades() {
      if (!window._z36) return [];
      const nodes = _z36.registry.all();
      const cascades = [];

      for (const node of nodes) {
        if ((node.heat || 0) >= CASCADE_THRESHOLD) {
          const subtree    = CausalGraph.getSubtree(node.id);
          const unstable   = subtree.filter(id => {
            const n = _z36.registry.get(id);
            return n && (n.heat || 0) >= 0.3;
          });
          if (unstable.length >= 2) {
            cascades.push({ root: node.id, affected: unstable, maxPressure: node.heat });
          }
        }
      }
      return cascades;
    }

    function detectBottlenecks() {
      if (!window._z36) return [];
      const nodes = _z36.registry.all();
      return nodes
        .filter(n => (n.retries || 0) >= 2 || (n.errors || 0) >= 1)
        .sort((a, b) => (b.retries + b.errors) - (a.retries + a.errors))
        .slice(0, 3)
        .map(n => ({ id: n.id, retries: n.retries, errors: n.errors, heat: n.heat }));
    }

    function startCooling(nodeId) {
      // Cool a recovered node over multiple cycles
      let cycles = 0;
      const interval = setInterval(() => {
        cool(nodeId);
        cycles++;
        if (cycles >= 6) clearInterval(interval);
      }, 2500);
    }

    return { propagate, cool, detectCascades, detectBottlenecks, startCooling, CASCADE_THRESHOLD };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     Z37C — PREDICTIVE FAILURE + RECOVERY MODELING
     ═══════════════════════════════════════════════════════════════════ */

  const Predictor = (function () {
    // Risk levels
    const LEVELS = ['LOW', 'ELEVATED', 'HIGH', 'CRITICAL'];

    function getRiskLevel(pressure, errorCount, retryCount, confidence) {
      let score = 0;
      score += pressure * 3;
      score += Math.min(1, errorCount  / 3) * 2;
      score += Math.min(1, retryCount  / 6) * 1.5;
      score += confidence != null ? Math.max(0, 1 - confidence) * 1.5 : 0;

      const normalized = Math.min(1, score / 8);
      if (normalized >= 0.75) return 'CRITICAL';
      if (normalized >= 0.5)  return 'HIGH';
      if (normalized >= 0.25) return 'ELEVATED';
      return 'LOW';
    }

    function predictNextUnstableNode() {
      if (!window._z36) return null;
      const nodes    = _z36.registry.all();
      const running  = nodes.filter(n => n.state === 'running' || n.state === 'pending');
      // Highest heat pending/running node is the most likely next failure
      const sorted   = running.sort((a, b) => (b.heat || 0) - (a.heat || 0));
      return sorted[0] || null;
    }

    function getEscalationProbability(nodeId) {
      if (!window._z36) return 0;
      const node = _z36.registry.get(nodeId);
      if (!node) return 0;
      // Simple linear model
      const retryFactor   = Math.min(1, (node.retries || 0) / 5) * 0.4;
      const errorFactor   = Math.min(1, (node.errors  || 0) / 3) * 0.4;
      const heatFactor    = (node.heat || 0) * 0.2;
      return Math.min(0.99, retryFactor + errorFactor + heatFactor);
    }

    function getRecoveryConfidence(nodeId) {
      if (!window._z36) return null;
      const node = _z36.registry.get(nodeId);
      if (!node || !node.recoveryHistory?.length) return null;
      const successes = node.recoveryHistory.filter(r => r.success).length;
      return successes / node.recoveryHistory.length;
    }

    function getRetryAmplificationRisk(nodeId) {
      if (!window._z36) return 'none';
      const node    = _z36.registry.get(nodeId);
      if (!node) return 'none';
      const retries = node.retries || 0;
      if (retries >= 5) return 'severe';
      if (retries >= 3) return 'elevated';
      if (retries >= 1) return 'low';
      return 'none';
    }

    /* System-wide forecast */
    function getSystemForecast() {
      if (!window._z36) return { risk: 'LOW', cascades: [], bottlenecks: [] };
      const nodes    = _z36.registry.all();
      const pressure = nodes.reduce((s, n) => s + (n.heat || 0), 0) / Math.max(1, nodes.length);
      const errors   = nodes.reduce((s, n) => s + (n.errors || 0), 0);
      const retries  = nodes.reduce((s, n) => s + (n.retries || 0), 0);

      let z35Conf = null;
      let z35Pressure = 0;
      if (window._z35) {
        const st = _z35.getState();
        z35Conf     = st.confidence;
        z35Pressure = st.pressure || 0;
      }

      const risk       = getRiskLevel(Math.max(pressure, z35Pressure), errors, retries, z35Conf);
      const cascades   = PressurePropagation.detectCascades();
      const bottlenecks = PressurePropagation.detectBottlenecks();
      const nextUnstable = predictNextUnstableNode();

      return { risk, cascades, bottlenecks, nextUnstable, pressure, errors, retries };
    }

    return {
      getRiskLevel,
      predictNextUnstableNode,
      getEscalationProbability,
      getRecoveryConfidence,
      getRetryAmplificationRisk,
      getSystemForecast,
      LEVELS,
    };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     Z37D — EXECUTION MEMORY GRAPH
     Persist recovery chains, repeated bottlenecks, historical patterns
     ═══════════════════════════════════════════════════════════════════ */

  const ExecutionMemory = (function () {
    const _unstableHistory  = {};  // nodeId → count
    const _expensiveHistory = {};  // nodeId → total dur_ms
    const _recoveryPaths    = {};  // nodeId → [{type, count, successRate}]
    const _escalationHistory= [];  // [{nodeId, ts, resolved}]
    const MAX_HISTORY       = 100;

    function recordCompletion(nodeId, durMs, wasUnstable) {
      if (wasUnstable) {
        _unstableHistory[nodeId] = (_unstableHistory[nodeId] || 0) + 1;
      }
      if (durMs != null) {
        _expensiveHistory[nodeId] = (_expensiveHistory[nodeId] || 0) + durMs;
      }
    }

    function recordRecovery(nodeId, recoveryType, success) {
      if (!_recoveryPaths[nodeId]) _recoveryPaths[nodeId] = {};
      if (!_recoveryPaths[nodeId][recoveryType]) {
        _recoveryPaths[nodeId][recoveryType] = { count: 0, successes: 0 };
      }
      _recoveryPaths[nodeId][recoveryType].count++;
      if (success) _recoveryPaths[nodeId][recoveryType].successes++;
    }

    function recordEscalation(nodeId, resolved) {
      _escalationHistory.push({ nodeId, ts: Date.now(), resolved });
      if (_escalationHistory.length > MAX_HISTORY) _escalationHistory.shift();
    }

    function getNodeHistory(nodeId) {
      const unstableCount = _unstableHistory[nodeId] || 0;
      const totalDur      = _expensiveHistory[nodeId] || null;
      const recovery      = _recoveryPaths[nodeId] || {};
      const escalations   = _escalationHistory.filter(e => e.nodeId === nodeId);

      const insight = _generateInsight(nodeId, unstableCount, totalDur, recovery, escalations);
      return { nodeId, unstableCount, totalDur, recovery, escalations, insight };
    }

    function _generateInsight(nodeId, unstableCount, totalDur, recovery, escalations) {
      const parts = [];
      if (unstableCount >= 3)      parts.push(`historically unstable (${unstableCount} occurrences)`);
      if (totalDur != null && totalDur > 30000) parts.push(`execution-heavy (~${Math.round(totalDur/1000)}s cumulative)`);
      if (escalations.length >= 1) parts.push(`escalated ${escalations.length}× in this session`);

      const recovTypes = Object.keys(recovery);
      if (recovTypes.length) {
        const best = recovTypes.reduce((best, type) => {
          const r = recovery[type];
          const rate = r.count > 0 ? r.successes / r.count : 0;
          return rate > (recovery[best]?.successes / (recovery[best]?.count || 1) || 0) ? type : best;
        }, recovTypes[0]);
        const bestRate = Math.round((recovery[best].successes / recovery[best].count) * 100);
        if (bestRate > 0) parts.push(`best recovery: "${best}" (${bestRate}% success)`);
      }

      return parts.length ? parts.join(' · ') : null;
    }

    function getMostUnstableNodes(n) {
      return Object.entries(_unstableHistory)
        .sort((a, b) => b[1] - a[1])
        .slice(0, n)
        .map(([id, count]) => ({ id, count }));
    }

    function getMostExpensiveNodes(n) {
      return Object.entries(_expensiveHistory)
        .sort((a, b) => b[1] - a[1])
        .slice(0, n)
        .map(([id, totalDur]) => ({ id, totalDur }));
    }

    function clear() {
      Object.keys(_unstableHistory).forEach(k => delete _unstableHistory[k]);
      Object.keys(_expensiveHistory).forEach(k => delete _expensiveHistory[k]);
      Object.keys(_recoveryPaths).forEach(k => delete _recoveryPaths[k]);
      _escalationHistory.length = 0;
    }

    return {
      recordCompletion, recordRecovery, recordEscalation,
      getNodeHistory, getMostUnstableNodes, getMostExpensiveNodes, clear,
    };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     Z37 RISK INDICATOR — Persistent system-wide risk badge
     ═══════════════════════════════════════════════════════════════════ */

  function _injectRiskIndicator() {
    if (document.getElementById('z37RiskIndicator')) return;

    const el = document.createElement('div');
    el.id = 'z37RiskIndicator';
    el.className = 'z37-risk-indicator z37-risk-LOW';
    el.innerHTML = `
      <span class="z37-risk-label">RISK</span>
      <span id="z37RiskLevel" class="z37-risk-level">LOW</span>
    `;

    // Mount in mission bar area next to Z35 indicators
    const missionBar = document.getElementById('z35MissionBar');
    if (missionBar) {
      const inds = missionBar.querySelector('.z35-mission-indicators');
      if (inds) inds.appendChild(el);
    } else {
      const hdr = document.querySelector('.z30-dag-panel-hdr');
      if (hdr) hdr.appendChild(el);
    }
  }

  function _updateRiskIndicator(risk) {
    const el       = document.getElementById('z37RiskIndicator');
    const levelEl  = document.getElementById('z37RiskLevel');
    if (!el || !levelEl) return;

    el.className   = `z37-risk-indicator z37-risk-${risk}`;
    levelEl.textContent = risk;
    document.documentElement.setAttribute('data-z37-risk', risk);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z37 CAUSAL INSPECTOR SECTION
     Appended to z36ForensicSection: dependency trace, forecast
     ═══════════════════════════════════════════════════════════════════ */

  function _injectCausalSection() {
    if (document.getElementById('z37CausalSection')) return;

    const sec = document.createElement('div');
    sec.id = 'z37CausalSection';
    sec.className = 'z37-causal-section';

    const z36 = document.getElementById('z36ForensicSection')
              || document.getElementById('z34InspectorBody');
    if (z36) z36.appendChild(sec);
  }

  function _updateCausalSection(nodeId) {
    const sec = document.getElementById('z37CausalSection');
    if (!sec) return;

    /* Dependency trace */
    const trace = CausalGraph.getDependencyTrace(nodeId);
    const traceHtml = trace.length
      ? `<div class="z37-causal-block">
           <div class="z37-causal-title">Dependency Trace</div>
           <div class="z37-dep-chain">
             ${trace.map(t =>
               `<div class="z37-dep-step">
                  <span class="z37-dep-node z37-branch-${t.branchType}">${_esc(t.from)}</span>
                  <span class="z37-dep-arrow">→</span>
                </div>`
             ).join('')}
             <span class="z37-dep-node z37-dep-active">${_esc(nodeId)}</span>
           </div>
         </div>`
      : '';

    /* Execution memory insight */
    const history = ExecutionMemory.getNodeHistory(nodeId);
    const insightHtml = history.insight
      ? `<div class="z37-causal-block">
           <div class="z37-causal-title">Runtime Memory</div>
           <div class="z37-insight-text">${_esc(history.insight)}</div>
         </div>`
      : '';

    /* Predictive forecast for this node */
    const escProb    = Predictor.getEscalationProbability(nodeId);
    const recovConf  = Predictor.getRecoveryConfidence(nodeId);
    const retryRisk  = Predictor.getRetryAmplificationRisk(nodeId);
    const branchType = CausalGraph.getBranchType(nodeId);

    const forecastHtml = `<div class="z37-causal-block">
      <div class="z37-causal-title">Node Forecast</div>
      <div class="z37-forecast-grid">
        <div class="z37-fc-row">
          <span class="z37-fc-label">Branch</span>
          <span class="z37-fc-val z37-branch-${branchType}">${branchType}</span>
        </div>
        <div class="z37-fc-row">
          <span class="z37-fc-label">Escalation Prob</span>
          <span class="z37-fc-val ${escProb > 0.6 ? 'z37-risk-val-critical' : escProb > 0.3 ? 'z37-risk-val-elevated' : ''}">${Math.round(escProb * 100)}%</span>
        </div>
        <div class="z37-fc-row">
          <span class="z37-fc-label">Recovery Conf</span>
          <span class="z37-fc-val ${recovConf != null ? (recovConf >= 0.7 ? 'z37-good' : recovConf >= 0.4 ? 'z37-warn' : 'z37-bad') : ''}">${recovConf != null ? Math.round(recovConf * 100) + '%' : '—'}</span>
        </div>
        <div class="z37-fc-row">
          <span class="z37-fc-label">Retry Risk</span>
          <span class="z37-fc-val z37-retry-${retryRisk}">${retryRisk}</span>
        </div>
      </div>
    </div>`;

    sec.innerHTML = traceHtml + insightHtml + forecastHtml;
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z37 SYSTEM FORECAST BAR
     Minimal 1-line system-wide risk strip
     ═══════════════════════════════════════════════════════════════════ */

  function _injectForecastBar() {
    if (document.getElementById('z37ForecastBar')) return;

    const bar = document.createElement('div');
    bar.id = 'z37ForecastBar';
    bar.className = 'z37-forecast-bar';
    bar.innerHTML = `
      <div class="z37-fc-bar-content">
        <span class="z37-fc-bar-label">FORECAST</span>
        <span id="z37ForecastText" class="z37-fc-bar-text">—</span>
        <span id="z37NextUnstable" class="z37-fc-next"></span>
        <div class="z37-cascade-badges" id="z37CascadeBadges"></div>
      </div>
    `;

    // Insert below suggestion tray / mission bar
    const suggTray = document.getElementById('z35SuggestionTray');
    if (suggTray && suggTray.parentNode) {
      suggTray.parentNode.insertBefore(bar, suggTray.nextSibling);
    } else {
      const missionBar = document.getElementById('z35MissionBar');
      if (missionBar && missionBar.parentNode) {
        missionBar.parentNode.insertBefore(bar, missionBar.nextSibling);
      }
    }
  }

  function _updateForecastBar(forecast) {
    const textEl     = document.getElementById('z37ForecastText');
    const nextEl     = document.getElementById('z37NextUnstable');
    const cascadeEl  = document.getElementById('z37CascadeBadges');

    if (!textEl) return;

    const risk = forecast.risk;
    textEl.textContent = risk;
    textEl.className   = `z37-fc-bar-text z37-risk-text-${risk}`;

    if (nextEl) {
      if (forecast.nextUnstable && risk !== 'LOW') {
        nextEl.textContent = `watch: ${forecast.nextUnstable.id}`;
        nextEl.style.display = 'inline';
      } else {
        nextEl.style.display = 'none';
      }
    }

    if (cascadeEl) {
      cascadeEl.innerHTML = forecast.cascades.slice(0, 2).map(c =>
        `<span class="z37-cascade-badge" title="Cascade: ${_esc(c.affected.join(', '))}">cascade</span>`
      ).join('');
    }

    // Only show bar on non-LOW risk or active cascades
    const bar = document.getElementById('z37ForecastBar');
    if (bar) {
      bar.classList.toggle('z37-fc-bar-visible', risk !== 'LOW' || forecast.cascades.length > 0);
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z37E — CAUSAL REPLAY IMMERSION
     Apply DAG node depth states during replay based on causal position
     ═══════════════════════════════════════════════════════════════════ */

  function _applyCausalReplayDepth(cursorIdx) {
    if (!window._z34) return;
    const events = _z34.getTimelineEvents();
    if (!events.length) return;

    // Nodes that appeared before cursor: 'replayed'
    // Nodes at cursor: 'active-replay'
    // Nodes after cursor: 'future-replay'
    const before = new Set();
    const active  = new Set();
    const after   = new Set();

    events.forEach((ev, i) => {
      if (!ev.nodeId) return;
      if (i < cursorIdx)       before.add(ev.nodeId);
      else if (i === cursorIdx) active.add(ev.nodeId);
      else                      after.add(ev.nodeId);
    });

    // Apply depth classes
    before.forEach(id => _setNodeReplayDepth(id, 'before'));
    active.forEach(id => _setNodeReplayDepth(id, 'active'));
    after.forEach(id  => _setNodeReplayDepth(id, 'after'));
  }

  function _setNodeReplayDepth(nodeId, depth) {
    const el = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
    if (!el) return;
    el.setAttribute('data-z37-replay', depth);
  }

  function _clearReplayDepth() {
    document.querySelectorAll('[data-z37-replay]').forEach(el => el.removeAttribute('data-z37-replay'));
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z37F — OPERATIONAL VISUAL MATURITY
     Surface weight: critical surfaces gain subtle depth during execution
     ═══════════════════════════════════════════════════════════════════ */

  function _applyVisualMaturity(risk) {
    const dagPanel  = document.querySelector('.z30-dag-panel');
    const missionBar = document.getElementById('z35MissionBar');

    // During HIGH/CRITICAL risk, inspector and mission bar gain prominence
    if (dagPanel) {
      dagPanel.classList.toggle('z37-prominent', risk === 'HIGH' || risk === 'CRITICAL');
    }
    if (missionBar) {
      missionBar.classList.toggle('z37-prominent', risk === 'CRITICAL');
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     INGEST LINEAGE FROM DAG EVENTS
     ═══════════════════════════════════════════════════════════════════ */

  /* Infer parent-child from execution order in NodeRegistry */
  function _inferLineageFromOrder() {
    if (!window._z36) return;
    const nodes     = _z36.registry.all();
    const sorted    = nodes.sort((a, b) => (a.ts_start || 0) - (b.ts_start || 0));
    const prevNode  = {};  // phase → last node before this one

    for (let i = 0; i < sorted.length; i++) {
      const n = sorted[i];
      if (i === 0) {
        CausalGraph.addRoot(n.id);
      } else {
        const parent = sorted[i - 1];
        if (!CausalGraph.getParent(n.id)) {
          // Determine branch type from node state
          const branchType = n.retries > 0 ? 'retry'
                           : n.recoveryHistory?.length > 0 ? 'recovery'
                           : 'main';
          CausalGraph.addEdge(parent.id, n.id, branchType);
        }
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     SCHEDULED UPDATE
     ═══════════════════════════════════════════════════════════════════ */

  let _rafPending = false;

  function _scheduleUpdate() {
    if (_rafPending) return;
    _rafPending = true;
    requestAnimationFrame(_applyUpdate);
  }

  function _applyUpdate() {
    _rafPending = false;
    _inferLineageFromOrder();
    const forecast = Predictor.getSystemForecast();
    _updateRiskIndicator(forecast.risk);
    _updateForecastBar(forecast);
    _applyVisualMaturity(forecast.risk);
  }

  /* ═══════════════════════════════════════════════════════════════════
     NXBUS WIRING
     ═══════════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    /* Session */
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z37' });
    NxBus.on('session.done',  () => _onSessionEnd('success'), { owner: 'z37' });
    NxBus.on('session.error', () => _onSessionEnd('error'),   { owner: 'z37' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED || 'nx:session:created', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z37' });

    /* DAG node events with lineage */
    NxBus.on('dag.node.selected', (e) => {
      if (!e?.node) return;
      const node = e.node;
      const nid  = node.id;

      // If parentId provided by Z30, register the edge
      if (node.parentId) {
        CausalGraph.addEdge(node.parentId, nid, node.branchType || 'main');
      } else {
        // Infer on next update
        _scheduleUpdate();
      }

      // Update causal inspector
      _updateCausalSection(nid);

      // Record to execution memory
      const existing = window._z36 ? _z36.registry.get(nid) : null;
      if (existing) {
        ExecutionMemory.recordCompletion(
          nid,
          existing.dur_ms,
          (existing.errors || 0) > 0 || (existing.retries || 0) > 0
        );
      }
    }, { owner: 'z37' });

    NxBus.on('dag.node.done', (e) => {
      if (!e?.id) return;
      const nid    = e.id;
      const node   = window._z36 ? _z36.registry.get(nid) : null;
      const wasUnstable = node && ((node.errors || 0) > 0 || (node.retries || 0) > 0);
      ExecutionMemory.recordCompletion(nid, node?.dur_ms, wasUnstable);
      PressurePropagation.startCooling(nid);
      _scheduleUpdate();
    }, { owner: 'z37' });

    NxBus.on('dag.node.error', (e) => {
      if (!e?.id) return;
      // Propagate pressure to children
      const node = window._z36 ? _z36.registry.get(e.id) : null;
      PressurePropagation.propagate(e.id, node?.heat || 0.7);
      _scheduleUpdate();
    }, { owner: 'z37' });

    /* Z32 replan applied → record recovery */
    NxBus.on('z32.replan.applied', (e) => {
      const nid = e?.nodeId;
      if (nid) {
        ExecutionMemory.recordRecovery(nid, e?.action || 'replan', true);
        PressurePropagation.startCooling(nid);
      }
      _scheduleUpdate();
    }, { owner: 'z37' });

    /* Z29 HITL escalation */
    NxBus.on('z29.hitl.escalated', (e) => {
      const nid = e?.nodeId || (window._z36 ? _z36.getState().activeNodeId : null);
      if (nid) ExecutionMemory.recordEscalation(nid, false);
      _scheduleUpdate();
    }, { owner: 'z37' });
    NxBus.on('z29.hitl.resolved', (e) => {
      const nid = e?.nodeId;
      if (nid) {
        ExecutionMemory.recordEscalation(nid, true);
        ExecutionMemory.recordRecovery(nid, 'hitl', true);
      }
      _scheduleUpdate();
    }, { owner: 'z37' });

    /* Z34 replay cursor changes → causal replay depth */
    NxBus.on('z34.cursor.changed', (state) => {
      if (state?.mode === 'replay') {
        _applyCausalReplayDepth(state.position);
      } else {
        _clearReplayDepth();
      }
    }, { owner: 'z37' });

    /* Z36 node focus → update causal section */
    NxBus.on('z36.node.focus', (e) => {
      if (e?.id) _updateCausalSection(e.id);
    }, { owner: 'z37' });

    /* Z36 node registry updates → pressure propagation */
    NxBus.on('z36.node.updated', (e) => {
      if (!e?.id) return;
      const node = window._z36 ? _z36.registry.get(e.id) : null;
      if (node && (node.heat || 0) >= PressurePropagation.CASCADE_THRESHOLD) {
        PressurePropagation.propagate(e.id, node.heat);
      }
    }, { owner: 'z37' });

    /* Log rows → lineage inference from structured log patterns */
    NxBus.on('agent.log_row', (e) => {
      if (!e?.text) return;
      // Detect explicit parent references in logs: "[plan] -> [code]"
      const edgeM = /\[(\w+)\]\s*(?:→|->)\s*\[(\w+)\]/i.exec(e.text);
      if (edgeM) {
        const [, parentId, childId] = edgeM;
        CausalGraph.addEdge(parentId.toLowerCase(), childId.toLowerCase(), 'main');
      }
      _scheduleUpdate();
    }, { owner: 'z37' });

    /* Periodic forecast refresh (every 12s) */
    setInterval(() => {
      _inferLineageFromOrder();
      const forecast = Predictor.getSystemForecast();
      _updateRiskIndicator(forecast.risk);
      _updateForecastBar(forecast);
    }, 12000);
  }

  /* ═══════════════════════════════════════════════════════════════════
     SESSION LIFECYCLE
     ═══════════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid) {
    CausalGraph.clear();
    // ExecutionMemory persists across sessions (cross-session learning)
    _updateRiskIndicator('LOW');
    _scheduleUpdate();
  }

  function _onSessionEnd(status) {
    if (status === 'error') {
      const forecast = Predictor.getSystemForecast();
      _updateRiskIndicator(forecast.risk);
    } else {
      setTimeout(() => _updateRiskIndicator('LOW'), 3000);
    }
    _clearReplayDepth();
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITY
     ═══════════════════════════════════════════════════════════════════ */

  function _esc(s) {
    return String(s ?? '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
  }

  /* ═══════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════ */

  window._z37 = {
    causalGraph:   CausalGraph,
    predictor:     Predictor,
    memory:        ExecutionMemory,
    pressure:      PressurePropagation,
    getState:      () => ({
      risk: document.getElementById('z37RiskLevel')?.textContent || 'LOW',
    }),
    updateCausal:  _updateCausalSection,
    update:        _scheduleUpdate,
  };

  /* ═══════════════════════════════════════════════════════════════════
     INIT
     ═══════════════════════════════════════════════════════════════════ */

  function _init() {
    _wireNxBus();

    setTimeout(() => {
      _injectRiskIndicator();
      _injectCausalSection();
      _injectForecastBar();
      _scheduleUpdate();
    }, 1100);

    console.log('[Phase Z37] Causal Runtime Intelligence + Dependency Cognition active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 1100));
  } else {
    setTimeout(_init, 1100);
  }
})();
