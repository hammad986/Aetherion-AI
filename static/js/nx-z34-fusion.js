/**
 * nx-z34-fusion.js — Phase Z34 Forensic Replay Fusion + Deep Execution Presence
 * ════════════════════════════════════════════════════════════════════════════════
 *
 * Z34A — DAG ↔ Timeline Forensic Fusion (shared replay cursor, bidirectional sync)
 * Z34B — Execution Spatial Presence (live depth layer, runtime immersion)
 * Z34C — Forensic Inspector Evolution (node-centric reasoning surface)
 * Z34D — Execution Continuity Memory (failure lineage, recovery mapping)
 * Z34E — Visual Depth + Execution Immersion (layered hierarchy, motion governance)
 *
 * Rules:
 *  - NO fake data. All state derives from live NxBus events + API.
 *  - RAF-batched DOM writes only. No layout thrashing.
 *  - Calm execution environment. No neon. No hype. No telemetry overload.
 *  - Motion is slow, subtle, state-driven.
 *  - Replay coherence > new capabilities.
 */
'use strict';

(function () {
  if (window._z34) return;

  /* ═══════════════════════════════════════════════════════════════════
     SHARED REPLAY CURSOR (Z34A core)
     Single source of truth for replay position across:
     DAG, Timeline, Forensic Inspector, Replay Viewer
     ═══════════════════════════════════════════════════════════════════ */
  const ReplayCursor = (function () {
    let _position = 0;        // current cursor index
    let _total    = 0;        // total events in this replay session
    let _ts       = 0;        // timestamp at cursor
    let _sid      = null;     // session being replayed
    let _mode     = 'live';   // 'live' | 'replay'
    const _listeners = [];

    function seek(index, ts) {
      _position = Math.max(0, Math.min(index, Math.max(0, _total - 1)));
      if (ts != null) _ts = ts;
      _notify();
    }

    function setTotal(n) { _total = n; }
    function setSession(sid) { _sid = sid; }
    function setMode(m) {
      _mode = m;
      document.documentElement.setAttribute('data-z34-mode', m);
      _notify();
    }

    function getState() {
      return { position: _position, total: _total, ts: _ts, sid: _sid, mode: _mode };
    }

    function onChange(fn) { _listeners.push(fn); }

    function _notify() {
      const state = getState();
      _listeners.forEach(fn => { try { fn(state); } catch (_) {} });
      if (window.NxBus) NxBus.emit('z34.cursor.changed', state);
    }

    return { seek, setTotal, setSession, setMode, getState, onChange };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     STATE
     ═══════════════════════════════════════════════════════════════════ */
  const S = {
    sid:            null,
    activeNodeId:   null,
    timelineEvents: [],       // [{idx, ts, type, nodeId, data}]
    nodeIndex:      {},       // nodeId → {logs, retries, confidence, ts_start, ts_end, ...}
    sessionThreads: [],       // Z34D: cross-session continuity threads
    failureLineage: [],       // Z34D: recurring failure chains
    recoveryMap:    {},       // Z34D: replan → outcome
    depthLayer:     'runtime',// 'runtime'|'replay'|'forensic'|'timeline'
    rafPending:     false,
    initialized:    false,
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z34A — DAG ↔ TIMELINE FORENSIC FUSION
     ═══════════════════════════════════════════════════════════════════ */

  /* Timeline event → seek DAG to matching snapshot */
  function _onTimelineEventClick(idx) {
    const ev = S.timelineEvents[idx];
    if (!ev) return;

    ReplayCursor.seek(idx, ev.ts);

    // Restore DAG node states up to this point
    if (window.NxDagEngine) {
      const nodeStates = _reconstructNodeStatesAt(idx);
      if (nodeStates.length) {
        NxDagEngine.applySnapshot({ nodes: nodeStates, edges: _buildEdges(nodeStates) });
      }
    }

    // Highlight the related node in the inspector
    if (ev.nodeId) {
      _openForensicInspector(ev.nodeId, idx);
    }

    _emitSyncPulse('timeline→dag');
  }

  /* DAG node select → seek timeline to related events */
  function _onDagNodeSelected(nodeId) {
    S.activeNodeId = nodeId;

    // Find earliest timeline event for this node
    const idx = S.timelineEvents.findIndex(e => e.nodeId === nodeId);
    if (idx >= 0) {
      ReplayCursor.seek(idx, S.timelineEvents[idx].ts);
      _scrollTimelineToIndex(idx);
    }

    _openForensicInspector(nodeId, idx >= 0 ? idx : null);
    _emitSyncPulse('dag→timeline');
  }

  /* Reconstruct node state up to timeline position idx */
  function _reconstructNodeStatesAt(idx) {
    const states = {};
    for (let i = 0; i <= idx; i++) {
      const ev = S.timelineEvents[i];
      if (!ev || !ev.nodeId) continue;
      if (!states[ev.nodeId]) {
        states[ev.nodeId] = {
          id:     ev.nodeId,
          label:  ev.nodeId.charAt(0).toUpperCase() + ev.nodeId.slice(1),
          state:  'pending',
          retries: 0,
          semantic_confidence: null,
          is_critical_path: false,
        };
      }
      const n = states[ev.nodeId];
      if (ev.type === 'node-done')  n.state = 'done';
      if (ev.type === 'node-error') n.state = 'error';
      if (ev.type === 'retry')      n.retries++;
      if (ev.type === 'conf-drop' && ev.data?.pct != null) {
        n.semantic_confidence = ev.data.pct / 100;
      }
      if (ev.type === 'recovery') n.state = 'done';
    }
    return Object.values(states);
  }

  function _buildEdges(nodes) {
    return nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id }));
  }

  /* Scroll timeline dock to the event at idx */
  function _scrollTimelineToIndex(idx) {
    const dock = document.getElementById('z33TimelineDock');
    if (!dock) return;
    const rows = dock.querySelectorAll('.z33-tl-event, .z33-tl-group');
    const row = rows[idx];
    if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    // Highlight
    rows.forEach(r => r.classList.remove('z34-tl-cursor'));
    if (row) {
      row.classList.add('z34-tl-cursor');
      setTimeout(() => row.classList.remove('z34-tl-cursor'), 2000);
    }
  }

  /* Visual sync pulse on health bar sync dot */
  function _emitSyncPulse(direction) {
    const dot = document.getElementById('z30TimelineSyncDot');
    if (dot) {
      dot.classList.add('z34-sync-pulse');
      dot.title = `Synced: ${direction} @ ${new Date().toLocaleTimeString()}`;
      setTimeout(() => dot && dot.classList.remove('z34-sync-pulse'), 1200);
    }
  }

  /* Wire timeline event rows to cursor (applied after render) */
  function _wireTimelineRows() {
    const dock = document.getElementById('z33TimelineDock');
    if (!dock) return;
    dock.querySelectorAll('.z33-tl-event, .z33-tl-group').forEach((row, idx) => {
      if (row._z34wired) return;
      row._z34wired = true;
      row.addEventListener('click', (e) => {
        e.stopPropagation();
        _onTimelineEventClick(idx);
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z34C — FORENSIC INSPECTOR EVOLUTION
     Node-centric reasoning surface with recovery narrative
     ═══════════════════════════════════════════════════════════════════ */

  function _openForensicInspector(nodeId, timelineIdx) {
    S.activeNodeId = nodeId;

    const panel  = document.getElementById('z34InspectorPanel');
    const title  = document.getElementById('z34InspectorTitle');
    const body   = document.getElementById('z34InspectorBody');
    if (!panel || !body) return;

    const node = S.nodeIndex[nodeId] || {};

    if (title) {
      title.textContent = nodeId.charAt(0).toUpperCase() + nodeId.slice(1);
    }
    panel.classList.add('open');

    /* Build timeline position context */
    const tlCtx = timelineIdx != null
      ? `<div class="z34-insp-tl-pos">
           <span class="z34-insp-label">Timeline Position</span>
           <span class="z34-insp-val">${timelineIdx + 1} / ${S.timelineEvents.length}</span>
         </div>`
      : '';

    /* Confidence drift strip */
    const confHistory = node.confHistory || [];
    const driftHtml = confHistory.length >= 2
      ? `<div class="z34-insp-section">
           <div class="z34-insp-section-title">Confidence Drift</div>
           <div class="z34-conf-drift-strip">
             ${confHistory.map((c, i) => {
               const pct = Math.round(c * 100);
               const cls = c >= 0.75 ? 'hi' : c >= 0.45 ? 'med' : 'low';
               return `<div class="z34-conf-drift-bar ${cls}" style="height:${pct}%" title="${pct}%"></div>`;
             }).join('')}
           </div>
         </div>`
      : '';

    /* Retry history */
    const retryLogs = node.retryLogs || [];
    const retryHtml = retryLogs.length
      ? `<div class="z34-insp-section">
           <div class="z34-insp-section-title">Retry History (${retryLogs.length})</div>
           ${retryLogs.map((r, i) => `
             <div class="z34-insp-retry-row">
               <span class="z34-insp-retry-n">#${i + 1}</span>
               <span class="z34-insp-retry-text">${_esc(r.text || '')}</span>
               <span class="z34-insp-retry-ts">${_fmtTs(r.ts)}</span>
             </div>
           `).join('')}
         </div>`
      : '';

    /* Dependency lineage */
    const deps = _getNodeDependencies(nodeId);
    const depsHtml = deps.length
      ? `<div class="z34-insp-section">
           <div class="z34-insp-section-title">Dependency Lineage</div>
           <div class="z34-insp-deps">
             ${deps.map(d => `<span class="z34-insp-dep-node">${_esc(d)}</span>`).join('<span class="z34-insp-dep-arrow">→</span>')}
             <span class="z34-insp-dep-arrow">→</span>
             <span class="z34-insp-dep-node active">${_esc(nodeId)}</span>
           </div>
         </div>`
      : '';

    /* Recovery narrative (Z34C.4) */
    const narrative = _buildRecoveryNarrative(nodeId);
    const narrativeHtml = narrative
      ? `<div class="z34-insp-section">
           <div class="z34-insp-section-title">Recovery Narrative</div>
           ${narrative}
         </div>`
      : '';

    /* Replan/recovery history from Z34D */
    const replans = (S.recoveryMap[nodeId] || []).slice(-5);
    const replansHtml = replans.length
      ? `<div class="z34-insp-section">
           <div class="z34-insp-section-title">Replan History</div>
           ${replans.map(rp => `
             <div class="z34-insp-replan-row">
               <span class="z34-insp-replan-trigger">${_esc(rp.trigger || '?')}</span>
               <span class="z34-insp-dep-arrow">→</span>
               <span class="z34-insp-replan-outcome ${rp.success ? 'success' : 'fail'}">${rp.success ? '✓ recovered' : '✕ escalated'}</span>
             </div>
           `).join('')}
         </div>`
      : '';

    /* Core metrics */
    const dur = node.dur_ms != null
      ? (node.dur_ms >= 1000 ? (node.dur_ms / 1000).toFixed(1) + 's' : node.dur_ms + 'ms')
      : '—';
    const confPct = node.confidence != null ? Math.round(node.confidence * 100) + '%' : '—';
    const confCls = node.confidence == null ? '' : node.confidence >= 0.75 ? 'hi' : node.confidence >= 0.45 ? 'med' : 'low';

    body.innerHTML = `
      ${tlCtx}
      <div class="z34-insp-metrics">
        <div class="z34-insp-metric">
          <span class="z34-insp-label">State</span>
          <span class="z34-insp-state-dot ${node.state || 'pending'}"></span>
          <span class="z34-insp-val">${_esc(node.state || 'pending')}</span>
        </div>
        <div class="z34-insp-metric">
          <span class="z34-insp-label">Duration</span>
          <span class="z34-insp-val">${dur}</span>
        </div>
        <div class="z34-insp-metric">
          <span class="z34-insp-label">Retries</span>
          <span class="z34-insp-val ${(node.retries || 0) > 2 ? 'z34-warn' : ''}">${node.retries || 0}</span>
        </div>
        <div class="z34-insp-metric">
          <span class="z34-insp-label">Confidence</span>
          <span class="z34-insp-val z34-conf-${confCls}">${confPct}</span>
        </div>
        <div class="z34-insp-metric">
          <span class="z34-insp-label">Provider</span>
          <span class="z34-insp-val">${_esc(node.provider || '—')}</span>
        </div>
        <div class="z34-insp-metric">
          <span class="z34-insp-label">Tokens</span>
          <span class="z34-insp-val">${node.tokens ? node.tokens.toLocaleString() : '—'}</span>
        </div>
      </div>
      ${driftHtml}
      ${narrativeHtml}
      ${replansHtml}
      ${depsHtml}
      ${retryHtml}
    `;
  }

  function _buildRecoveryNarrative(nodeId) {
    const node = S.nodeIndex[nodeId];
    if (!node) return null;

    const steps = [];

    if (node.state === 'error' || node.hadError) {
      steps.push(`<div class="z34-narr-step before"><span class="z34-narr-icon">◎</span><span>Before failure: ${_esc(node.lastLogBefore || 'Execution progressing normally')}</span></div>`);
      steps.push(`<div class="z34-narr-step failure"><span class="z34-narr-icon">✕</span><span>Failure: ${_esc(node.errorMsg || 'Execution error encountered')}</span></div>`);
    }

    if (node.replanned) {
      steps.push(`<div class="z34-narr-step replan"><span class="z34-narr-icon">⬡</span><span>Replan: ${_esc(node.replanAction || 'Alternative path selected')}</span></div>`);
    }

    if (node.recovered) {
      steps.push(`<div class="z34-narr-step recovery"><span class="z34-narr-icon">⟳</span><span>Recovery: ${_esc(node.recoveryMsg || 'Execution resumed successfully')}</span></div>`);
    }

    if (!steps.length) return null;
    return `<div class="z34-recovery-narrative">${steps.join('')}</div>`;
  }

  function _getNodeDependencies(nodeId) {
    // Reconstruct dependency chain from timeline event order
    const nodeOrder = [];
    for (const ev of S.timelineEvents) {
      if (ev.nodeId && !nodeOrder.includes(ev.nodeId)) {
        nodeOrder.push(ev.nodeId);
        if (ev.nodeId === nodeId) break;
      }
    }
    return nodeOrder.slice(0, -1); // all nodes before this one
  }

  function _closeForensicInspector() {
    const panel = document.getElementById('z34InspectorPanel');
    if (panel) panel.classList.remove('open');
    S.activeNodeId = null;
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z34B — EXECUTION SPATIAL PRESENCE
     Subtle live visual depth during execution
     ═══════════════════════════════════════════════════════════════════ */

  const DepthLayer = (function () {
    let _layer = 'runtime';
    let _rafId = null;
    let _active = false;

    /* Set the active depth layer and apply to DOM */
    function set(layer) {
      _layer = layer;
      S.depthLayer = layer;
      document.documentElement.setAttribute('data-z34-layer', layer);
      _updateDepthIndicator(layer);
    }

    /* Called on each new log row during live execution */
    function onLiveActivity(phase, severity) {
      if (!_active) return;
      // Propagate subtle focus wave across DAG surface
      _propagateWave(phase, severity);
    }

    /* Start live layer — called when session begins */
    function activateLive() {
      _active = true;
      set('runtime');
      _applyRuntimeDepth(true);
    }

    /* Switch to replay layer */
    function activateReplay() {
      _active = false;
      set('replay');
      _applyRuntimeDepth(false);
    }

    /* Switch to forensic layer */
    function activateForensic() {
      _active = false;
      set('forensic');
      _applyRuntimeDepth(false);
    }

    function deactivate() {
      _active = false;
      set('runtime');
      _applyRuntimeDepth(false);
    }

    function _applyRuntimeDepth(on) {
      const surface = document.getElementById('z30DagSurface');
      if (surface) surface.classList.toggle('z34-live-active', on);
      const wrap = document.querySelector('.z30-live-wrap');
      if (wrap) wrap.classList.toggle('z34-execution-active', on);
    }

    /* Phase-specific node focus — highlights active branch calmly */
    function _propagateWave(phase, severity) {
      if (_rafId) return; // throttle to one RAF at a time
      _rafId = requestAnimationFrame(() => {
        _rafId = null;
        // Apply wave class to the dag surface briefly
        const surface = document.getElementById('z30DagSurface');
        if (!surface) return;
        surface.setAttribute('data-z34-phase', phase || '');
        surface.setAttribute('data-z34-severity', severity || 'INFO');
        // Self-clearing after transition
        clearTimeout(surface._z34waveTimer);
        surface._z34waveTimer = setTimeout(() => {
          if (surface) {
            surface.removeAttribute('data-z34-phase');
            surface.removeAttribute('data-z34-severity');
          }
        }, 1800);
      });
    }

    function _updateDepthIndicator(layer) {
      const el = document.getElementById('z34DepthIndicator');
      if (!el) return;
      el.textContent = layer.toUpperCase();
      el.className = `z34-depth-indicator z34-layer-${layer}`;
    }

    return { set, onLiveActivity, activateLive, activateReplay, activateForensic, deactivate };
  })();

  /* ═══════════════════════════════════════════════════════════════════
     Z34D — EXECUTION CONTINUITY MEMORY
     Cross-session failure lineage + recovery success mapping
     ═══════════════════════════════════════════════════════════════════ */

  function _loadContinuityData(sid) {
    if (!sid) return;
    // Load related sessions from Z31 forensic API
    Promise.all([
      fetch(`/api/z31/sessions?limit=20`).then(r => r.json()).catch(() => ({ ok: false })),
      fetch(`/api/z31/sessions?filter=failed&limit=10`).then(r => r.json()).catch(() => ({ ok: false })),
    ]).then(([all, failed]) => {
      if (all.ok && all.sessions) {
        S.sessionThreads = _groupSessionThreads(all.sessions, sid);
      }
      if (failed.ok && failed.sessions) {
        S.failureLineage = _buildFailureLineage(failed.sessions);
      }
      _renderContinuityPanel();
    });
  }

  function _groupSessionThreads(sessions, currentSid) {
    // Group by recency into threads (< 2 hours apart = same thread)
    const threads = [];
    let currentThread = [];
    const sorted = [...sessions].sort((a, b) => (b.age_s || 0) - (a.age_s || 0));
    for (const sess of sorted) {
      if (!currentThread.length || (currentThread[0].age_s - sess.age_s) < 7200) {
        currentThread.unshift(sess);
      } else {
        if (currentThread.length) threads.push([...currentThread]);
        currentThread = [sess];
      }
    }
    if (currentThread.length) threads.push(currentThread);
    return threads;
  }

  function _buildFailureLineage(sessions) {
    // Each failed session = a node in failure chain
    return sessions.slice(0, 8).map((sess, i) => ({
      sid:   sess.session_id,
      age:   sess.age_s,
      snaps: sess.snapshot_count || 0,
    }));
  }

  function _renderContinuityPanel() {
    const panel = document.getElementById('z34ContinuityPanel');
    if (!panel) return;

    const threads = S.sessionThreads;
    const lineage = S.failureLineage;

    if (!threads.length && !lineage.length) {
      panel.innerHTML = `<div class="z34-cont-empty">No continuity data yet</div>`;
      return;
    }

    let html = '';

    if (lineage.length) {
      html += `<div class="z34-cont-section">
        <div class="z34-cont-title">Failure Lineage</div>
        <div class="z34-lineage-chain">
          ${lineage.map((f, i) => `
            <div class="z34-lineage-node ${i === lineage.length - 1 ? 'latest' : ''}"
                 title="${_esc(f.sid)}"
                 onclick="_z31forensics && _z31forensics.loadReplay('${_esc(f.sid)}')">
              <span class="z34-lineage-dot"></span>
              <span class="z34-lineage-age">${_fmtAge(f.age)}</span>
            </div>
          `).join('<span class="z34-lineage-connector">—</span>')}
        </div>
      </div>`;
    }

    if (threads.length) {
      html += `<div class="z34-cont-section">
        <div class="z34-cont-title">Session Threads</div>
        ${threads.slice(0, 3).map((thread, ti) => `
          <div class="z34-thread-row">
            <span class="z34-thread-label">Thread ${ti + 1}</span>
            <div class="z34-thread-sessions">
              ${thread.map(s => `
                <span class="z34-thread-sess-dot ${S.sid === s.session_id ? 'active' : ''}"
                      title="${_esc(s.session_id.slice(-16))}"
                      onclick="_z31forensics && _z31forensics.loadReplay('${_esc(s.session_id)}')">
                </span>
              `).join('')}
            </div>
          </div>
        `).join('')}
      </div>`;
    }

    panel.innerHTML = html;
  }

  /* Register replan outcome for recovery mapping */
  function _recordReplanOutcome(nodeId, trigger, success, details) {
    if (!S.recoveryMap[nodeId]) S.recoveryMap[nodeId] = [];
    S.recoveryMap[nodeId].push({ trigger, success, details, ts: Date.now() });
    // Trim to last 10
    if (S.recoveryMap[nodeId].length > 10) S.recoveryMap[nodeId].shift();
  }

  /* ═══════════════════════════════════════════════════════════════════
     NODE INDEX — track per-node execution data for inspector
     ═══════════════════════════════════════════════════════════════════ */

  function _upsertNode(nodeId, patch) {
    if (!S.nodeIndex[nodeId]) {
      S.nodeIndex[nodeId] = {
        id: nodeId, state: 'running', retries: 0,
        tokens: 0, provider: null, confidence: null,
        confHistory: [], retryLogs: [], dur_ms: null,
        start_ts: Date.now(), last_ts: Date.now(),
        hadError: false, recovered: false, replanned: false,
        errorMsg: null, replanAction: null, recoveryMsg: null,
        lastLogBefore: null,
      };
    }
    const n = S.nodeIndex[nodeId];
    Object.assign(n, patch);

    if (patch.confidence != null) {
      n.confHistory.push(patch.confidence);
      if (n.confHistory.length > 20) n.confHistory.shift();
    }
    if (patch._retryLog) {
      n.retryLogs.push(patch._retryLog);
      if (n.retryLogs.length > 10) n.retryLogs.shift();
      delete n._retryLog;
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     INJECT Z34 INSPECTOR INTO THE LIVE TAB DOM
     ═══════════════════════════════════════════════════════════════════ */

  function _injectInspectorPanel() {
    if (document.getElementById('z34InspectorPanel')) return;

    const panel = document.createElement('div');
    panel.id = 'z34InspectorPanel';
    panel.className = 'z34-inspector-panel';
    panel.innerHTML = `
      <div class="z34-inspector-hdr">
        <span class="z34-inspector-label">Forensic Inspector</span>
        <span id="z34InspectorTitle" class="z34-inspector-title"></span>
        <button class="z34-inspector-close" onclick="_z34.closeInspector()" title="Close inspector">×</button>
      </div>
      <div id="z34InspectorBody" class="z34-inspector-body"></div>
    `;

    // Mount inside z30 live wrap if available, else body
    const wrap = document.querySelector('.z30-live-wrap') || document.body;
    wrap.appendChild(panel);
  }

  function _injectContinuityPanel() {
    if (document.getElementById('z34ContinuityPanel')) return;

    const panel = document.createElement('div');
    panel.id = 'z34ContinuityPanel';
    panel.className = 'z34-continuity-panel';
    panel.innerHTML = `<div class="z34-cont-empty">Loading continuity data…</div>`;

    // Append to z31 forensic panel if present, else z30 wrap
    const z31 = document.getElementById('z31ForensicPanel');
    if (z31) {
      const footer = document.createElement('div');
      footer.className = 'z34-cont-footer';
      footer.innerHTML = `<div class="z34-cont-header">
        <span class="z34-cont-label">Execution Continuity</span>
      </div>`;
      footer.appendChild(panel);
      z31.appendChild(footer);
    } else {
      const wrap = document.querySelector('.z30-live-wrap') || document.body;
      wrap.appendChild(panel);
    }
  }

  function _injectDepthIndicator() {
    if (document.getElementById('z34DepthIndicator')) return;

    const el = document.createElement('span');
    el.id = 'z34DepthIndicator';
    el.className = 'z34-depth-indicator z34-layer-runtime';
    el.textContent = 'RUNTIME';
    el.title = 'Current execution depth layer';

    // Place next to z30 replay mode indicator if present
    const replayMode = document.getElementById('z30ReplayMode');
    if (replayMode) {
      replayMode.parentNode.insertBefore(el, replayMode.nextSibling);
    } else {
      const hdr = document.querySelector('.z30-dag-panel-hdr');
      if (hdr) hdr.appendChild(el);
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     REPLAY CURSOR SYNC — tie cursor changes back to DAG + timeline
     ═══════════════════════════════════════════════════════════════════ */

  ReplayCursor.onChange(function (state) {
    // Sync cursor position display
    const cursorEl = document.getElementById('z34ReplayCursorPos');
    if (cursorEl) {
      cursorEl.textContent = state.mode === 'replay'
        ? `Cursor ${state.position + 1}/${state.total}`
        : '';
    }

    // Sync depth layer indicator
    if (state.mode === 'replay') {
      DepthLayer.activateReplay();
    }
  });

  /* ═══════════════════════════════════════════════════════════════════
     NXBUS WIRING
     ═══════════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    /* Session lifecycle */
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z34' });

    NxBus.on('session.done',  () => _onSessionEnd('success'), { owner: 'z34' });
    NxBus.on('session.error', () => _onSessionEnd('error'),   { owner: 'z34' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED  || 'nx:session:created',  (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid);
    }, { owner: 'z34' });
    NxBus.on(EV.SESSION_RESTORED || 'nx:session:restored', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) { S.sid = sid; _loadContinuityData(sid); }
    }, { owner: 'z34' });

    /* DAG node selection → Z34A sync */
    NxBus.on('dag.node.selected', (e) => {
      if (e?.node?.id) _onDagNodeSelected(e.node.id);
    }, { owner: 'z34' });

    /* DAG node completions → index node state */
    NxBus.on('dag.node.done', (e) => {
      if (e?.id) {
        _upsertNode(e.id, { state: 'done', last_ts: Date.now() });
        S.timelineEvents.push({ idx: S.timelineEvents.length, ts: Date.now(), type: 'node-done', nodeId: e.id, data: {} });
        ReplayCursor.setTotal(S.timelineEvents.length);
        _wireTimelineRowsDeferred();
      }
    }, { owner: 'z34' });

    NxBus.on('dag.node.error', (e) => {
      if (e?.id) {
        _upsertNode(e.id, { state: 'error', hadError: true, errorMsg: e.error || '', last_ts: Date.now() });
        S.timelineEvents.push({ idx: S.timelineEvents.length, ts: Date.now(), type: 'node-error', nodeId: e.id, data: { msg: e.error } });
        ReplayCursor.setTotal(S.timelineEvents.length);
        _wireTimelineRowsDeferred();
      }
    }, { owner: 'z34' });

    /* Log rows → depth presence + node indexing */
    NxBus.on('agent.log_row', (e) => {
      if (!e?.text) return;
      const phase = _detectPhase(e.text);
      const severity = _detectSeverity(e.text, e.level);

      if (phase) {
        DepthLayer.onLiveActivity(phase, severity);
        _upsertNode(phase, { last_ts: Date.now(), lastLogBefore: e.text.slice(0, 100) });

        // Extract provider / tokens / confidence from log rows
        const pm = /\b(openai|groq|anthropic|gemini|mistral|deepseek|grok|together)\b/i.exec(e.text);
        if (pm) _upsertNode(phase, { provider: pm[1].toLowerCase() });

        const tm = /(\d+)\s*(?:tokens?|tok)\b/i.exec(e.text);
        if (tm) _upsertNode(phase, { tokens: (S.nodeIndex[phase]?.tokens || 0) + parseInt(tm[1], 10) });

        const cm = /confidence[:\s]+([0-9.]+)/i.exec(e.text);
        if (cm) {
          let c = parseFloat(cm[1]);
          if (c > 1) c /= 100;
          _upsertNode(phase, { confidence: c });
        }
      }

      // Retry tracking
      if (/retry|retrying/i.test(e.text) && phase) {
        _upsertNode(phase, {
          retries: (S.nodeIndex[phase]?.retries || 0) + 1,
          _retryLog: { ts: Date.now(), text: e.text.slice(0, 120) },
        });
        S.timelineEvents.push({ idx: S.timelineEvents.length, ts: Date.now(), type: 'retry', nodeId: phase, data: { n: S.nodeIndex[phase]?.retries } });
        ReplayCursor.setTotal(S.timelineEvents.length);
        _wireTimelineRowsDeferred();
      }
    }, { owner: 'z34' });

    /* Replan events → continuity mapping */
    NxBus.on('dag.replan.triggered', (e) => {
      const nodeId = e?.plan?.nodeId || S.activeNodeId;
      if (nodeId) {
        _upsertNode(nodeId, { replanned: true, replanAction: e?.plan?.action || '' });
      }
      S.timelineEvents.push({ idx: S.timelineEvents.length, ts: Date.now(), type: 'replan', nodeId, data: e?.plan || {} });
      ReplayCursor.setTotal(S.timelineEvents.length);
      _wireTimelineRowsDeferred();
    }, { owner: 'z34' });

    /* Z32 confidence drops → node index + timeline */
    NxBus.on('z32.confidence.update', (e) => {
      const nodeId = e?.nodeId || S.activeNodeId;
      if (nodeId && e?.confidence != null) {
        _upsertNode(nodeId, { confidence: e.confidence });
      }
      if (e?.level === 'LOW') {
        S.timelineEvents.push({ idx: S.timelineEvents.length, ts: Date.now(), type: 'conf-drop', nodeId, data: { pct: e?.pct, level: 'LOW' } });
        ReplayCursor.setTotal(S.timelineEvents.length);
      }
    }, { owner: 'z34' });

    /* Z32 replanning → record outcome when session ends */
    NxBus.on('z32.replan.applied', (e) => {
      const nodeId = e?.nodeId || S.activeNodeId;
      if (nodeId) {
        _recordReplanOutcome(nodeId, e?.trigger || '', true, e?.action || '');
        _upsertNode(nodeId, { recovered: true, recoveryMsg: e?.action || '' });
      }
    }, { owner: 'z34' });

    /* Replay mode entry/exit → depth layer */
    NxBus.on('dag.replay.started', () => {
      ReplayCursor.setMode('replay');
      DepthLayer.activateReplay();
    }, { owner: 'z34' });

    NxBus.on('dag.replay.stopped', () => {
      ReplayCursor.setMode('live');
      DepthLayer.activateLive();
    }, { owner: 'z34' });

    /* Z34 cursor navigation from replay controls */
    NxBus.on('z34.cursor.seek', (e) => {
      if (e?.index != null) _onTimelineEventClick(e.index);
    }, { owner: 'z34' });

    /* Observe timeline render completions to re-wire rows */
    NxBus.on('z33.timeline.rendered', () => {
      setTimeout(_wireTimelineRows, 50);
    }, { owner: 'z34' });
  }

  /* Debounced timeline row wiring (after DOM updates settle) */
  let _wireTimer = null;
  function _wireTimelineRowsDeferred() {
    if (_wireTimer) clearTimeout(_wireTimer);
    _wireTimer = setTimeout(_wireTimelineRows, 300);
  }

  /* ═══════════════════════════════════════════════════════════════════
     SESSION LIFECYCLE
     ═══════════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid) {
    S.sid = sid;
    S.nodeIndex = {};
    S.timelineEvents = [];
    S.recoveryMap = {};
    S.activeNodeId = null;

    ReplayCursor.setSession(sid);
    ReplayCursor.setMode('live');
    ReplayCursor.setTotal(0);
    DepthLayer.activateLive();

    _closeForensicInspector();
    _loadContinuityData(sid);
  }

  function _onSessionEnd(status) {
    DepthLayer.deactivate();
    if (status === 'error') {
      // Mark running nodes as errored for continuity
      for (const [id, node] of Object.entries(S.nodeIndex)) {
        if (node.state === 'running') {
          _upsertNode(id, { state: 'error', hadError: true });
        }
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     PHASE DETECTION (mirrors Z30)
     ═══════════════════════════════════════════════════════════════════ */

  const PHASE_MAP = {
    plan:  [/\b(plan|think|analyz|design|decompos)\b/i],
    code:  [/\b(cod|impl|generat|writ|build|creat)\b/i, /Writing\s+file/i],
    debug: [/\b(debug|test|fix|verif|retry|patch)\b/i, /\[RETRY/i],
    tool:  [/\btool:/i, /calling\s+tool/i, /\[TOOL\]/i],
    done:  [/Task finished/i, /status=success/i, /✅/],
  };

  function _detectPhase(text) {
    for (const [phase, patterns] of Object.entries(PHASE_MAP)) {
      if (patterns.some(re => re.test(text))) return phase;
    }
    return null;
  }

  function _detectSeverity(text, level) {
    if (level === 'error' || /error|failed|traceback/i.test(text)) return 'CRITICAL';
    if (/retry|fallback|timeout/i.test(text)) return 'WARNING';
    if (/warn|slow|pressure/i.test(text)) return 'DEGRADED';
    return 'INFO';
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

  function _fmtAge(s) {
    if (!s) return '—';
    if (s < 60)    return `${s}s ago`;
    if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  /* ═══════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════ */

  window._z34 = {
    cursor:           ReplayCursor,
    depth:            DepthLayer,
    openInspector:    _openForensicInspector,
    closeInspector:   _closeForensicInspector,
    onTimelineClick:  _onTimelineEventClick,
    onDagNodeSelected: _onDagNodeSelected,
    getNodeData:      (id) => S.nodeIndex[id] || null,
    getTimelineEvents: () => S.timelineEvents.slice(),
    getContinuityThreads: () => S.sessionThreads,
    getFailureLineage:    () => S.failureLineage,
  };

  /* ═══════════════════════════════════════════════════════════════════
     INIT
     ═══════════════════════════════════════════════════════════════════ */

  function _init() {
    _wireNxBus();
    _injectInspectorPanel();
    _injectContinuityPanel();
    _injectDepthIndicator();

    // Wire existing DAG node click → Z34 inspector
    // (augments Z30's existing dag.node.selected handler)
    if (window.NxBus) {
      NxBus.on('dag.node.selected', (e) => {
        if (e?.node) _openForensicInspector(e.node.id, null);
      }, { owner: 'z34-dag' });
    }

    // Initial timeline row wiring (defer to allow Z33 to render)
    setTimeout(_wireTimelineRows, 1000);

    S.initialized = true;
    console.debug('[Phase Z34] Forensic Replay Fusion + Deep Execution Presence active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 800));
  } else {
    setTimeout(_init, 800);
  }
})();
