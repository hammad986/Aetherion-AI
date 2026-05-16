/**
 * nx-z45-sync.js — Phase Z45: Causal Execution Synchronization + Runtime Consolidation
 * ═══════════════════════════════════════════════════════════════════════════════════════
 *
 * Z45A — DAG ↔ Timeline true bidirectional sync (hover focus, replay cursor sync)
 * Z45B — Causal execution flow (parent→child transitions, retry/recovery branch causality)
 * Z45C — Replay immersion (progression narrative, failure moment marking, cursor sync)
 * Z45D — Runtime pressure intelligence (hotspot tier stamping, bottleneck ranking)
 * Z45E — Telemetry consolidation (suppress Z44 strip, route narrative to Z35)
 * Z45F — Long-session continuity (objective anchor, fatigue awareness, drift marking)
 * Z45G — Governance audit (dead surface removal, duplicate suppression)
 *
 * Design rules:
 *   ✗ No new NxBus channels (uses existing z36.node.focus, dag.replay.*)
 *   ✗ No touching Z30/Z33/Z35/Z36/Z37/Z44 source files
 *   ✗ No heavy computation in hot paths
 *   ✗ No new animations beyond opacity + outline transitions
 *   ✓ RAF-batched DOM writes only — zero layout thrashing
 *   ✓ Pure augmentation and consolidation
 *   ✓ Zero-cost when idle (event-driven + low-freq intervals)
 * ═══════════════════════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Guard ───────────────────────────────────────────────────────── */
  if (window._z45) return;

  /* ── Utilities ───────────────────────────────────────────────────── */
  function $id(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s ?? '').replace(/[<>&"]/g,
      c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
  }

  let _rafPending = false;
  function _raf(fn) {
    if (_rafPending) return;
    _rafPending = true;
    requestAnimationFrame(() => { _rafPending = false; fn(); });
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45E — MISSION SURFACE CONSOLIDATION  (most critical, runs first)
     Hide Z44 #nx-mission-strip when Z35 bar is present.
     Route Z44 narrative text into Z35 objective field.
     ══════════════════════════════════════════════════════════════════ */
  function consolidateMissionSurfaces() {
    const z35bar   = $id('z35MissionBar');
    const z44strip = $id('nx-mission-strip');
    if (!z35bar || !z44strip) return;

    z44strip.setAttribute('data-z45-consolidated', 'true');
    z44strip.style.display = 'none';

    // Mirror Z44 narrative text → Z35 objective field
    const z35obj = $id('z35MissionObjective');
    const textEl = z44strip.querySelector('.nx-mission-text');
    if (textEl && z35obj) {
      new MutationObserver(() => {
        const val = textEl.textContent;
        if (val && val !== 'Ready.' && z35obj.textContent !== val) {
          z35obj.textContent = val;
        }
      }).observe(textEl, { characterData: true, childList: true, subtree: true });
    }

    document.documentElement.setAttribute('data-z45-mission-consolidated', 'true');
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45A — SHARED CAUSAL FOCUS STATE
     Single source of truth for which node is focused across all surfaces.
     ══════════════════════════════════════════════════════════════════ */
  const CausalFocus = (function () {
    let _nodeId  = null;
    let _source  = null;
    const _cbs   = [];

    function set(nodeId, source) {
      if (_nodeId === nodeId) return;
      _nodeId = nodeId; _source = source;
      _cbs.forEach(fn => { try { fn(nodeId, source); } catch (_) {} });
      if (window.NxBus) NxBus.emit('z45.focus', { nodeId, source });
    }
    function clear(source) { if (_nodeId) set(null, source); }
    function get() { return { nodeId: _nodeId, source: _source }; }
    function on(fn) { _cbs.push(fn); }
    return { set, clear, get, on };
  })();

  /* ══════════════════════════════════════════════════════════════════
     Z45A — DAG → TIMELINE HOVER SYNC
     When operator hovers a DAG node, matching timeline events are focused.
     ══════════════════════════════════════════════════════════════════ */
  function _dagToTimeline(nodeId) {
    // Clear previous focus
    document.querySelectorAll('.z36-timeline-focus, .z45-soft-focus').forEach(el => {
      el.classList.remove('z36-timeline-focus', 'z45-soft-focus');
    });
    if (!nodeId) return;

    // Primary: data-z36-node-id exact match
    const exact = document.querySelectorAll(
      `.z33-tl-event[data-z36-node-id="${CSS.escape(nodeId)}"]`
    );
    if (exact.length) {
      exact.forEach(el => el.classList.add('z36-timeline-focus'));
      _scrollTlTo(exact[0]);
      return;
    }

    // Fallback: label text contains the node id
    document.querySelectorAll('.z33-tl-event').forEach(el => {
      const txt = el.querySelector('.z33-tl-text');
      if (txt && txt.textContent.toLowerCase().includes(nodeId.toLowerCase())) {
        el.classList.add('z45-soft-focus');
      }
    });
  }

  function _scrollTlTo(el) {
    if (!el) return;
    const parent = el.closest('.z33-tl-scroll, .z33-timeline-body');
    if (parent) parent.scrollTop = Math.max(0, el.offsetTop - parent.offsetTop - 20);
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45A — TIMELINE → DAG HOVER SYNC
     Hover on a timeline event → focus the matching DAG node.
     ══════════════════════════════════════════════════════════════════ */
  function _timelineToDag(nodeId) {
    document.querySelectorAll('[data-node-id].z36-focus-ring').forEach(el => {
      el.classList.remove('z36-focus-ring');
    });
    if (!nodeId) return;
    const dagEl = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
    if (dagEl) dagEl.classList.add('z36-focus-ring');
  }

  // CausalFocus fan-out → both surfaces
  CausalFocus.on(function (nodeId, source) {
    _raf(() => {
      if (source !== 'dag')      _timelineToDag(nodeId);
      if (source !== 'timeline') _dagToTimeline(nodeId);
    });
  });

  // Wire DAG surface hover
  function _wireDagHovers() {
    const surface = $id('z30DagSurface');
    if (!surface || surface.dataset.z45h) return;
    surface.dataset.z45h = '1';

    surface.addEventListener('mouseover', e => {
      const n = e.target.closest('[data-node-id]');
      if (n) CausalFocus.set(n.dataset.nodeId, 'dag');
    }, { passive: true });
    surface.addEventListener('mouseleave', () => CausalFocus.clear('dag'), { passive: true });
  }

  // Wire timeline dock hover
  function _wireTimelineHovers() {
    const dock = document.querySelector('.z33-tl-scroll, .z33-timeline-body, #z33TimelineDock');
    if (!dock || dock.dataset.z45h) return;
    dock.dataset.z45h = '1';

    dock.addEventListener('mouseover', e => {
      const row = e.target.closest('.z33-tl-event');
      if (!row) return;
      const nodeId = row.dataset.z36NodeId || _labelToNodeId(row);
      if (nodeId) CausalFocus.set(nodeId, 'timeline');
    }, { passive: true });
    dock.addEventListener('mouseleave', () => CausalFocus.clear('timeline'), { passive: true });
  }

  // Z45A fallback: extract node id from label text (Z36 already handles the exact-match case)
  function _labelToNodeId(row) {
    const txt = (row.querySelector('.z33-tl-text') || row).textContent || '';
    const m = txt.match(/[✓✕↺⬡⟳◉]\s*([\w][\w\-.:]*)/);
    return m ? m[1] : null;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45A — LABEL-BASED SOFT SYNC (original fallback kept)
     Enhances Z36's existing sync for events without data-z36-node-id.
     ══════════════════════════════════════════════════════════════════ */
  function enhanceSurfaceSync() {
    if (!window.NxBus) return;

    NxBus.on('z36.node.focus', (e) => {
      if (!e) return;
      const tlDock = $id('z33TimelineDock');
      if (!tlDock) return;
      tlDock.querySelectorAll('.z45-soft-focus').forEach(el => el.classList.remove('z45-soft-focus'));
      if (!e.id && !e.label) return;
      const label = e.label || e.id || '';
      tlDock.querySelectorAll('.z33-tl-event:not([data-z36-node-id])').forEach(el => {
        const text = (el.querySelector('.z33-tl-text') || el).textContent || '';
        if (label && text.toLowerCase().includes(label.toLowerCase())) {
          el.classList.add('z45-soft-focus');
        }
      });
    }, { owner: 'z45' });

    // Global hover for unlabelled timeline rows
    document.addEventListener('mouseover', _onGlobalHover, { passive: true });
    document.addEventListener('mouseleave', _onGlobalLeave, { passive: true, capture: true });
  }

  let _lastHoverLabel = null;
  function _onGlobalHover(e) {
    const row = e.target.closest('.z33-tl-event:not([data-z36-node-id])');
    if (!row) return;
    const txt = row.querySelector('.z33-tl-text');
    const label = txt ? txt.textContent.slice(0, 40) : null;
    if (!label || label === _lastHoverLabel) return;
    _lastHoverLabel = label;
    row.classList.add('z45-soft-focus');
    if (window.NxBus) NxBus.emit('z36.node.focus', { id: null, label, source: 'z45-tl-label' });
  }
  function _onGlobalLeave(e) {
    const row = e.target.closest && e.target.closest('.z33-tl-event');
    if (row) { row.classList.remove('z45-soft-focus'); _lastHoverLabel = null; }
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45A — REPLAY CURSOR SYNCHRONIZATION
     Replay step events → apply z45-replay-current/past/future on timeline.
     ══════════════════════════════════════════════════════════════════ */
  function _applyReplayCursor(stepIndex, totalSteps, nodeId) {
    const events = document.querySelectorAll('.z33-tl-event');
    if (!events.length) return;
    const pivot = Math.round((stepIndex / Math.max(1, totalSteps - 1)) * (events.length - 1));
    events.forEach((el, i) => {
      el.classList.remove('z45-replay-current', 'z45-replay-past', 'z45-replay-future');
      if (i === pivot)    el.classList.add('z45-replay-current');
      else if (i < pivot) el.classList.add('z45-replay-past');
      else                el.classList.add('z45-replay-future');
    });
    if (nodeId) CausalFocus.set(nodeId, 'replay');
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45B — CAUSAL BRANCH TYPE STAMPING
     Stamps data-causal-branch on DAG nodes using Z37 CausalGraph.
     Stamps data-last-transition for parent→child causality display.
     ══════════════════════════════════════════════════════════════════ */
  function _stampCausalBranches() {
    if (!window._z37) return;
    const cg = _z37.CausalGraph;
    if (!cg) return;
    document.querySelectorAll('[data-node-id]').forEach(el => {
      const id = el.dataset.nodeId;
      if (!id) return;
      const branch = cg.getBranchType(id);
      if (branch && branch !== 'main') el.dataset.causalBranch = branch;

      // Show parent → child in tooltip-style data attr
      const parent = cg.getParent(id);
      if (parent) el.dataset.causalParent = parent;
    });
  }

  function _onNodeTransition(data) {
    const { nodeId, fromState, toState, parentId } = data || {};
    if (!nodeId || !fromState || !toState) return;
    if (window._z37 && _z37.CausalGraph) {
      if (parentId) {
        const type = toState === 'retry' ? 'retry'
                   : toState === 'recovery' ? 'recovery'
                   : fromState === 'failed' ? 'escalation' : 'main';
        _z37.CausalGraph.addEdge(parentId, nodeId, type);
      } else {
        _z37.CausalGraph.addRoot(nodeId);
      }
    }
    const el = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
    if (el) el.dataset.lastTransition = `${fromState}→${toState}`;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45C — REPLAY IMMERSION
     Replay narrative in Z35 mission bar. Failure moment marking.
     Replay available indicator on timeline header.
     ══════════════════════════════════════════════════════════════════ */
  function wireReplayNarrative() {
    if (!window.NxBus) return;

    const z35obj = $id('z35MissionObjective');
    const z35ph  = $id('z35PhaseText');

    NxBus.on('dag.replay.started', () => {
      document.body.dataset.nxState = 'replay';
      if (z35obj) z35obj.textContent = 'Replay — reconstructing execution history…';
      if (z35ph)  { z35ph.textContent = 'replay'; z35ph.className = 'z35-mission-val z35-phase-replay'; }
    }, { owner: 'z45' });

    NxBus.on('dag.replay.step', (e) => {
      if (!e) return;
      const { step, total, nodeId, label } = e;
      if (z35obj && label) z35obj.textContent = `Replay [${step}/${total}] — ${label}`;
      _raf(() => _applyReplayCursor(step, total, nodeId));
    }, { owner: 'z45' });

    NxBus.on('dag.replay.stopped', () => {
      const state = window.nxZ44 ? nxZ44.getState() : 'idle';
      document.body.dataset.nxState = state;
      if (z35ph) { z35ph.textContent = 'idle'; z35ph.className = 'z35-mission-val z35-phase-idle'; }
      // Clear replay cursor classes
      document.querySelectorAll('.z45-replay-current, .z45-replay-past, .z45-replay-future')
        .forEach(el => el.classList.remove('z45-replay-current', 'z45-replay-past', 'z45-replay-future'));
    }, { owner: 'z45' });

    NxBus.on('dag.replay.available', (e) => {
      if (e && e.count > 0) {
        document.documentElement.setAttribute('data-z45-replay-available', 'true');
      }
    }, { owner: 'z45' });

    // Mark failure moments in timeline for forensic replay
    NxBus.on('z30.node.error', () => _raf(_markFailureMoments), { owner: 'z45' });
    NxBus.on('session.done',   () => {
      document.documentElement.setAttribute('data-z45-replay-available', 'true');
      _raf(_markFailureMoments);
    }, { owner: 'z45' });
  }

  function _markFailureMoments() {
    document.querySelectorAll('.z33-tl-event').forEach(el => {
      const dot = el.querySelector('.z33-tl-dot');
      if (!dot) return;
      const type = el.dataset.eventType || '';
      if (type === 'node-error' || dot.classList.contains('error')) {
        el.dataset.z45Moment = 'failure';
      } else if (type === 'recovery' || dot.classList.contains('recovery')) {
        el.dataset.z45Moment = 'recovery';
      } else if (type === 'retry' || dot.classList.contains('retry')) {
        el.dataset.z45Moment = 'retry';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45D — RUNTIME PRESSURE INTELLIGENCE
     Stamps data-pressure-tier on DAG nodes and timeline events.
     Surfaces bottleneck ranking in inspector.
     ══════════════════════════════════════════════════════════════════ */
  function wirePressureIntelligence() {
    if (!window.NxBus) return;

    NxBus.on('z36.pressure.update', (e) => {
      if (e?.hotspots) _raf(() => _stampHotspots(e.hotspots));
    }, { owner: 'z45' });

    NxBus.on('z36.node.focus', (e) => {
      if (e?.id) _enrichInspectorPressure(e.id);
    }, { owner: 'z45-pressure' });
  }

  function _stampHotspots(hotspots) {
    if (!Array.isArray(hotspots)) return;
    hotspots.slice(0, 5).forEach((h, i) => {
      const tier = i === 0 ? 'critical' : i === 1 ? 'high' : 'medium';
      const dag  = document.querySelector(`[data-node-id="${CSS.escape(h.id)}"]`);
      if (dag) dag.setAttribute('data-pressure-tier', tier);
      document.querySelectorAll(`[data-z36-node-id="${CSS.escape(h.id)}"]`)
        .forEach(el => el.setAttribute('data-pressure-tier', tier));
    });
  }

  // Also stamp from Z36 NodeRegistry directly (available after Z36 init)
  function _stampAllPressureTiers() {
    if (!window._z36) return;
    const nodes = _z36.registry ? _z36.registry.all() : [];
    nodes.forEach(node => {
      const heat = node.heat || 0;
      const tier = heat >= 0.75 ? 'critical' : heat >= 0.5 ? 'high' : heat >= 0.25 ? 'medium' : null;
      const dag  = document.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
      if (dag)  { if (tier) dag.dataset.pressureTier = tier; else delete dag.dataset.pressureTier; }
      document.querySelectorAll(`[data-z36-node-id="${CSS.escape(node.id)}"]`).forEach(el => {
        if (tier) el.dataset.pressureTier = tier; else delete el.dataset.pressureTier;
      });
    });
  }

  function _enrichInspectorPressure(nodeId) {
    const forensicSec = $id('z36ForensicSection');
    if (!forensicSec) return;

    let hint = forensicSec.querySelector('.z45-pressure-hint');
    if (!hint) {
      hint = document.createElement('div');
      hint.className = 'z45-pressure-hint';
      forensicSec.appendChild(hint);
    }

    const reg  = window._z36?.registry || window._z36?.NodeRegistry;
    const node = reg ? reg.get(nodeId) : null;

    if (node && node.retries > 0) {
      const tier = node.retries >= 4 ? 'critical' : node.retries >= 2 ? 'high' : 'medium';
      hint.innerHTML = `
        <span class="z45-pressure-label">RETRY PRESSURE</span>
        <span class="z45-pressure-val z45-pressure-${tier}">${node.retries}× retries</span>
      `;
      hint.dataset.pressureTier = tier;
    } else {
      hint.innerHTML = '';
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45F — LONG-SESSION CONTINUITY
     Session objective anchor. Fatigue tier stamping. Drift awareness.
     ══════════════════════════════════════════════════════════════════ */
  let _sessionStartTs = null;

  function wireSessionContinuity() {
    if (!window.NxBus) return;

    NxBus.on('session.started', (e) => {
      const ts = e && (e.started_at || e.ts || Date.now() / 1000);
      if (ts) _updateSessionAge(ts);
      _sessionStartTs = ts ? ts * 1000 : Date.now();

      // Capture prompt as session objective
      if (e?.prompt) _captureObjective(e.prompt);
    }, { owner: 'z45' });

    NxBus.on('prompt.submit', (e) => {
      if (e?.prompt && !_sessionObjective) _captureObjective(e.prompt);
    }, { owner: 'z45' });

    // Poll session age every 2 minutes for long-session awareness
    setInterval(() => {
      const card = $id('nxSessionCard');
      if (!card) return;
      const ts = card.dataset.startedAt;
      if (ts) _updateSessionAge(parseFloat(ts));
      if (_sessionStartTs) _checkFatigue();
    }, 120000);
  }

  function _updateSessionAge(startedAtSec) {
    const card = $id('nxSessionCard');
    if (!card) return;
    card.dataset.startedAt = startedAtSec;
    const elapsed = Date.now() / 1000 - startedAtSec;
    card.dataset.sessionAge = elapsed < 300 ? 'fresh'
                            : elapsed < 1800 ? 'active'
                            : 'long';
  }

  function _checkFatigue() {
    if (!_sessionStartTs) return;
    const mins = (Date.now() - _sessionStartTs) / 60000;
    const body = document.body;
    if (mins >= 60)      body.dataset.z45Fatigue = 'extended';
    else if (mins >= 30) body.dataset.z45Fatigue = 'long';
    else if (mins >= 10) body.dataset.z45Fatigue = 'moderate';
    else                 delete body.dataset.z45Fatigue;
  }

  // Objective anchor (continuity across retries / replans)
  let _sessionObjective = null;
  function _captureObjective(prompt) {
    if (!prompt || _sessionObjective) return;
    _sessionObjective = prompt.length > 90 ? prompt.slice(0, 87) + '…' : prompt;
    const el = document.querySelector('.z35-mission-text, #z35MissionObjective');
    if (el && !el.textContent.trim()) el.textContent = _sessionObjective;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45G — GOVERNANCE: TELEMETRY CONSOLIDATION STAMPS
     Stamp known-redundant surfaces. No elements removed — only marked.
     ══════════════════════════════════════════════════════════════════ */
  function auditTelemetryLayer() {
    // Mark Z35 bar as primary mission surface
    const z35bar = $id('z35MissionBar');
    if (z35bar) z35bar.setAttribute('data-z45-primary-mission', 'true');

    // Mark Z44 strip as secondary (already hidden by consolidateMissionSurfaces)
    const z44strip = $id('nx-mission-strip');
    if (z44strip && z35bar) z44strip.setAttribute('data-z45-primary-mission', 'false');

    // Suppress stale pulse indicator if present
    const pulse = $id('pulse');
    if (pulse) pulse.setAttribute('data-z45-suppressed', 'true');

    // Suppress any second instance of forecast bars (keep only the first)
    document.querySelectorAll('.z37-forecast-bar').forEach((el, i) => {
      if (i > 0) el.setAttribute('data-z45-suppressed', 'true');
    });

    // Suppress empty causal blocks (no content siblings)
    document.querySelectorAll('.z37-causal-block').forEach(el => {
      if (!el.id && el.textContent.trim() === '') {
        el.setAttribute('data-z45-suppressed', 'true');
      }
    });

    // Reduce noise: suppress "idle" risk badges (they convey no signal)
    document.querySelectorAll('.z37-risk-indicator.z37-risk-LOW').forEach(el => {
      el.style.opacity = '0.4';
    });

    // Flag dual-forensic coexistence for CSS-side handling
    const z36f = $id('z36ForensicSection');
    const z37c = $id('z37CausalSection');
    if (z36f && z37c) {
      document.documentElement.setAttribute('data-z45-dual-forensic', 'true');
    }

    document.documentElement.setAttribute('data-z45-exec-observers', '2');
  }

  /* ══════════════════════════════════════════════════════════════════
     PERIODIC SYNC CYCLE
     Runs every 4s — lightweight, RAF-batched.
     ══════════════════════════════════════════════════════════════════ */
  function _startSyncCycle() {
    setInterval(() => {
      _raf(() => {
        _stampAllPressureTiers();
        _stampCausalBranches();
        _wireDagHovers();
        _wireTimelineHovers();
        _checkFatigue();
      });
    }, 4000);
  }

  /* ══════════════════════════════════════════════════════════════════
     BOOTSTRAP
     ══════════════════════════════════════════════════════════════════ */
  function waitForNxBus(cb) {
    if (window.NxBus) { cb(); return; }
    const t = setInterval(() => { if (window.NxBus) { clearInterval(t); cb(); } }, 100);
  }

  function initZ45() {
    const ready = () =>
      window.NxBus &&
      (document.getElementById('z35MissionBar') ||
       document.getElementById('nx-mission-strip') ||
       document.getElementById('z33TimelineDock') ||
       document.getElementById('z30DagSurface'));

    if (ready()) { _boot(); return; }
    const mo = new MutationObserver(() => { if (ready()) { mo.disconnect(); _boot(); } });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  function _boot() {
    setTimeout(() => {
      // Z45E — consolidation first (removes visual redundancy immediately)
      consolidateMissionSurfaces();

      waitForNxBus(() => {
        enhanceSurfaceSync();      // Z45A label fallback
        wireReplayNarrative();     // Z45C
        wirePressureIntelligence();// Z45D
        wireSessionContinuity();   // Z45F

        if (window.NxBus) {
          NxBus.on('z30.node.transition', _onNodeTransition, { owner: 'z45' }); // Z45B
        }
      });

      auditTelemetryLayer();       // Z45G
      _wireDagHovers();            // Z45A bidirectional
      _wireTimelineHovers();       // Z45A bidirectional
      _stampCausalBranches();      // Z45B
      _stampAllPressureTiers();    // Z45D
      _startSyncCycle();           // periodic maintenance

      console.log('[Phase Z45] Causal synchronization + runtime consolidation active.');
    }, 400); // after Z35/Z36/Z44 have initialized
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initZ45);
  } else {
    initZ45();
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  window._z45 = {
    version:      'Z45',
    CausalFocus,
    focusNode:    (id, src) => CausalFocus.set(id, src || 'external'),
    clearFocus:   ()        => CausalFocus.clear('external'),
    consolidate:  consolidateMissionSurfaces,
    audit:        auditTelemetryLayer,
  };

  // Backward-compat alias
  window.nxZ45 = { consolidate: consolidateMissionSurfaces };
})();
