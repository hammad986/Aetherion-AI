/**
 * nx-chunker.js — Aetherion Execution Chunking Engine v1
 * ═══════════════════════════════════════════════════════
 * Intercepts NxBus STREAM_CHUNK events and groups them into
 * structured execution blocks rendered in the Output timeline.
 *
 * Group types: PLAN · REASONING · ACTION · TOOL · VALIDATION · RESULT · RECOVERY · ESCALATION
 * Zero rewrites to backend SSE or runtime logic.
 * ═══════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Configuration ──────────────────────────────────────────────── */
  const GROUP_TIMEOUT_MS = 1800;   // ms of silence before group is sealed
  const MAX_LINES_BEFORE_COLLAPSE = 6;
  const AUTO_COLLAPSE_OLD_MS = 25000; // collapse groups older than this

  /* ── Kind → Group mapping ────────────────────────────────────────── */
  const KIND_GROUP = {
    'think':        'REASONING',
    'action':       'ACTION',
    'tool_success': 'TOOL',
    'output':       'RESULT',
    'plan':         'PLAN',
    'validation':   'VALIDATION',
    'recovery':     'RECOVERY',
    'escalation':   'ESCALATION',
  };

  const GROUP_META = {
    PLAN:       { color: '#bc8cff', icon: '◈', label: 'Plan' },
    REASONING:  { color: '#79c0ff', icon: '◆', label: 'Reasoning' },
    ACTION:     { color: '#f59e0b', icon: '▶', label: 'Action' },
    TOOL:       { color: '#4caf50', icon: '⚙', label: 'Tool' },
    VALIDATION: { color: '#3fb950', icon: '✓', label: 'Validation' },
    RESULT:     { color: '#e0e0e0', icon: '▣', label: 'Result' },
    RECOVERY:   { color: '#f59e0b', icon: '↺', label: 'Recovery' },
    ESCALATION: { color: '#f85149', icon: '⚠', label: 'Escalation' },
  };

  /* ── State ───────────────────────────────────────────────────────── */
  let _activeGroup     = null;  // { type, el, contentEl, lines, timer, ts }
  let _logPanel        = null;
  let _groupCount      = 0;
  let _initialized     = false;

  /* ── Helpers ─────────────────────────────────────────────────────── */
  function _getLogPanel() {
    if (_logPanel && _logPanel.isConnected) return _logPanel;
    // Primary target: nx-timeline panel OR the logs tab content
    _logPanel = document.getElementById('nxLogPanel')
      || document.querySelector('[data-nxtab="logs"].nx-panel-content')
      || document.querySelector('.nx-panel[data-nxtab="logs"] .nx-log-output')
      || document.querySelector('#nxCenterMain');
    return _logPanel;
  }

  function _ts() {
    const d = new Date();
    return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}:${d.getSeconds().toString().padStart(2,'0')}`;
  }

  function _escHtml(s) {
    if (!s) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ── Group rendering ─────────────────────────────────────────────── */
  function _createGroup(type) {
    const m    = GROUP_META[type] || GROUP_META.RESULT;
    const id   = `nx-chunk-${++_groupCount}`;
    const panel = _getLogPanel();
    if (!panel) return null;

    const el = document.createElement('div');
    el.className   = 'nx-exec-chunk';
    el.id          = id;
    el.dataset.type = type;

    el.innerHTML = `
      <div class="nx-chunk-header" onclick="nxChunkerToggle('${id}')">
        <span class="nx-chunk-icon" style="color:${m.color}">${m.icon}</span>
        <span class="nx-chunk-label" style="color:${m.color}">${m.label}</span>
        <span class="nx-chunk-ts">${_ts()}</span>
        <span class="nx-chunk-caret">▾</span>
      </div>
      <div class="nx-chunk-body" id="${id}-body"></div>
    `;

    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;

    return {
      type,
      id,
      el,
      contentEl: el.querySelector('.nx-chunk-body'),
      lines: 0,
      ts: Date.now(),
      timer: null,
    };
  }

  function _appendLine(group, text, tool) {
    if (!group || !group.contentEl) return;

    const row = document.createElement('div');
    row.className = 'nx-chunk-row';

    if (tool) {
      row.innerHTML = `<span class="nx-chunk-tool">${_escHtml(tool)}</span><span class="nx-chunk-text">${_escHtml(text)}</span>`;
    } else {
      row.innerHTML = `<span class="nx-chunk-text">${_escHtml(text)}</span>`;
    }

    group.contentEl.appendChild(row);
    group.lines++;

    // Auto-collapse body after threshold — progressive disclosure
    if (group.lines > MAX_LINES_BEFORE_COLLAPSE) {
      group.contentEl.classList.add('nx-chunk-overflow');
    }

    // Keep panel scrolled to bottom
    const panel = _getLogPanel();
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  function _sealGroup(group) {
    if (!group) return;
    clearTimeout(group.timer);
    group.el.classList.add('nx-chunk-sealed');

    // Auto-collapse old sealed groups after delay
    setTimeout(() => {
      if (group.el.isConnected) {
        group.el.classList.add('nx-chunk-collapsed');
      }
    }, AUTO_COLLAPSE_OLD_MS);
  }

  function _resetGroupTimer(group) {
    if (!group) return;
    clearTimeout(group.timer);
    group.timer = setTimeout(() => {
      _sealGroup(group);
      if (_activeGroup === group) _activeGroup = null;
    }, GROUP_TIMEOUT_MS);
  }

  /* ── Public toggle (called from HTML onclick) ────────────────────── */
  window.nxChunkerToggle = function(id) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('nx-chunk-collapsed');
  };

  /* ── Ingest a stream event ───────────────────────────────────────── */
  function _ingest(kind, text, tool) {
    const groupType = KIND_GROUP[kind] || 'RESULT';

    // Decide whether to continue current group or start a new one
    const needsNewGroup = !_activeGroup
      || _activeGroup.type !== groupType
      || _activeGroup.el.classList.contains('nx-chunk-sealed');

    if (needsNewGroup) {
      if (_activeGroup) _sealGroup(_activeGroup);
      _activeGroup = _createGroup(groupType);
      if (!_activeGroup) return; // no panel found
    }

    _appendLine(_activeGroup, text, tool);
    _resetGroupTimer(_activeGroup);
  }

  /* ── File artifact card ──────────────────────────────────────────── */
  function _renderFileCard(payload) {
    const panel = _getLogPanel();
    if (!panel) return;

    const path    = payload.path || payload.file || 'unknown';
    const action  = payload.action || 'modified';
    const status  = payload.status || 'healthy';
    const colorMap = { modified: '#f59e0b', created: '#3fb950', deleted: '#f85149' };
    const color   = colorMap[action] || '#8b949e';

    const card = document.createElement('div');
    card.className = 'nx-artifact-card';
    card.innerHTML = `
      <div class="nx-artifact-row">
        <span class="nx-artifact-icon" style="color:${color}">▣</span>
        <span class="nx-artifact-path">${_escHtml(path)}</span>
        <span class="nx-artifact-action" style="color:${color}">${action.toUpperCase()}</span>
        <span class="nx-artifact-status nx-artifact-status--${status}">${status}</span>
      </div>
    `;
    panel.appendChild(card);
    panel.scrollTop = panel.scrollHeight;
  }

  /* ── Persistent execution strip update ──────────────────────────── */
  function _updateExecStrip(kind) {
    const stripState = document.getElementById('nxExecStripState');
    if (!stripState) return;
    const stateMap = {
      think:        { label: 'REASONING', color: '#79c0ff' },
      action:       { label: 'EXECUTING', color: '#f59e0b' },
      tool_success: { label: 'TOOL OK',   color: '#3fb950' },
      output:       { label: 'STREAMING', color: '#bc8cff' },
    };
    const s = stateMap[kind];
    if (s) {
      stripState.textContent  = s.label;
      stripState.style.color  = s.color;
    }
  }

  /* ── Wire NxBus ──────────────────────────────────────────────────── */
  function _wire() {
    if (_initialized || !window.NxBus || !NxBus.EVENTS) { if (!_initialized && window.NxBus && !NxBus.EVENTS) setTimeout(_wire, 200); return; }
    _initialized = true;

    const E = NxBus.EVENTS;

    NxBus.on(E.STREAM_CHUNK, (d) => {
      const kind = d.kind || 'output';
      const text = d.text || d.output || d.message || '';
      const tool = d.tool || null;
      _ingest(kind, text, tool);
      _updateExecStrip(kind);
    }, { owner: 'nx-chunker' });

    NxBus.on(E.FILE_CHANGED, (d) => {
      _renderFileCard(d);
    }, { owner: 'nx-chunker' });

    NxBus.on(E.AGENT_DONE, () => {
      if (_activeGroup) {
        _sealGroup(_activeGroup);
        _activeGroup = null;
      }
      const stripState = document.getElementById('nxExecStripState');
      if (stripState) { stripState.textContent = 'COMPLETED'; stripState.style.color = '#3fb950'; }
    }, { owner: 'nx-chunker' });

    NxBus.on(E.AGENT_STOP, () => {
      if (_activeGroup) { _sealGroup(_activeGroup); _activeGroup = null; }
      const stripState = document.getElementById('nxExecStripState');
      if (stripState) { stripState.textContent = 'STOPPED'; stripState.style.color = '#f85149'; }
    }, { owner: 'nx-chunker' });

    NxBus.on(E.STREAM_ERROR, (d) => {
      _ingest('escalation', d.error || 'Runtime error', null);
      const stripState = document.getElementById('nxExecStripState');
      if (stripState) { stripState.textContent = 'ERROR'; stripState.style.color = '#f85149'; }
    }, { owner: 'nx-chunker' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wire);
  } else {
    // NxBus might not be ready yet — defer slightly
    setTimeout(_wire, 200);
  }

  window.NxChunker = { ingest: _ingest };

})();
