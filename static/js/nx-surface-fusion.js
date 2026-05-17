/**
 * nx-surface-fusion.js — Aetherion Unified Execution Environment v1
 * ═══════════════════════════════════════════════════════════════════
 * Fuses all runtime surfaces into one mission-centered workspace.
 *
 * Responsibilities:
 *  1. Smart Auto-Focus — soft contextual routing, no aggressive tab steals
 *  2. Monaco Execution State — glyph/color markers for touched/unstable files
 *  3. Preview Intelligence — extended state management
 *  4. Terminal Mission Fusion — phase-aware status header
 *  5. Inspector Chain Grouping — coherent narrative grouping
 *  6. Workspace Calmness — idle/cleanup routines
 *
 * Preserves: NxBus, NxMission, NxTrust, NxChunker, NxOrchestrator,
 *            Monaco, xterm, Split.js, SSE runtime.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const $q = sel => document.querySelector(sel);

  /* ══════════════════════════════════════════════════════════════════
     1. SMART AUTO-FOCUS ENGINE (soft guidance only)
     ══════════════════════════════════════════════════════════════════ */
  // Debounce focus hints to avoid rapid context switching
  let _focusTimer = null;
  const FOCUS_DEBOUNCE_MS = 1200;

  function _softFocus(tabName, reason) {
    clearTimeout(_focusTimer);
    _focusTimer = setTimeout(() => {
      _hintTab(tabName, reason);
    }, FOCUS_DEBOUNCE_MS);
  }

  // Highlight a tab with a subtle pulse — does NOT switch the active tab
  function _hintTab(tabName, reason) {
    const tab = $q(`[data-nxtab="${tabName}"]`);
    if (!tab) return;

    // Add hint glow — CSS drives the animation
    tab.classList.add('nx-tab-hint');
    tab.title = reason || tab.title;

    clearTimeout(tab._hintTimer);
    tab._hintTimer = setTimeout(() => {
      tab.classList.remove('nx-tab-hint');
    }, 4000);

    // Show a minimal toast-style banner (not a modal)
    _showContextBanner(reason, tabName);
  }

  // Only switch tab when it's truly operator-relevant (HITL, fatal errors)
  function _switchTab(tabName) {
    if (typeof nxSetTab === 'function') nxSetTab(tabName);
  }

  /* ══════════════════════════════════════════════════════════════════
     2. CONTEXT BANNER (minimal, auto-dismissing)
     ══════════════════════════════════════════════════════════════════ */
  let _banner     = null;
  let _bannerTimer = null;

  function _showContextBanner(msg, tab) {
    if (!msg) return;

    if (!_banner) {
      _banner = document.createElement('div');
      _banner.id = 'nxContextBanner';
      _banner.className = 'nx-context-banner';
      _banner.onclick = () => _banner.classList.remove('visible');
      const shell = $q('.nx-shell-root');
      if (shell) shell.appendChild(_banner);
    }

    const tabLabel = { logs:'Output', code:'Code', preview:'Preview', terminal:'Terminal', inspector:'Inspector' }[tab] || tab;
    _banner.innerHTML = `<span class="nx-cb-dot"></span><span class="nx-cb-msg">${_esc(msg)}</span><span class="nx-cb-tab">${tabLabel}</span>`;
    _banner.classList.add('visible');

    clearTimeout(_bannerTimer);
    _bannerTimer = setTimeout(() => {
      _banner.classList.remove('visible');
    }, 3500);
  }

  /* ══════════════════════════════════════════════════════════════════
     3. MONACO EXECUTION STATE
     — Marks touched / unstable / validated files in the tab bar
     ══════════════════════════════════════════════════════════════════ */
  const _monacoFileState = new Map(); // path → 'modified' | 'unstable' | 'validated' | 'recovery'

  function _setMonacoFileState(path, state) {
    if (!path) return;
    _monacoFileState.set(path, state);

    // Mark the Monaco tab (NxMonaco tab bar uses data-path or title attrs)
    const tabBar = $q('#nxMonacoTabBar') || $q('.nx-monaco-tab-bar') || $q('.nx-tab-scroll');
    if (!tabBar) return;

    const tabEl = tabBar.querySelector(`[data-path="${path}"]`)
      || Array.from(tabBar.querySelectorAll('[class*="tab"]'))
           .find(el => el.dataset.file === path || el.title === path);

    if (!tabEl) return;

    // Remove prior state classes
    tabEl.classList.remove('nx-file-modified', 'nx-file-unstable', 'nx-file-validated', 'nx-file-recovery');
    if (state) tabEl.classList.add(`nx-file-${state}`);
  }

  function _syncAllMonacoStates() {
    _monacoFileState.forEach((state, path) => _setMonacoFileState(path, state));
  }

  /* ══════════════════════════════════════════════════════════════════
     4. PREVIEW INTELLIGENCE — extended state machine
     ══════════════════════════════════════════════════════════════════ */
  const _previewStates = {
    rebuilding:    { label: 'REBUILDING',    color: '#79c0ff', dot: 'building'   },
    validating:    { label: 'VALIDATING',    color: '#f59e0b', dot: 'validating' },
    degraded:      { label: 'DEGRADED',      color: '#f59e0b', dot: 'degraded'   },
    healthy:       { label: 'HEALTHY',       color: '#3fb950', dot: ''           },
    disconnected:  { label: 'DISCONNECTED',  color: '#484f58', dot: 'failed'     },
    retrying:      { label: 'RETRYING',      color: '#f59e0b', dot: 'validating' },
    failed:        { label: 'FAILED',        color: '#f85149', dot: 'failed'     },
    generating:    { label: 'GENERATING',    color: '#bc8cff', dot: 'building'   },
  };

  function _setPreviewState(state) {
    // Delegate to NxOrchestrator if available, else direct
    if (window.NxOrchestrator) {
      NxOrchestrator.setPreviewState(state);
      return;
    }

    const overlay = $('nxPreviewOverlay');
    const s = _previewStates[state];
    if (!overlay || !s) return;

    if (state === 'healthy') { overlay.style.display = 'none'; return; }
    overlay.style.display = 'flex';
    overlay.innerHTML = `<div class="nx-preview-dot ${s.dot}"></div><span style="color:${s.color};font-size:10px;font-weight:700;letter-spacing:0.06em;">${s.label}</span>`;
  }

  /* ══════════════════════════════════════════════════════════════════
     5. TERMINAL MISSION FUSION
     ══════════════════════════════════════════════════════════════════ */
  let _termHeader = null;

  function _ensureTermHeader() {
    if (_termHeader && _termHeader.isConnected) return _termHeader;
    const termTab = $('nxTab-terminal');
    if (!termTab) return null;

    _termHeader = document.createElement('div');
    _termHeader.id = 'nxTermMissionHeader';
    _termHeader.className = 'nx-term-mission-header';
    _termHeader.innerHTML = `
      <span class="nx-tm-phase" id="nxTmPhase" style="color:#484f58">IDLE</span>
      <span class="nx-tm-sep">·</span>
      <span class="nx-tm-op" id="nxTmOp">Ready</span>
    `;

    const existing = termTab.querySelector('div[style*="border-bottom"]');
    if (existing) {
      existing.insertAdjacentElement('beforebegin', _termHeader);
    } else {
      termTab.insertBefore(_termHeader, termTab.firstChild);
    }
    return _termHeader;
  }

  function _updateTermHeader(phase, op) {
    _ensureTermHeader();
    const phEl = $('nxTmPhase');
    const opEl = $('nxTmOp');

    const PHASES = {
      idle:       '#484f58', analyzing:'#79c0ff', planning:'#bc8cff',
      modifying:  '#f59e0b', validating:'#f59e0b', recovering:'#f59e0b',
      escalating: '#f85149', finalized:'#3fb950',
    };

    if (phEl && phase) {
      phEl.textContent = phase.toUpperCase();
      phEl.style.color = PHASES[phase] || '#484f58';
    }
    if (opEl && op) opEl.textContent = op.slice(0, 60);
  }

  /* ══════════════════════════════════════════════════════════════════
     6. INSPECTOR CHAIN GROUPING
     ── Groups entries by execution chain instead of dumping raw notes
     ══════════════════════════════════════════════════════════════════ */
  let _inspChain     = null;   // current chain container
  let _inspChainKind = null;
  let _inspChainTimer = null;
  const CHAIN_SEAL_MS = 3000;

  function _appendInspectorChained(kind, text) {
    const panel = $('nxInspectorContent');
    if (!panel) return;

    const CHAIN_GROUPS = {
      think:      'reasoning', action: 'action', tool_success: 'action',
      validation: 'validation', recovery: 'action', escalation: 'escalation',
    };
    const chainKind = CHAIN_GROUPS[kind] || 'output';

    // Continue existing chain or start new
    if (!_inspChain || _inspChainKind !== chainKind || !_inspChain.isConnected) {
      _sealChain();
      _inspChain = document.createElement('div');
      _inspChain.className = 'nx-insp-chain';
      _inspChain.dataset.kind = chainKind;
      _inspChainKind = chainKind;
      panel.insertBefore(_inspChain, panel.firstChild);
    }

    // Append entry to chain
    const row = document.createElement('div');
    row.className = 'nx-insp-chain-row';
    row.textContent = text.slice(0, 200);
    _inspChain.appendChild(row);

    // Cap rows per chain
    const rows = _inspChain.querySelectorAll('.nx-insp-chain-row');
    if (rows.length > 8) rows[0].remove();

    // Seal chain after silence
    clearTimeout(_inspChainTimer);
    _inspChainTimer = setTimeout(_sealChain, CHAIN_SEAL_MS);

    // Cap total chains
    const chains = panel.querySelectorAll('.nx-insp-chain');
    if (chains.length > 6) chains[chains.length - 1].remove();
  }

  function _sealChain() {
    if (_inspChain) {
      _inspChain.classList.add('nx-insp-chain--sealed');
      _inspChain = null;
      _inspChainKind = null;
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     7. IDLE CLEANUP — reduce surface noise between missions
     ══════════════════════════════════════════════════════════════════ */
  function _onMissionFinalized() {
    // Auto-collapse all but the last execution chunk after 15s
    setTimeout(() => {
      const chunks = document.querySelectorAll('.nx-exec-chunk:not(:last-child)');
      chunks.forEach(ch => ch.classList.add('nx-chunk-collapsed'));
    }, 15000);

    // Clear terminal header op after 8s
    setTimeout(() => {
      const opEl = $('nxTmOp');
      if (opEl) opEl.textContent = 'Mission complete';
    }, 8000);
  }

  /* ══════════════════════════════════════════════════════════════════
     8. NxBus WIRING
     ══════════════════════════════════════════════════════════════════ */
  let _initialized = false;

  function _wire() {
    if (_initialized) return;
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_wire, 200); return; }
    _initialized = true;
    const E = NxBus.EVENTS;

    /* ── STREAM_CHUNK ── */
    NxBus.on(E.STREAM_CHUNK, (d) => {
      const kind = d.kind || 'output';
      const text = d.text || d.output || '';

      // Terminal mission fusion
      const opMap = {
        think: 'Analyzing...', action: 'Executing action',
        tool_success: 'Tool completed', validation: 'Validating results',
        recovery: 'Recovering...', escalation: 'Escalation triggered',
      };
      const phase = window.NxMission ? NxMission.getPhase() : 'idle';
      _updateTermHeader(phase, opMap[kind] || text.slice(0, 50));

      // Inspector chain grouping for high-signal kinds
      if (['think','action','validation','recovery'].includes(kind) && text.length > 15) {
        _appendInspectorChained(kind, text);
      }

      // Soft focus hints
      if (kind === 'validation') _softFocus('logs', 'Validation running');
      if (kind === 'recovery')   _softFocus('logs', 'Recovery in progress');

    }, { owner: 'nx-surface-fusion' });

    /* ── FILE_CHANGED: Monaco state sync ── */
    NxBus.on(E.FILE_CHANGED, (d) => {
      const path = d.path || d.file || '';
      const act  = d.action || 'modified';

      _setMonacoFileState(path, act === 'created' ? 'modified' : act === 'deleted' ? 'unstable' : 'modified');
      _setPreviewState('rebuilding');

      // Soft focus: code tab hint for modified files
      _softFocus('code', `${path.split('/').pop()} ${act}`);

    }, { owner: 'nx-surface-fusion' });

    /* ── Trust signals: mark validated/unstable files ── */
    NxBus.on('nx:trust:signal', (d) => {
      if (d.verified === true && d.step) {
        _setMonacoFileState(d.step, 'validated');
      } else if (d.verified === false && d.step) {
        _setMonacoFileState(d.step, 'unstable');
        _softFocus('code', `Validation failed: ${d.step.split('/').pop()}`);
      }
    }, { owner: 'nx-surface-fusion' });

    /* ── HITL: force inspector open + switch tab ── */
    NxBus.on('nx:hitl:required', () => {
      _sealChain();
      _setPreviewState('degraded');
      _updateTermHeader('escalating', 'Operator approval required');
      // Inspector must open — this IS a focus-stealing case
      const insp = $('nxInspectorPanel');
      if (insp && !insp.classList.contains('is-open')) insp.classList.add('is-open');
      _showContextBanner('Operator approval required — review inspector', 'inspector');
    }, { owner: 'nx-surface-fusion' });

    /* ── Stream error: surface trace ── */
    NxBus.on(E.STREAM_ERROR, (d) => {
      _sealChain();
      _setPreviewState('failed');
      _updateTermHeader('recovering', 'Error — recovering');
      _softFocus('logs', 'Execution error — check trace');
    }, { owner: 'nx-surface-fusion' });

    /* ── Done ── */
    NxBus.on(E.AGENT_DONE, (d) => {
      _sealChain();
      _setPreviewState('healthy');
      _updateTermHeader('finalized', 'Mission complete');
      _onMissionFinalized();
      _syncAllMonacoStates();
    }, { owner: 'nx-surface-fusion' });

    /* ── Stop ── */
    NxBus.on(E.AGENT_STOP, () => {
      _sealChain();
      _setPreviewState('degraded');
      _updateTermHeader('idle', 'Stopped by operator');
    }, { owner: 'nx-surface-fusion' });

    /* ── Reconnect ── */
    NxBus.on(E.WS_STATUS, (d) => {
      if (d.state === 'reconnecting') {
        _setPreviewState('disconnected');
        _updateTermHeader('recovering', 'Reconnecting stream...');
      } else if (d.state === 'connected') {
        _setPreviewState('healthy');
      }
    }, { owner: 'nx-surface-fusion' });

    /* ── Session cleared ── */
    NxBus.on(E.SESSION_CLEARED, () => {
      _monacoFileState.clear();
      _sealChain();
      _setPreviewState('healthy');
      _updateTermHeader('idle', 'Ready');
      if (_banner) _banner.classList.remove('visible');
    }, { owner: 'nx-surface-fusion' });
  }

  function _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 600));
  } else {
    setTimeout(_wire, 600);
  }

  window.NxSurfaceFusion = { setPreviewState: _setPreviewState, hintTab: _hintTab };

})();
