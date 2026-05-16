/**
 * nx-z35-mission.js — Phase Z35 Operational Mission Presence + Execution Density
 * ════════════════════════════════════════════════════════════════════════════════
 *
 * Z35A — Mission-Centered Workspace (presence layer, adaptive context, heat mapping)
 * Z35B — Execution Density + Operator Flow (density scaling, surface expansion, attention)
 * Z35C — Predictive Operator Assistance (recovery guidance, pressure forecast, suggestions)
 * Z35D — Contextual Workspace Adaptation (layout modes, surface priority, collapse governance)
 * Z35E — Execution Immersion Refinement (ambient presence, motion language, spatial awareness)
 *
 * Rules:
 *  - NO new agents. NO new orchestration.
 *  - NO gamification. NO visual hype. NO telemetry walls.
 *  - Calm execution environment. Operator trust > visual novelty.
 *  - All DOM writes RAF-batched. No layout thrashing.
 *  - Every animation represents state — never decoration.
 */
'use strict';

(function () {
  if (window._z35) return;

  /* ═══════════════════════════════════════════════════════════════════
     STATE
     ═══════════════════════════════════════════════════════════════════ */
  const S = {
    sid:           null,
    phase:         'idle',         // idle | planning | executing | validating | recovering | escalating | replay
    pressure:      0,              // 0-1 derived runtime pressure
    confidence:    null,           // 0-1 semantic confidence
    escalated:     false,
    retryCount:    0,
    errorCount:    0,
    tokenEstimate: 0,
    nodeCount:     0,
    objective:     null,           // mission objective text
    activeNodeId:  null,
    layoutMode:    'execution',    // execution | replay | forensic | escalation | recovery
    heatMap:       {},             // nodeId → heat score 0-1
    suggestions:   [],             // active operator suggestions
    expandedSurfaces: new Set(),   // surfaces currently expanded
    collapsedSurfaces: new Set(),  // surfaces currently collapsed
    initialized:   false,
    rafPending:    false,
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z35A — MISSION PRESENCE LAYER
     Persistent minimal header showing: objective · phase · confidence · escalation
     ═══════════════════════════════════════════════════════════════════ */

  function _injectMissionBar() {
    if (document.getElementById('z35MissionBar')) return;

    const bar = document.createElement('div');
    bar.id = 'z35MissionBar';
    bar.className = 'z35-mission-bar';
    bar.innerHTML = `
      <div class="z35-mission-objective">
        <span class="z35-mission-label">MISSION</span>
        <span id="z35MissionObjective" class="z35-mission-text">—</span>
      </div>
      <div class="z35-mission-indicators">
        <div class="z35-mission-ind" id="z35PhaseInd">
          <span class="z35-mission-label">PHASE</span>
          <span id="z35PhaseText" class="z35-mission-val z35-phase-idle">idle</span>
        </div>
        <div class="z35-mission-ind" id="z35ConfInd">
          <span class="z35-mission-label">CONF</span>
          <span id="z35ConfText" class="z35-mission-val">—</span>
        </div>
        <div class="z35-mission-ind" id="z35PressureInd">
          <span class="z35-mission-label">PRESSURE</span>
          <div class="z35-pressure-micro">
            <div id="z35PressureFill" class="z35-pressure-fill" style="width:0%"></div>
          </div>
        </div>
        <div class="z35-mission-ind" id="z35EscInd" style="display:none">
          <span class="z35-escalation-dot"></span>
          <span class="z35-mission-label z35-esc-label">ESCALATED</span>
        </div>
      </div>
    `;

    // Insert above the main live-wrap or at top of content area
    const liveWrap = document.querySelector('.z30-live-wrap');
    const target   = liveWrap ? liveWrap.parentNode : document.querySelector('#nxTab-live') || document.body;
    target.insertBefore(bar, target.firstChild);
  }

  function _updateMissionBar() {
    const objEl   = document.getElementById('z35MissionObjective');
    const phEl    = document.getElementById('z35PhaseText');
    const confEl  = document.getElementById('z35ConfText');
    const pressEl = document.getElementById('z35PressureFill');
    const escInd  = document.getElementById('z35EscInd');

    if (objEl) {
      objEl.textContent = S.objective ? _truncate(S.objective, 72) : '—';
      objEl.title = S.objective || '';
    }
    if (phEl) {
      phEl.textContent = S.phase;
      phEl.className = `z35-mission-val z35-phase-${S.phase}`;
    }
    if (confEl) {
      if (S.confidence != null) {
        const pct = Math.round(S.confidence * 100);
        const cls = S.confidence >= 0.75 ? 'hi' : S.confidence >= 0.45 ? 'med' : 'low';
        confEl.textContent = pct + '%';
        confEl.className = `z35-mission-val z35-conf-${cls}`;
      } else {
        confEl.textContent = '—';
        confEl.className = 'z35-mission-val';
      }
    }
    if (pressEl) {
      pressEl.style.width = Math.round(S.pressure * 100) + '%';
      pressEl.className = 'z35-pressure-fill ' + (
        S.pressure >= 0.75 ? 'critical' : S.pressure >= 0.5 ? 'warning' : ''
      );
    }
    if (escInd) {
      escInd.style.display = S.escalated ? 'flex' : 'none';
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z35A — MISSION HEAT MAP
     Color-codes DAG nodes by heat score derived from retries + errors + conf drops
     ═══════════════════════════════════════════════════════════════════ */

  function _updateHeatScore(nodeId, delta) {
    S.heatMap[nodeId] = Math.min(1, Math.max(0, (S.heatMap[nodeId] || 0) + delta));
    _applyHeatToNode(nodeId);
  }

  function _applyHeatToNode(nodeId) {
    // Find DAG node element (Z30 renders nodes with data-node-id)
    const el = document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
    if (!el) return;

    const heat = S.heatMap[nodeId] || 0;
    // Remove old heat classes
    el.classList.remove('z35-heat-low', 'z35-heat-med', 'z35-heat-high', 'z35-heat-critical');

    if      (heat >= 0.75) el.classList.add('z35-heat-critical');
    else if (heat >= 0.50) el.classList.add('z35-heat-high');
    else if (heat >= 0.25) el.classList.add('z35-heat-med');
    else if (heat  > 0)   el.classList.add('z35-heat-low');

    el.setAttribute('data-z35-heat', heat.toFixed(2));
  }

  function _applyAllHeat() {
    for (const nodeId of Object.keys(S.heatMap)) {
      _applyHeatToNode(nodeId);
    }
  }

  function _clearHeatMap() {
    S.heatMap = {};
    document.querySelectorAll('[data-z35-heat]').forEach(el => {
      el.classList.remove('z35-heat-low', 'z35-heat-med', 'z35-heat-high', 'z35-heat-critical');
      el.removeAttribute('data-z35-heat');
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z35B — EXECUTION DENSITY + SURFACE EXPANSION
     ═══════════════════════════════════════════════════════════════════ */

  /* Expand/collapse DAG, inspector, timeline, memory surfaces
     based on current phase and pressure */
  function _routeSurfaceExpansion() {
    const phase    = S.phase;
    const pressure = S.pressure;

    // DAG expands during execution and replay
    if (phase === 'executing' || phase === 'planning') {
      _expandSurface('dag');
    } else if (phase === 'replay') {
      _expandSurface('dag');
      _expandSurface('timeline');
    }

    // Inspector expands on failure / escalation / recovery
    if (phase === 'recovering' || phase === 'escalating' || S.errorCount > 0) {
      _expandSurface('inspector');
    }

    // Timeline expands during replay
    if (phase === 'replay') {
      _expandSurface('timeline');
    }

    // Under pressure: collapse memory and skill surfaces
    if (pressure >= 0.75) {
      _collapseSurface('memory');
      _collapseSurface('skills');
    }

    // Low pressure or idle: restore collapsed surfaces
    if (pressure < 0.4 && phase === 'idle') {
      _restoreSurface('memory');
      _restoreSurface('skills');
    }
  }

  function _expandSurface(name) {
    if (S.expandedSurfaces.has(name)) return;
    S.expandedSurfaces.add(name);

    const el = _getSurfaceEl(name);
    if (el) {
      el.classList.add('z35-surface-expanded');
      el.classList.remove('z35-surface-collapsed');
    }
    document.documentElement.setAttribute(`data-z35-expand-${name}`, '1');
  }

  function _collapseSurface(name) {
    if (S.collapsedSurfaces.has(name)) return;
    S.collapsedSurfaces.add(name);
    S.expandedSurfaces.delete(name);

    const el = _getSurfaceEl(name);
    if (el) {
      el.classList.add('z35-surface-collapsed');
      el.classList.remove('z35-surface-expanded');
    }
    document.documentElement.setAttribute(`data-z35-collapse-${name}`, '1');
  }

  function _restoreSurface(name) {
    S.collapsedSurfaces.delete(name);
    const el = _getSurfaceEl(name);
    if (el) {
      el.classList.remove('z35-surface-collapsed');
    }
    document.documentElement.removeAttribute(`data-z35-collapse-${name}`);
  }

  function _getSurfaceEl(name) {
    const map = {
      dag:       () => document.querySelector('.z30-dag-panel'),
      inspector: () => document.getElementById('z34InspectorPanel'),
      timeline:  () => document.getElementById('z33TimelineDock'),
      memory:    () => document.getElementById('z31ForensicPanel'),
      skills:    () => document.querySelector('.z32-skill-panel'),
    };
    return map[name] ? map[name]() : null;
  }

  /* Attention routing via luminance — dims inactive surfaces */
  function _routeAttention() {
    const phase = S.phase;

    // Clear old attention classes
    document.querySelectorAll('.z35-attention-active, .z35-attention-dimmed').forEach(el => {
      el.classList.remove('z35-attention-active', 'z35-attention-dimmed');
    });

    // Focus to the most relevant surface for this phase
    if (phase === 'executing' || phase === 'planning') {
      _setAttention('dag', 'active');
    } else if (phase === 'recovering' || phase === 'escalating') {
      _setAttention('inspector', 'active');
      _setAttention('dag', 'dimmed');
    } else if (phase === 'replay') {
      _setAttention('timeline', 'active');
    }
  }

  function _setAttention(name, level) {
    const el = _getSurfaceEl(name);
    if (el) el.classList.add(`z35-attention-${level}`);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z35C — PREDICTIVE OPERATOR ASSISTANCE
     ═══════════════════════════════════════════════════════════════════ */

  function _injectSuggestionTray() {
    if (document.getElementById('z35SuggestionTray')) return;

    const tray = document.createElement('div');
    tray.id = 'z35SuggestionTray';
    tray.className = 'z35-suggestion-tray';
    tray.innerHTML = `
      <div class="z35-sugg-hdr">
        <span class="z35-sugg-label">OPERATOR GUIDANCE</span>
        <button class="z35-sugg-dismiss" onclick="window._z35.dismissAllSuggestions()" title="Dismiss all">×</button>
      </div>
      <div id="z35SuggestionList" class="z35-sugg-list"></div>
    `;

    // Mount below mission bar, above dag panel
    const bar = document.getElementById('z35MissionBar');
    if (bar && bar.nextSibling) {
      bar.parentNode.insertBefore(tray, bar.nextSibling);
    } else {
      const wrap = document.querySelector('.z30-live-wrap') || document.body;
      wrap.insertBefore(tray, wrap.firstChild);
    }
  }

  /* Suggestion types and their content */
  const SUGGESTION_DEFS = {
    compress: {
      icon: '⊟',
      text: 'Context approaching limit — compress to reduce pressure',
      action: () => { if (window.NxBus) NxBus.emit('z32.compress.trigger', { source: 'z35' }); },
      actionLabel: 'Compress',
    },
    inspect_unstable: {
      icon: '◎',
      text: 'Unstable node detected — open forensic inspector',
      action: (nodeId) => { if (window._z34) _z34.openInspector(nodeId, null); },
      actionLabel: 'Inspect',
    },
    replay_recovery: {
      icon: '⟳',
      text: 'Recovery branch available — replay to review execution path',
      action: () => { if (window.NxBus) NxBus.emit('dag.replay.start', {}); },
      actionLabel: 'Replay',
    },
    escalate_hitl: {
      icon: '⚡',
      text: 'High pressure + repeated failures — consider HITL escalation',
      action: () => { if (window.NxBus) NxBus.emit('z29.hitl.request', { source: 'z35' }); },
      actionLabel: 'Escalate',
    },
    reduce_pressure: {
      icon: '▽',
      text: 'Retry storm forming — reduce concurrency or switch provider',
      action: () => { if (window.NxBus) NxBus.emit('z35.pressure.reduce', {}); },
      actionLabel: 'Reduce',
    },
  };

  function _suggest(type, nodeId) {
    if (S.suggestions.find(s => s.type === type)) return; // deduplicate
    const def = SUGGESTION_DEFS[type];
    if (!def) return;
    S.suggestions.push({ type, nodeId: nodeId || null, ts: Date.now() });
    _renderSuggestions();
    _showSuggestionTray();
  }

  function _dismissSuggestion(type) {
    S.suggestions = S.suggestions.filter(s => s.type !== type);
    _renderSuggestions();
    if (!S.suggestions.length) _hideSuggestionTray();
  }

  function _dismissAllSuggestions() {
    S.suggestions = [];
    _hideSuggestionTray();
  }

  function _renderSuggestions() {
    const list = document.getElementById('z35SuggestionList');
    if (!list) return;

    if (!S.suggestions.length) {
      list.innerHTML = '';
      return;
    }

    list.innerHTML = S.suggestions.slice(0, 3).map(s => {
      const def = SUGGESTION_DEFS[s.type];
      if (!def) return '';
      return `
        <div class="z35-sugg-row" data-type="${_esc(s.type)}">
          <span class="z35-sugg-icon">${def.icon}</span>
          <span class="z35-sugg-text">${_esc(def.text)}</span>
          <div class="z35-sugg-actions">
            <button class="z35-sugg-act-btn"
                    onclick="window._z35.applySuggestion('${_esc(s.type)}', '${_esc(s.nodeId || '')}')">
              ${_esc(def.actionLabel)}
            </button>
            <button class="z35-sugg-dismiss-row"
                    onclick="window._z35.dismissSuggestion('${_esc(s.type)}')" title="Dismiss">×</button>
          </div>
        </div>
      `;
    }).join('');
  }

  function _applySuggestion(type, nodeId) {
    const def = SUGGESTION_DEFS[type];
    if (def && def.action) def.action(nodeId || null);
    _dismissSuggestion(type);
  }

  function _showSuggestionTray() {
    const tray = document.getElementById('z35SuggestionTray');
    if (tray) tray.classList.add('visible');
  }

  function _hideSuggestionTray() {
    const tray = document.getElementById('z35SuggestionTray');
    if (tray) tray.classList.remove('visible');
  }

  /* Evaluate current state and raise/clear suggestions */
  function _evaluateSuggestions() {
    // Context pressure → compress suggestion
    if (S.pressure >= 0.72) {
      _suggest('compress');
    }

    // High heat node → inspect suggestion
    const hotNode = Object.entries(S.heatMap).find(([, h]) => h >= 0.7);
    if (hotNode) {
      _suggest('inspect_unstable', hotNode[0]);
    }

    // Retry storm (≥4 retries) → reduce pressure
    if (S.retryCount >= 4) {
      _suggest('reduce_pressure');
    }

    // Escalation state → HITL suggestion
    if (S.escalated) {
      _suggest('escalate_hitl');
    }

    // Any error + recovery phase → replay suggestion
    if (S.phase === 'recovering' && S.errorCount > 0) {
      _suggest('replay_recovery');
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z35D — CONTEXTUAL WORKSPACE ADAPTATION
     Layout modes applied via data-z35-mode on documentElement
     ═══════════════════════════════════════════════════════════════════ */

  const LAYOUT_MODES = {
    execution: {
      expand:   ['dag'],
      collapse: ['timeline'],
      attention: 'dag',
    },
    replay: {
      expand:   ['dag', 'timeline'],
      collapse: ['inspector'],
      attention: 'timeline',
    },
    forensic: {
      expand:   ['inspector', 'memory'],
      collapse: [],
      attention: 'inspector',
    },
    escalation: {
      expand:   ['inspector'],
      collapse: ['memory', 'skills'],
      attention: 'inspector',
    },
    recovery: {
      expand:   ['inspector', 'dag'],
      collapse: [],
      attention: 'inspector',
    },
  };

  function _setLayoutMode(mode) {
    if (S.layoutMode === mode) return;
    S.layoutMode = mode;
    document.documentElement.setAttribute('data-z35-mode', mode);

    const spec = LAYOUT_MODES[mode];
    if (!spec) return;

    // Clear previous expansion state
    S.expandedSurfaces.forEach(n => _restoreSurface(n));
    S.expandedSurfaces.clear();

    spec.expand.forEach(n => _expandSurface(n));
    spec.collapse.forEach(n => _collapseSurface(n));

    _routeAttention();

    // Update layout mode indicator
    const ind = document.getElementById('z35LayoutModeInd');
    if (ind) {
      ind.textContent = mode.toUpperCase();
      ind.className = `z35-layout-mode-ind z35-mode-${mode}`;
    }
  }

  /* Derive layout mode from current phase */
  function _deriveLayoutMode() {
    const map = {
      idle:       'execution',
      planning:   'execution',
      executing:  'execution',
      validating: 'execution',
      recovering: 'recovery',
      escalating: 'escalation',
      replay:     'replay',
      forensic:   'forensic',
    };
    return map[S.phase] || 'execution';
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z35E — EXECUTION IMMERSION REFINEMENT
     Ambient runtime presence — state-driven, minimal
     ═══════════════════════════════════════════════════════════════════ */

  function _injectAmbientLayer() {
    if (document.getElementById('z35AmbientLayer')) return;

    // Ambient overlay on the DAG surface — very subtle gradient that drifts with execution
    const el = document.createElement('div');
    el.id = 'z35AmbientLayer';
    el.className = 'z35-ambient-layer';

    const surface = document.getElementById('z30DagSurface');
    if (surface) {
      surface.style.position = surface.style.position || 'relative';
      surface.appendChild(el);
    }
  }

  /* Update ambient layer based on phase + pressure */
  function _updateAmbient() {
    const el = document.getElementById('z35AmbientLayer');
    if (!el) return;

    el.className = `z35-ambient-layer z35-ambient-${S.phase}`;
    el.style.setProperty('--z35-pressure', S.pressure.toFixed(3));

    // Under high pressure: subtle pulsing border on critical DAG nodes
    const surface = document.getElementById('z30DagSurface');
    if (surface) {
      surface.setAttribute('data-z35-phase', S.phase);
      surface.setAttribute('data-z35-pressure', S.pressure >= 0.75 ? 'critical' : S.pressure >= 0.5 ? 'elevated' : 'normal');
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     PRESSURE CALCULATION
     Composite of: tokenEstimate, retryCount, errorCount, confidence drop
     ═══════════════════════════════════════════════════════════════════ */

  function _recalcPressure() {
    const tokenPressure = Math.min(1, S.tokenEstimate / 80000);
    const retryPressure = Math.min(1, S.retryCount / 8);
    const errorPressure = Math.min(1, S.errorCount / 4);
    const confPressure  = S.confidence != null ? Math.max(0, 1 - S.confidence) * 0.3 : 0;

    S.pressure = Math.min(1, (tokenPressure * 0.45) + (retryPressure * 0.25) + (errorPressure * 0.2) + confPressure);
  }

  /* ═══════════════════════════════════════════════════════════════════
     PHASE DETECTION FROM LOG ROWS
     ═══════════════════════════════════════════════════════════════════ */

  function _detectPhaseFromLog(text) {
    if (!text) return null;
    if (/\b(plan|think|decompos|analyz|design)\b/i.test(text))  return 'planning';
    if (/\b(execut|run|cod|impl|writ|generat|build)\b/i.test(text)) return 'executing';
    if (/\b(test|verif|validat|check)\b/i.test(text))           return 'validating';
    if (/\b(recover|retry|fix|fallback|replan)\b/i.test(text))  return 'recovering';
    if (/\b(escalat|hitl|block|urgent)\b/i.test(text))          return 'escalating';
    if (/task finished|status=success|✅/i.test(text))          return 'idle';
    return null;
  }

  function _setPhase(phase) {
    if (!phase || S.phase === phase) return;
    S.phase = phase;
    document.documentElement.setAttribute('data-z35-phase', phase);
    _scheduleUpdate();
  }

  /* ═══════════════════════════════════════════════════════════════════
     SCHEDULE UPDATE — RAF-batched
     ═══════════════════════════════════════════════════════════════════ */

  function _scheduleUpdate() {
    if (S.rafPending) return;
    S.rafPending = true;
    requestAnimationFrame(_applyUpdate);
  }

  function _applyUpdate() {
    S.rafPending = false;
    _recalcPressure();
    _updateMissionBar();
    _updateAmbient();
    _routeSurfaceExpansion();
    _setLayoutMode(_deriveLayoutMode());
    _evaluateSuggestions();
    _applyAllHeat();
  }

  /* ═══════════════════════════════════════════════════════════════════
     NXBUS WIRING
     ═══════════════════════════════════════════════════════════════════ */

  function _wireNxBus() {
    if (!window.NxBus) { setTimeout(_wireNxBus, 200); return; }

    /* Session lifecycle */
    NxBus.on('session.started', (e) => {
      const sid = e?.sid || e?.session_id;
      if (sid) _onSessionStart(sid, e?.objective || e?.task || null);
    }, { owner: 'z35' });

    NxBus.on('session.done', () => _onSessionEnd('success'), { owner: 'z35' });
    NxBus.on('session.error', () => _onSessionEnd('error'),  { owner: 'z35' });

    const EV = NxBus.EVENTS || {};
    NxBus.on(EV.SESSION_CREATED  || 'nx:session:created',  (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) _onSessionStart(sid, e?.objective || e?.task || null);
    }, { owner: 'z35' });
    NxBus.on(EV.SESSION_RESTORED || 'nx:session:restored', (e) => {
      const sid = e?.session_id || e?.sid;
      if (sid) { S.sid = sid; _scheduleUpdate(); }
    }, { owner: 'z35' });

    /* Log rows → phase detection + pressure tracking */
    NxBus.on('agent.log_row', (e) => {
      if (!e?.text) return;

      const phase = _detectPhaseFromLog(e.text);
      if (phase) _setPhase(phase);

      const level = e.level || '';
      if (level === 'error' || /error|traceback/i.test(e.text)) {
        S.errorCount++;
        const nodePhase = _detectNodeFromLog(e.text);
        if (nodePhase) _updateHeatScore(nodePhase, 0.3);
      }
      if (/retry|retrying/i.test(e.text)) {
        S.retryCount++;
        const nodePhase = _detectNodeFromLog(e.text);
        if (nodePhase) _updateHeatScore(nodePhase, 0.15);
        _scheduleUpdate();
      }
      if (/token|context/i.test(e.text)) {
        const m = /(\d[\d,]+)\s*(?:tokens?|tok)/i.exec(e.text);
        if (m) S.tokenEstimate = parseInt(m[1].replace(/,/g,''), 10);
      }
      // Capture objective from task start log
      if (!S.objective && /^(Task|Goal|Objective)[:\s]+/i.test(e.text)) {
        S.objective = e.text.replace(/^(Task|Goal|Objective)[:\s]+/i, '').trim().slice(0, 120);
        _scheduleUpdate();
      }
    }, { owner: 'z35' });

    /* Z32 semantic confidence */
    NxBus.on('z32.confidence.update', (e) => {
      if (e?.confidence != null) {
        S.confidence = e.confidence;
        const nodeId = e?.nodeId;
        if (nodeId && e.confidence < 0.45) {
          _updateHeatScore(nodeId, 0.2);
        }
        _scheduleUpdate();
      }
    }, { owner: 'z35' });

    /* Z32 replanning */
    NxBus.on('dag.replan.triggered', (e) => {
      _setPhase('recovering');
      _scheduleUpdate();
    }, { owner: 'z35' });

    /* DAG node events → heat map */
    NxBus.on('dag.node.error', (e) => {
      if (e?.id) {
        _updateHeatScore(e.id, 0.35);
        S.errorCount++;
        _setPhase('recovering');
      }
    }, { owner: 'z35' });

    NxBus.on('dag.node.done', (e) => {
      if (e?.id) {
        // Cool down successfully completed nodes
        S.heatMap[e.id] = Math.max(0, (S.heatMap[e.id] || 0) - 0.1);
      }
    }, { owner: 'z35' });

    NxBus.on('dag.node.selected', (e) => {
      if (e?.node?.id) {
        S.activeNodeId = e.node.id;
        // If node is hot, auto-suggest inspection
        if ((S.heatMap[e.node.id] || 0) >= 0.5) {
          _suggest('inspect_unstable', e.node.id);
        }
      }
    }, { owner: 'z35' });

    /* Replay mode */
    NxBus.on('dag.replay.started', () => {
      _setPhase('replay');
      _setLayoutMode('replay');
    }, { owner: 'z35' });
    NxBus.on('dag.replay.stopped', () => {
      _setPhase(S.sid ? 'idle' : 'idle');
      _setLayoutMode('execution');
    }, { owner: 'z35' });

    /* Z29 HITL escalation */
    NxBus.on('z29.hitl.escalated', () => {
      S.escalated = true;
      _setPhase('escalating');
      _scheduleUpdate();
    }, { owner: 'z35' });
    NxBus.on('z29.hitl.resolved', () => {
      S.escalated = false;
      _scheduleUpdate();
    }, { owner: 'z35' });

    /* Z34 cursor (forensic mode) */
    NxBus.on('z34.cursor.changed', (state) => {
      if (state?.mode === 'forensic') {
        _setLayoutMode('forensic');
      }
    }, { owner: 'z35' });
  }

  function _detectNodeFromLog(text) {
    // Try to extract node-like token from log text
    const m = /\[(plan|code|debug|tool|done|review|test)\]/i.exec(text);
    return m ? m[1].toLowerCase() : null;
  }

  /* ═══════════════════════════════════════════════════════════════════
     SESSION LIFECYCLE
     ═══════════════════════════════════════════════════════════════════ */

  function _onSessionStart(sid, objective) {
    S.sid          = sid;
    S.phase        = 'planning';
    S.pressure     = 0;
    S.confidence   = null;
    S.escalated    = false;
    S.retryCount   = 0;
    S.errorCount   = 0;
    S.tokenEstimate= 0;
    S.objective    = objective || null;
    S.suggestions  = [];

    _clearHeatMap();
    _dismissAllSuggestions();
    _setLayoutMode('execution');
    _scheduleUpdate();

    document.documentElement.setAttribute('data-z35-phase', 'planning');
  }

  function _onSessionEnd(status) {
    _setPhase(status === 'error' ? 'recovering' : 'idle');

    if (status === 'error') {
      _suggest('replay_recovery');
    } else {
      // Clear suggestions on successful completion
      setTimeout(() => _dismissAllSuggestions(), 3000);
    }

    _scheduleUpdate();
  }

  /* ═══════════════════════════════════════════════════════════════════
     INJECT LAYOUT MODE INDICATOR
     ═══════════════════════════════════════════════════════════════════ */

  function _injectLayoutModeIndicator() {
    if (document.getElementById('z35LayoutModeInd')) return;

    const el = document.createElement('span');
    el.id = 'z35LayoutModeInd';
    el.className = 'z35-layout-mode-ind z35-mode-execution';
    el.textContent = 'EXECUTION';
    el.title = 'Current workspace layout mode';

    // Place next to Z34 depth indicator
    const z34ind = document.getElementById('z34DepthIndicator');
    if (z34ind) {
      z34ind.parentNode.insertBefore(el, z34ind.nextSibling);
    } else {
      const hdr = document.querySelector('.z30-dag-panel-hdr');
      if (hdr) hdr.appendChild(el);
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITY
     ═══════════════════════════════════════════════════════════════════ */

  function _esc(s) {
    return String(s ?? '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
  }

  function _truncate(s, n) {
    return s && s.length > n ? s.slice(0, n) + '…' : (s || '');
  }

  /* ═══════════════════════════════════════════════════════════════════
     PUBLIC API
     ═══════════════════════════════════════════════════════════════════ */

  window._z35 = {
    getState:              () => ({ ...S }),
    setPhase:              _setPhase,
    setLayoutMode:         _setLayoutMode,
    suggest:               _suggest,
    dismissSuggestion:     _dismissSuggestion,
    dismissAllSuggestions: _dismissAllSuggestions,
    applySuggestion:       _applySuggestion,
    getHeatMap:            () => ({ ...S.heatMap }),
    update:                _scheduleUpdate,
  };

  /* ═══════════════════════════════════════════════════════════════════
     INIT
     ═══════════════════════════════════════════════════════════════════ */

  function _init() {
    _wireNxBus();

    // Deferred DOM injection — wait for Z30 surfaces to be present
    setTimeout(() => {
      _injectMissionBar();
      _injectSuggestionTray();
      _injectAmbientLayer();
      _injectLayoutModeIndicator();
      _scheduleUpdate();
    }, 900);

    S.initialized = true;
    console.log('[Phase Z35] Operational Mission Presence + Execution Density active.');
  }

  if (window.NX_LOAD_TASKS) {
    window.NX_LOAD_TASKS.push(_init);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 900));
  } else {
    setTimeout(_init, 900);
  }
})();
