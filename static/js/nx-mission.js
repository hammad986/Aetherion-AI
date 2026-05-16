/**
 * nx-mission.js — Nexora Strategic Execution Narrative v1
 * ═══════════════════════════════════════════════════════════════════
 * Mission lifecycle, operational phase transitions, reasoning
 * compression, adaptive messaging, and completion narrative.
 *
 * Surfaces: mission header (timeline), inspector, exec strip.
 * Event-driven only — no synthetic timers for phase changes.
 * Zero writes to Monaco, xterm, SSE, or Split.js.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  /* ══════════════════════════════════════════════════════════════════
     OPERATIONAL PHASES
     ══════════════════════════════════════════════════════════════════ */
  const PHASES = {
    idle:       { label: 'IDLE',       color: '#484f58' },
    analyzing:  { label: 'ANALYZING',  color: '#79c0ff' },
    planning:   { label: 'PLANNING',   color: '#bc8cff' },
    modifying:  { label: 'MODIFYING',  color: '#f59e0b' },
    validating: { label: 'VALIDATING', color: '#f59e0b' },
    recovering: { label: 'RECOVERING', color: '#f59e0b' },
    escalating: { label: 'ESCALATING', color: '#f85149' },
    finalized:  { label: 'FINALIZED',  color: '#3fb950' },
  };

  // Chunk kind → phase transition
  const KIND_PHASE = {
    think:        'analyzing',
    plan:         'planning',
    action:       'modifying',
    tool_success: 'modifying',
    validation:   'validating',
    recovery:     'recovering',
    escalation:   'escalating',
    output:       null,   // no phase change on generic output
  };

  /* ══════════════════════════════════════════════════════════════════
     MISSION STATE
     ══════════════════════════════════════════════════════════════════ */
  const _mission = {
    phase:          'idle',
    objective:      '',
    strategy:       '',
    blockers:       [],
    filesModified:  new Set(),
    filesCreated:   new Set(),
    validations:    { passed: 0, failed: 0 },
    adaptations:    0,
    startedAt:      null,
    taskInput:      '',
  };

  /* ══════════════════════════════════════════════════════════════════
     CONTINUITY MEMORY (cross-session patterns)
     ══════════════════════════════════════════════════════════════════ */
  const _continuity = {
    recurringBlockers:  {},   // blocker text → count
    successfulRecoveries: 0,
    escalationCauses:   [],
  };

  function _recordBlocker(text) {
    const key = text.slice(0, 60);
    _continuity.recurringBlockers[key] = (_continuity.recurringBlockers[key] || 0) + 1;
  }

  /* ══════════════════════════════════════════════════════════════════
     REASONING COMPRESSOR
     ══════════════════════════════════════════════════════════════════ */
  // Tracks recent chunk text hashes to suppress near-duplicates
  const _recentHashes   = new Set();
  const COMPRESS_WINDOW = 6000; // ms — clear hash window

  function _hashText(text) {
    // Simple 32-char prefix + length fingerprint
    return text.trim().slice(0, 32) + '|' + text.length;
  }

  function _isDuplicate(text) {
    const h = _hashText(text);
    if (_recentHashes.has(h)) return true;
    _recentHashes.add(h);
    setTimeout(() => _recentHashes.delete(h), COMPRESS_WINDOW);
    return false;
  }

  // Pattern → compressed summary
  const COMPRESSION_PATTERNS = [
    { rx: /analyz\w+ (file|depend|import|module|workspace)/i,
      summary: () => 'Analyzing workspace dependencies' },
    { rx: /reading|loading|parsing (file|module|config)/i,
      summary: (m) => `Loading ${m[1]}` },
    { rx: /writ\w+|creat\w+|generat\w+ (file|class|function|method|route|endpoint)/i,
      summary: (m) => `Generating ${m[1]}` },
    { rx: /install\w+|pip install|npm install/i,
      summary: () => 'Installing dependencies' },
    { rx: /test\w+ (pass|fail|skip)/i,
      summary: (m) => `Tests: ${m[1].toUpperCase()}` },
    { rx: /retry|attempt \d+/i,
      summary: () => 'Retrying failed operation' },
    { rx: /recover\w+|fallback/i,
      summary: () => 'Switching recovery strategy' },
    { rx: /validat\w+ (endpoint|api|response|syntax|output)/i,
      summary: (m) => `Validating ${m[1]}` },
    { rx: /rebuild\w+|restart\w+|reinitializ\w+/i,
      summary: () => 'Rebuilding runtime configuration' },
    { rx: /import error|module not found|no module named/i,
      summary: () => 'Resolving missing module' },
  ];

  function _compress(text) {
    if (!text || text.length < 20) return null;
    for (const p of COMPRESSION_PATTERNS) {
      const m = p.rx.exec(text);
      if (m) return p.summary(m);
    }
    return null;
  }

  /* ══════════════════════════════════════════════════════════════════
     MISSION HEADER CARD (rendered in timeline)
     ══════════════════════════════════════════════════════════════════ */
  let _missionCard  = null;
  let _missionPanel = null;

  function _getPanel() {
    if (_missionPanel && _missionPanel.isConnected) return _missionPanel;
    _missionPanel = $('nxTab-logs')
      || document.querySelector('[data-nxtab="logs"].nx-panel-content');
    return _missionPanel;
  }

  function _createMissionCard() {
    const panel = _getPanel();
    if (!panel) return;
    if (_missionCard && _missionCard.isConnected) {
      panel.insertBefore(_missionCard, panel.firstChild);
      return;
    }

    _missionCard = document.createElement('div');
    _missionCard.className = 'nx-mission-card';
    _missionCard.id = 'nxMissionCard';
    _missionCard.innerHTML = `
      <div class="nx-mission-row">
        <span class="nx-mission-objective" id="nxMissionObjective">—</span>
        <span class="nx-mission-phase" id="nxMissionPhase" style="color:#484f58">IDLE</span>
      </div>
      <div class="nx-mission-strategy" id="nxMissionStrategy"></div>
    `;
    panel.insertBefore(_missionCard, panel.firstChild);
  }

  function _updateMissionCard() {
    const obj  = $('nxMissionObjective');
    const ph   = $('nxMissionPhase');
    const st   = $('nxMissionStrategy');

    const p = PHASES[_mission.phase] || PHASES.idle;

    if (obj && _mission.objective) obj.textContent = _mission.objective.slice(0, 72);
    if (ph) { ph.textContent = p.label; ph.style.color = p.color; }
    if (st && _mission.strategy) st.textContent = _mission.strategy;
  }

  /* ══════════════════════════════════════════════════════════════════
     PHASE TRANSITION
     ══════════════════════════════════════════════════════════════════ */
  function _setPhase(phase) {
    if (!PHASES[phase] || _mission.phase === phase) return;
    _mission.phase = phase;
    _updateMissionCard();

    // Sync exec strip phase label
    const stripState = $('nxExecStripState');
    if (stripState) {
      const p = PHASES[phase];
      stripState.textContent = p.label;
      stripState.style.color = p.color;
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     ADAPTIVE STRATEGY MESSAGING
     ══════════════════════════════════════════════════════════════════ */
  function _setStrategy(msg) {
    if (!msg || _mission.strategy === msg) return;
    _mission.strategy = msg;
    _mission.adaptations++;
    const el = $('nxMissionStrategy');
    if (el) el.textContent = msg;
  }

  function _adaptiveStrategy(kind, text) {
    if (kind === 'recovery') {
      _mission.adaptations++;
      _setStrategy('Switching recovery strategy after failed validation');
      _recordBlocker(text);
    } else if (kind === 'escalation') {
      _setStrategy('Escalating — confidence threshold breached');
      _continuity.escalationCauses.push(text.slice(0, 80));
    } else if (kind === 'tool_success' && /fallback/i.test(text)) {
      _setStrategy('Fallback model activated — continuing execution');
      _continuity.successfulRecoveries++;
    } else if (kind === 'validation') {
      _setStrategy('Validating execution results');
    } else if (kind === 'action') {
      const comp = _compress(text);
      if (comp) _setStrategy(comp);
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     COMPLETION NARRATIVE
     ══════════════════════════════════════════════════════════════════ */
  function _renderCompletion(payload) {
    const panel = _getPanel();
    if (!panel) return;

    const modified  = Array.from(_mission.filesModified);
    const created   = Array.from(_mission.filesCreated);
    const duration  = _mission.startedAt
      ? Math.round((Date.now() - _mission.startedAt) / 1000) + 's'
      : '—';

    const confPct   = payload && payload.confidence != null
      ? Math.round(payload.confidence * 100) + '%'
      : '—';

    const uncertain = _mission.validations.failed > 0
      ? `${_mission.validations.failed} validation(s) unresolved`
      : 'None';

    const card = document.createElement('div');
    card.className = 'nx-completion-card';
    card.innerHTML = `
      <div class="nx-completion-header">
        <span class="nx-completion-icon">▣</span>
        <span class="nx-completion-title">Execution Complete</span>
        <span class="nx-completion-duration">${duration}</span>
        <span class="nx-completion-conf" id="nxCompletionConf">${confPct}</span>
      </div>
      <div class="nx-completion-body">
        ${modified.length ? `<div class="nx-completion-row"><span>Modified</span><span>${modified.slice(0,5).map(f=>f.split('/').pop()).join(', ')}${modified.length > 5 ? ' +' + (modified.length-5) : ''}</span></div>` : ''}
        ${created.length  ? `<div class="nx-completion-row"><span>Created</span><span>${created.slice(0,5).map(f=>f.split('/').pop()).join(', ')}</span></div>` : ''}
        ${_mission.validations.passed ? `<div class="nx-completion-row"><span>Validations</span><span style="color:#3fb950">${_mission.validations.passed} passed</span></div>` : ''}
        ${_mission.validations.failed ? `<div class="nx-completion-row"><span>Uncertain</span><span style="color:#f59e0b">${uncertain}</span></div>` : ''}
        ${_mission.adaptations > 0    ? `<div class="nx-completion-row"><span>Adaptations</span><span>${_mission.adaptations}</span></div>` : ''}
        ${payload && payload.completed_steps ? `<div class="nx-completion-row"><span>Steps</span><span>${payload.completed_steps}/${payload.total_steps || '?'}</span></div>` : ''}
      </div>
    `;

    // Prepend after mission card
    if (_missionCard && _missionCard.isConnected) {
      _missionCard.insertAdjacentElement('afterend', card);
    } else {
      panel.insertBefore(card, panel.firstChild);
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     MISSION RESET
     ══════════════════════════════════════════════════════════════════ */
  function _resetMission() {
    _mission.phase         = 'idle';
    _mission.objective     = '';
    _mission.strategy      = '';
    _mission.blockers      = [];
    _mission.filesModified = new Set();
    _mission.filesCreated  = new Set();
    _mission.validations   = { passed: 0, failed: 0 };
    _mission.adaptations   = 0;
    _mission.startedAt     = null;
    _recentHashes.clear();
    _updateMissionCard();
  }

  /* ══════════════════════════════════════════════════════════════════
     NxBus WIRING
     ══════════════════════════════════════════════════════════════════ */
  let _initialized = false;
  let _chunkCount  = 0;

  function _wire() {
    if (_initialized) return;
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_wire, 200); return; }
    _initialized = true;
    const E = NxBus.EVENTS;

    /* ── Agent start: capture objective from task input ── */
    NxBus.on(E.AGENT_START, (d) => {
      _resetMission();
      _mission.startedAt = Date.now();
      _mission.objective = d && d.task
        ? d.task
        : ($('taskInput') || {}).value || 'Executing task';
      _setPhase('analyzing');
      _createMissionCard();
    }, { owner: 'nx-mission' });

    /* ── Also react to SESSION_CREATED as a proxy for task start ── */
    NxBus.on(E.SESSION_CREATED, (d) => {
      if (_mission.startedAt) return; // already started via AGENT_START
      _mission.startedAt = Date.now();
      const taskEl = $('taskInput');
      _mission.objective = taskEl ? taskEl.value.slice(0, 80) : 'Executing task';
      _setPhase('analyzing');
      _createMissionCard();
    }, { owner: 'nx-mission' });

    /* ── Stream chunks: phase transitions + compression ── */
    NxBus.on(E.STREAM_CHUNK, (d) => {
      const kind = d.kind || 'output';
      const text = d.text || d.output || '';

      // Phase transition
      const newPhase = KIND_PHASE[kind];
      if (newPhase) _setPhase(newPhase);

      // Adaptive strategy messaging
      _adaptiveStrategy(kind, text);

      // Duplicate suppression — very high-frequency chunk noise
      if (_isDuplicate(text)) return;

      // Compressed strategy update for verbose think chunks
      if (kind === 'think') {
        _chunkCount++;
        // Only update strategy every 3rd think chunk to avoid noise
        if (_chunkCount % 3 === 0) {
          const comp = _compress(text);
          if (comp) _setStrategy(comp);
        }
      }
    }, { owner: 'nx-mission' });

    /* ── File changes ── */
    NxBus.on(E.FILE_CHANGED, (d) => {
      const path = d.path || d.file || '';
      if (!path) return;
      if (d.action === 'created')  _mission.filesCreated.add(path);
      else                         _mission.filesModified.add(path);
    }, { owner: 'nx-mission' });

    /* ── Trust signals: validation tracking ── */
    NxBus.on('nx:trust:signal', (d) => {
      if (d.verified === true)  _mission.validations.passed++;
      if (d.verified === false) _mission.validations.failed++;
    }, { owner: 'nx-mission' });

    /* ── HITL ── */
    NxBus.on('nx:hitl:required', (d) => {
      _setPhase('escalating');
      _setStrategy(`Escalation: ${(d.prompt || 'Operator approval required').slice(0,60)}`);
      _continuity.escalationCauses.push((d.prompt || '').slice(0, 80));
    }, { owner: 'nx-mission' });

    /* ── Stream error → recovering ── */
    NxBus.on(E.STREAM_ERROR, (d) => {
      _setPhase('recovering');
      _setStrategy(`Recovery: ${(d.error || 'runtime error').slice(0, 80)}`);
      _recordBlocker(d.error || '');
    }, { owner: 'nx-mission' });

    /* ── Done: finalized ── */
    NxBus.on(E.AGENT_DONE, (d) => {
      _setPhase('finalized');
      _mission.strategy = '';
      _renderCompletion(d);

      // Persist successful recovery count
      if (_mission.adaptations > 0) _continuity.successfulRecoveries++;
    }, { owner: 'nx-mission' });

    /* ── Stop ── */
    NxBus.on(E.AGENT_STOP, () => {
      _setPhase('idle');
      _mission.strategy = 'Execution stopped by operator';
      _updateMissionCard();
    }, { owner: 'nx-mission' });

    /* ── Session cleared ── */
    NxBus.on(E.SESSION_CLEARED, () => {
      _resetMission();
      if (_missionCard && _missionCard.isConnected) _missionCard.remove();
      _missionCard = null;
    }, { owner: 'nx-mission' });

  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 500));
  } else {
    setTimeout(_wire, 500);
  }

  window.NxMission = {
    getPhase:       () => _mission.phase,
    getContinuity:  () => _continuity,
    getObjective:   () => _mission.objective,
  };

})();
