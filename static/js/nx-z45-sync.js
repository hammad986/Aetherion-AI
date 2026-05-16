/**
 * nx-z45-sync.js — Phase Z45: Causal Execution Synchronization + Runtime Consolidation
 * ═══════════════════════════════════════════════════════════════════════════════════════
 * Responsibilities:
 *
 *   Z45A — Surface sync: enhance existing Z36 hover sync with label-based fallback
 *           when data-z36-node-id attributes are not yet stamped on timeline events.
 *
 *   Z45C — Replay immersion: listen for dag.replay events, update Z35 mission bar
 *           with replay progression narrative.
 *
 *   Z45D — Pressure intelligence: surface Z36 PressureMemory hotspots in inspector
 *           via NxBus z36.node.focus events.
 *
 *   Z45E — Mission consolidation (most critical):
 *           Hide Z44's #nx-mission-strip when Z35's #z35MissionBar is present.
 *           Route Z44 log-line narrative INTO Z35's mission objective field.
 *           Eliminate the duplicate mission surface.
 *
 *   Z45F — Long-session continuity: stamp session age onto session card
 *           using actual started_at timestamp when available.
 *
 *   Z45G — Governance: stamp telemetry consolidation markers, suppress
 *           known redundant indicators via data attributes.
 *
 * Design constraints:
 *   ✗ No new NxBus event channels (use existing z36.node.focus, dag.replay.*)
 *   ✗ No touching Z30, Z33, Z35, Z36 source files
 *   ✗ No heavy computation
 *   ✓ Pure augmentation and consolidation
 *   ✓ Zero-cost when idle (event-driven only)
 * ═══════════════════════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Utility ─────────────────────────────────────────────────────── */
  function $id(id) { return document.getElementById(id); }

  /* ══════════════════════════════════════════════════════════════════
     Z45E — MISSION SURFACE CONSOLIDATION
     Suppress Z44 strip when Z35 mission bar is present.
     Route Z44 narrative text → Z35 objective field.
     ══════════════════════════════════════════════════════════════════ */
  function consolidateMissionSurfaces() {
    const z35bar   = $id('z35MissionBar');
    const z44strip = $id('nx-mission-strip');

    if (!z35bar || !z44strip) return;

    // Z35 bar is the canonical mission surface — hide Z44 strip
    z44strip.setAttribute('data-z45-consolidated', 'true');
    z44strip.style.display = 'none';

    // Patch: redirect mission-text updates to Z35 objective
    const z35obj = $id('z35MissionObjective');
    if (!z35obj) return;

    // Watch for Z44 mission-text content changes, mirror into Z35
    const textEl = z44strip.querySelector('.nx-mission-text');
    if (textEl) {
      const mo = new MutationObserver(() => {
        const val = textEl.textContent;
        if (val && val !== 'Ready.' && z35obj.textContent !== val) {
          z35obj.textContent = val;
        }
      });
      mo.observe(textEl, { characterData: true, childList: true, subtree: true });
    }

    document.documentElement.setAttribute('data-z45-mission-consolidated', 'true');
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45A — SURFACE SYNC ENHANCEMENT
     Label-based fallback when data-z36-node-id is absent on timeline events.
     Z36 already handles sync when data-z36-node-id is present.
     This adds a softer label-match layer for timeline events lacking IDs.
     ══════════════════════════════════════════════════════════════════ */
  function enhanceSurfaceSync() {
    if (!window.NxBus) return;

    // Listen for z36.node.focus — add label-match fallback for timeline events
    NxBus.on('z36.node.focus', (e) => {
      if (!e) return;

      // Already handled by Z36 for elements WITH data-z36-node-id
      // Add label-based soft-focus for elements WITHOUT id (fallback)
      const tlDock = $id('z33TimelineDock');
      if (!tlDock) return;

      // Clear previous soft-focus
      tlDock.querySelectorAll('.z45-soft-focus').forEach(el => {
        el.classList.remove('z45-soft-focus');
      });

      if (!e.id && !e.label) return;

      // Find events without data-z36-node-id that match by label substring
      const label = e.label || e.id || '';
      tlDock.querySelectorAll('.z33-tl-event:not([data-z36-node-id])').forEach(el => {
        const text = (el.querySelector('.z33-tl-text') || el).textContent || '';
        if (label && text.toLowerCase().includes(label.toLowerCase())) {
          el.classList.add('z45-soft-focus');
        }
      });
    }, { owner: 'z45' });

    // Timeline events: add soft hover → emit z36.node.focus by label
    document.addEventListener('mouseover', _onGlobalHover, { passive: true });
    document.addEventListener('mouseleave', _onGlobalLeave, { passive: true, capture: true });
  }

  let _lastHoveredLabel = null;

  function _onGlobalHover(e) {
    // Only handle timeline events that lack data-z36-node-id (Z36 handles the ones with IDs)
    const row = e.target.closest('.z33-tl-event:not([data-z36-node-id])');
    if (!row) return;

    const textEl = row.querySelector('.z33-tl-text');
    const label = textEl ? textEl.textContent.slice(0, 40) : null;
    if (!label || label === _lastHoveredLabel) return;

    _lastHoveredLabel = label;
    row.classList.add('z45-soft-focus');

    if (window.NxBus) {
      NxBus.emit('z36.node.focus', { id: null, label, source: 'z45-timeline-label' });
    }
  }

  function _onGlobalLeave(e) {
    const row = e.target.closest && e.target.closest('.z33-tl-event');
    if (!row) return;
    row.classList.remove('z45-soft-focus');
    _lastHoveredLabel = null;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45C — REPLAY IMMERSION
     Listen for replay events, update Z35 mission bar with replay context.
     ══════════════════════════════════════════════════════════════════ */
  function wireReplayNarrative() {
    if (!window.NxBus) return;

    const z35obj  = $id('z35MissionObjective');
    const z35ph   = $id('z35PhaseText');

    NxBus.on('dag.replay.started', () => {
      document.body.dataset.nxState = 'replay';
      if (z35obj) z35obj.textContent = 'Replay — reconstructing execution history…';
      if (z35ph)  { z35ph.textContent = 'replay'; z35ph.className = 'z35-mission-val z35-phase-replay'; }
    }, { owner: 'z45' });

    NxBus.on('dag.replay.stopped', () => {
      document.body.dataset.nxState = window.nxZ44 ? window.nxZ44.getState() : 'idle';
      if (z35ph) { z35ph.textContent = 'idle'; z35ph.className = 'z35-mission-val z35-phase-idle'; }
    }, { owner: 'z45' });

    // Replay available — show forensic marker
    NxBus.on('dag.replay.available', (e) => {
      const count = e && e.count ? e.count : 0;
      if (count > 0) {
        document.documentElement.setAttribute('data-z45-replay-available', 'true');
      }
    }, { owner: 'z45' });
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45D — PRESSURE INTELLIGENCE SURFACING
     Annotate timeline and DAG elements with pressure tier.
     Z36 PressureMemory tracks hotspots — surface them in CSS.
     ══════════════════════════════════════════════════════════════════ */
  function wirePressureIntelligence() {
    if (!window.NxBus) return;

    NxBus.on('z36.pressure.update', (e) => {
      if (!e || !e.hotspots) return;
      _stampPressureTiers(e.hotspots);
    }, { owner: 'z45' });

    NxBus.on('z36.node.focus', (e) => {
      if (!e || !e.id) return;
      _enrichInspectorWithPressure(e.id);
    }, { owner: 'z45-pressure' });
  }

  function _stampPressureTiers(hotspots) {
    if (!Array.isArray(hotspots)) return;
    // Stamp top-3 hotspots with data-pressure-tier on their DAG/timeline elements
    hotspots.slice(0, 5).forEach((h, idx) => {
      const tier = idx === 0 ? 'critical' : idx <= 1 ? 'high' : 'medium';
      // DAG nodes
      const dagEl = document.querySelector(`[data-node-id="${CSS.escape(h.id)}"]`);
      if (dagEl) dagEl.setAttribute('data-pressure-tier', tier);
      // Timeline events
      document.querySelectorAll(`[data-z36-node-id="${CSS.escape(h.id)}"]`).forEach(el => {
        el.setAttribute('data-pressure-tier', tier);
      });
    });
  }

  function _enrichInspectorWithPressure(nodeId) {
    // Find or create pressure hint in the forensic section
    const forensicSec = $id('z36ForensicSection');
    if (!forensicSec) return;

    // Already has pressure indicator? Just update
    let pressHint = forensicSec.querySelector('.z45-pressure-hint');
    if (!pressHint) {
      pressHint = document.createElement('div');
      pressHint.className = 'z45-pressure-hint';
      forensicSec.appendChild(pressHint);
    }

    // Read from Z36's NodeRegistry if accessible
    if (window._z36 && window._z36.NodeRegistry) {
      const node = window._z36.NodeRegistry.get(nodeId);
      if (node && node.retries > 0) {
        const tier = node.retries >= 4 ? 'critical' : node.retries >= 2 ? 'high' : 'medium';
        pressHint.innerHTML = `
          <span class="z45-pressure-label">RETRY PRESSURE</span>
          <span class="z45-pressure-val z45-pressure-${tier}">${node.retries}× retries</span>
        `;
        pressHint.setAttribute('data-pressure-tier', tier);
      } else {
        pressHint.innerHTML = '';
      }
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45F — LONG-SESSION CONTINUITY
     Wire actual started_at into session age system.
     ══════════════════════════════════════════════════════════════════ */
  function wireSessionContinuity() {
    if (!window.NxBus) return;

    NxBus.on('session.started', (e) => {
      const ts = e && (e.started_at || e.ts || Date.now() / 1000);
      if (ts) _updateSessionAgeFromTs(ts);
    }, { owner: 'z45' });

    // Poll session age every 2 minutes for long sessions
    setInterval(() => {
      const card = $id('nxSessionCard');
      if (!card) return;
      const ts = card.dataset.startedAt;
      if (ts) _updateSessionAgeFromTs(parseFloat(ts));
    }, 120000);
  }

  function _updateSessionAgeFromTs(startedAt) {
    const card = $id('nxSessionCard');
    if (!card) return;
    if (startedAt) card.dataset.startedAt = startedAt;
    const elapsed = (Date.now() / 1000) - startedAt;
    const age = elapsed < 300 ? 'fresh'     // < 5 min
              : elapsed < 1800 ? 'active'   // < 30 min
              : 'long';
    card.dataset.sessionAge = age;
  }

  /* ══════════════════════════════════════════════════════════════════
     Z45G — GOVERNANCE: TELEMETRY CONSOLIDATION STAMPS
     Mark known-redundant elements so CSS can suppress or style them.
     No elements are removed — only stamped for potential suppression.
     ══════════════════════════════════════════════════════════════════ */
  function auditTelemetryLayer() {
    // Z43 exec-state script: sets body[data-nx-exec] — Z44 also sets it.
    // No conflict (same value), just a minor redundancy. Document via attribute.
    document.documentElement.setAttribute('data-z45-exec-observers', '2');

    // Identify and stamp the Z35 mission bar as the authoritative mission surface
    const z35bar = $id('z35MissionBar');
    if (z35bar) z35bar.setAttribute('data-z45-primary-mission', 'true');

    // Stamp the Z44 strip as secondary (hidden by consolidateMissionSurfaces)
    const z44strip = $id('nx-mission-strip');
    if (z44strip && !$id('z35MissionBar')) {
      z44strip.setAttribute('data-z45-primary-mission', 'true');
    }

    // Flag known duplicate telemetry: #pulse is hidden by Z44G, verify
    const pulse = $id('pulse');
    if (pulse) pulse.setAttribute('data-z45-suppressed', 'true');

    // Audit Z34 inspector body — flag if forensic + Z36 forensic coexist
    const z34body  = $id('z34InspectorBody');
    const z36foren = $id('z36ForensicSection');
    const z37caus  = $id('z37CausalSection');
    if (z34body && z36foren && z37caus) {
      document.documentElement.setAttribute('data-z45-dual-forensic', 'true');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     BOOTSTRAP
     ══════════════════════════════════════════════════════════════════ */
  function waitForNxBus(cb) {
    if (window.NxBus) { cb(); return; }
    const t = setInterval(() => { if (window.NxBus) { clearInterval(t); cb(); } }, 100);
  }

  function initZ45() {
    // Wait for both NxBus and key DOM elements to settle
    const ready = () =>
      window.NxBus &&
      (document.getElementById('z35MissionBar') ||
       document.getElementById('nx-mission-strip') ||
       document.getElementById('z33TimelineDock'));

    if (ready()) { _boot(); return; }

    const mo = new MutationObserver(() => { if (ready()) { mo.disconnect(); _boot(); } });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  function _boot() {
    // Execute after a short delay to let Z35, Z36, Z44 finish their own init
    setTimeout(() => {
      consolidateMissionSurfaces();   // Z45E — most critical
      waitForNxBus(() => {
        enhanceSurfaceSync();         // Z45A
        wireReplayNarrative();        // Z45C
        wirePressureIntelligence();   // Z45D
        wireSessionContinuity();      // Z45F
      });
      auditTelemetryLayer();          // Z45G
      console.log('[Phase Z45] Causal synchronization + runtime consolidation active.');
    }, 400);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initZ45);
  } else {
    initZ45();
  }

  // Minimal public API
  window.nxZ45 = {
    consolidate: consolidateMissionSurfaces,
  };
})();
