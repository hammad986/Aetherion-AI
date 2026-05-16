/**
 * nx-orchestrator.js — Nexora Cross-Surface Orchestration Layer v1
 * ═══════════════════════════════════════════════════════════════════
 * Single bus-to-surface bridge. All surfaces receive the same runtime truth.
 * No framework. No DOM rewrites. Surgical attribute/class mutations only.
 *
 * Surface registry:
 *   Timeline   → nxTab-logs  (nx-chunker owns rendering)
 *   Preview    → nxTab-preview (iframe inside, refresh + overlay)
 *   Terminal   → xtermStatus, xtermMount (class + status text)
 *   File Tree  → nxPanelContent-files (file items via data-path attr)
 *   Inspector  → nxInspectorContent (causality summaries)
 *   Exec Strip → nxExecStripState (Phase I, state text + color)
 *   Pipeline   → nxLogsPipeline, nxLivePipeline (stage activation)
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Surface refs (lazy-resolved) ────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const $q = (sel) => document.querySelector(sel);

  /* ── State ───────────────────────────────────────────────────────── */
  let _execActive      = false;
  let _previewOverlay  = null;
  let _initialized     = false;

  /* ══════════════════════════════════════════════════════════════════
     1. PIPELINE STAGE SYNC
     ══════════════════════════════════════════════════════════════════ */
  const STAGE_MAP = {
    'think':  'planning',
    'plan':   'planning',
    'action': 'coding',
    'tool_success': 'coding',
    'output': 'done',
    'validation': 'debugging',
    'recovery': 'debugging',
  };

  function _activateStage(kind) {
    const stage = STAGE_MAP[kind];
    if (!stage) return;
    // Both pipeline bars
    ['nxLogsPipeline', 'nxLivePipeline'].forEach(barId => {
      const bar = $(barId);
      if (!bar) return;
      bar.style.display = 'flex';
      bar.querySelectorAll('.nx-exec-stage').forEach(s => {
        s.classList.remove('active', 'done');
      });
      const stages = ['planning', 'coding', 'debugging', 'done'];
      const stageIdx = stages.indexOf(stage);
      bar.querySelectorAll('.nx-exec-stage').forEach((s, i) => {
        if (i < stageIdx) s.classList.add('done');
        if (i === stageIdx) s.classList.add('active');
      });
    });
  }

  function _clearPipeline() {
    ['nxLogsPipeline', 'nxLivePipeline'].forEach(barId => {
      const bar = $(barId);
      if (!bar) return;
      bar.querySelectorAll('.nx-exec-stage').forEach(s => {
        s.classList.remove('active', 'done');
      });
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     2. TERMINAL ORCHESTRATION
     ══════════════════════════════════════════════════════════════════ */
  function _syncTerminal(kind, text) {
    const status = $('xtermStatus');
    if (!status) return;

    const stateText = {
      'think': 'Reasoning...',
      'action': 'Executing action...',
      'tool_success': 'Tool completed',
      'output': 'Streaming output...',
      'validation': 'Validating...',
      'recovery': 'Recovering...',
    };
    if (stateText[kind]) {
      status.textContent = stateText[kind];
      status.style.color = kind === 'tool_success' ? '#3fb950'
        : kind === 'recovery' ? '#f59e0b'
        : 'var(--text-muted, #8b949e)';
    }

    // Mark terminal tab with execution pulse
    const termTab = $q('[data-nxtab="terminal"]');
    if (termTab) {
      termTab.classList.add('nx-tab-exec-active');
      // Auto-remove after idle
      clearTimeout(termTab._execTimer);
      termTab._execTimer = setTimeout(() => {
        termTab.classList.remove('nx-tab-exec-active');
      }, 4000);
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     3. FILE TREE ORCHESTRATION
     ══════════════════════════════════════════════════════════════════ */
  function _syncFileTree(payload) {
    const path   = payload.path || payload.file || '';
    const action = payload.action || 'modified';
    if (!path) return;

    const filename = path.split('/').pop();

    // Look for existing file item by data-path or title
    const fileTree = $('nxPanelContent-files');
    if (!fileTree) return;

    // Try to find by data-path first, then by text match
    let fileEl = fileTree.querySelector(`[data-path="${CSS.escape(path)}"]`)
      || Array.from(fileTree.querySelectorAll('.nx-file-item, .p3-file-item, [class*="file"]'))
           .find(el => el.textContent.includes(filename));

    if (fileEl) {
      // Mark existing
      _markFileEl(fileEl, action);
    } else {
      // Create transient artifact entry
      _insertTransientFile(fileTree, path, filename, action);
    }
  }

  function _markFileEl(el, action) {
    const colorMap = { modified: '#f59e0b', created: '#3fb950', deleted: '#f85149' };
    const color = colorMap[action] || '#8b949e';
    el.style.borderLeft = `2px solid ${color}`;
    el.dataset.execAction = action;
    el.title = `${action} by execution`;
    // Fade marker after 20s
    clearTimeout(el._execMarkTimer);
    el._execMarkTimer = setTimeout(() => {
      el.style.borderLeft = '';
      delete el.dataset.execAction;
    }, 20000);
  }

  function _insertTransientFile(tree, path, filename, action) {
    const colorMap = { modified: '#f59e0b', created: '#3fb950', deleted: '#f85149' };
    const color = colorMap[action] || '#8b949e';

    // Check if transient group exists
    let group = tree.querySelector('.nx-transient-group');
    if (!group) {
      group = document.createElement('div');
      group.className = 'nx-transient-group';
      group.innerHTML = '<div class="nx-transient-label">EXECUTION FILES</div>';
      tree.appendChild(group);
    }

    // Avoid duplicates
    if (group.querySelector(`[data-path="${path}"]`)) return;

    const item = document.createElement('div');
    item.className  = 'nx-transient-file';
    item.dataset.path = path;
    item.innerHTML  = `<span style="color:${color};font-size:9px;">▣</span> <span>${filename}</span> <span class="nx-transient-action" style="color:${color}">${action}</span>`;
    group.appendChild(item);

    // Auto-remove after 60s
    setTimeout(() => { if (item.isConnected) item.remove(); }, 60000);
  }

  /* ══════════════════════════════════════════════════════════════════
     4. PREVIEW ORCHESTRATION
     ══════════════════════════════════════════════════════════════════ */
  function _getPreviewIframe() {
    const tab = $('nxTab-preview');
    if (!tab) return null;
    return tab.querySelector('iframe');
  }

  function _ensurePreviewOverlay() {
    const tab = $('nxTab-preview');
    if (!tab) return null;
    if (_previewOverlay && _previewOverlay.isConnected) return _previewOverlay;
    _previewOverlay = document.createElement('div');
    _previewOverlay.id = 'nxPreviewOverlay';
    _previewOverlay.className = 'nx-preview-overlay';
    tab.style.position = 'relative';
    tab.appendChild(_previewOverlay);
    return _previewOverlay;
  }

  function _setPreviewState(state) {
    const overlay = _ensurePreviewOverlay();
    if (!overlay) return;

    const states = {
      validating: { label: 'VALIDATING', color: '#f59e0b', dot: 'validating' },
      building:   { label: 'BUILDING',   color: '#79c0ff', dot: 'building'   },
      generating: { label: 'GENERATING', color: '#bc8cff', dot: 'building'   },
      retrying:   { label: 'RETRYING',   color: '#f59e0b', dot: 'validating' },
      healthy:    { label: 'HEALTHY',    color: '#3fb950', dot: ''            },
      degraded:   { label: 'DEGRADED',   color: '#f59e0b', dot: 'degraded'   },
      failed:     { label: 'FAILED',     color: '#f85149', dot: 'failed'     },
    };

    const s = states[state];
    if (!s) { overlay.style.display = 'none'; return; }

    if (state === 'healthy') {
      overlay.style.display = 'none';
      return;
    }

    overlay.style.display = 'flex';
    overlay.innerHTML = `
      <div class="nx-preview-dot ${s.dot}"></div>
      <span style="color:${s.color};font-size:10px;font-weight:700;letter-spacing:0.06em;">${s.label}</span>
    `;
  }

  /* ══════════════════════════════════════════════════════════════════
     5. INSPECTOR CAUSALITY LAYER
     ══════════════════════════════════════════════════════════════════ */
  let _inspectorBuffer = [];
  let _inspectorFlushTimer = null;

  function _appendToInspector(kind, text, extra) {
    _inspectorBuffer.push({ kind, text, extra, ts: Date.now() });
    clearTimeout(_inspectorFlushTimer);
    _inspectorFlushTimer = setTimeout(_flushInspector, 800);
  }

  function _flushInspector() {
    const panel = $('nxInspectorContent');
    if (!panel || _inspectorBuffer.length === 0) return;

    // Group buffer by kind
    const groups = {};
    _inspectorBuffer.forEach(entry => {
      if (!groups[entry.kind]) groups[entry.kind] = [];
      groups[entry.kind].push(entry);
    });
    _inspectorBuffer = [];

    const LABELS = {
      think:        { label: 'Reasoning', color: '#79c0ff' },
      action:       { label: 'Action Taken', color: '#f59e0b' },
      tool_success: { label: 'Tool Result', color: '#3fb950' },
      validation:   { label: 'Validation', color: '#3fb950' },
      recovery:     { label: 'Recovery Cause', color: '#f59e0b' },
      escalation:   { label: 'Escalation', color: '#f85149' },
    };

    Object.entries(groups).forEach(([kind, entries]) => {
      const m = LABELS[kind] || { label: kind, color: '#8b949e' };
      const section = document.createElement('div');
      section.className = 'nx-inspector-section';
      const latest = entries[entries.length - 1];
      const truncated = latest.text ? latest.text.slice(0, 280) : '';
      section.innerHTML = `
        <div class="nx-inspector-section-label" style="color:${m.color}">${m.label}</div>
        <div class="nx-inspector-section-body">${_esc(truncated)}${truncated.length >= 280 ? '…' : ''}</div>
      `;
      panel.insertBefore(section, panel.firstChild);
    });

    // Cap inspector to 12 sections to prevent accumulation
    const sections = panel.querySelectorAll('.nx-inspector-section');
    if (sections.length > 12) {
      Array.from(sections).slice(12).forEach(s => s.remove());
    }
  }

  function _esc(s) {
    return String(s || '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  /* ══════════════════════════════════════════════════════════════════
     6. TAB DOT INDICATOR
     ══════════════════════════════════════════════════════════════════ */
  function _pulseTab(tabName) {
    const dot = $q(`[data-nxtab="${tabName}"] .nx-tab-dot`);
    if (!dot) return;
    dot.classList.add('nx-tab-dot-active');
    clearTimeout(dot._pulseTimer);
    dot._pulseTimer = setTimeout(() => dot.classList.remove('nx-tab-dot-active'), 3000);
  }

  /* ══════════════════════════════════════════════════════════════════
     7. MAIN NxBus WIRING
     ══════════════════════════════════════════════════════════════════ */
  function _wire() {
    if (_initialized || !window.NxBus) return;
    _initialized = true;
    const E = NxBus.EVENTS;

    /* ── STREAM_CHUNK: sync all active surfaces ── */
    NxBus.on(E.STREAM_CHUNK, (d) => {
      const kind = d.kind || 'output';
      const text = d.text || d.output || '';

      _execActive = true;
      _activateStage(kind);
      _syncTerminal(kind, text);

      // Inspector: buffer reasoning + action + validation chunks
      if (['think','action','validation','recovery'].includes(kind)) {
        _appendToInspector(kind, text, d);
      }

      // Preview: mark as generating during execution
      if (kind === 'action' || kind === 'tool_success') {
        _setPreviewState('validating');
        _pulseTab('preview');
      }

      // Pulse logs tab
      _pulseTab('logs');
    }, { owner: 'nx-orchestrator' });

    /* ── FILE_CHANGED: sync file tree ── */
    NxBus.on(E.FILE_CHANGED, (d) => {
      _syncFileTree(d);
      _pulseTab('code');

      // Inspector: note the file change causality
      _appendToInspector('tool_success', `File ${d.action || 'modified'}: ${d.path || ''}`, d);
    }, { owner: 'nx-orchestrator' });

    /* ── AGENT_DONE ── */
    NxBus.on(E.AGENT_DONE, (d) => {
      _execActive = false;
      _clearPipeline();
      _setPreviewState('healthy');

      const status = $('xtermStatus');
      if (status) { status.textContent = 'Execution complete'; status.style.color = '#3fb950'; }

      if (d && d.confidence != null) {
        _appendToInspector('validation',
          `Task completed. Confidence: ${Math.round(d.confidence * 100)}%`, d);
      }
    }, { owner: 'nx-orchestrator' });

    /* ── AGENT_STOP ── */
    NxBus.on(E.AGENT_STOP, () => {
      _execActive = false;
      _clearPipeline();
      _setPreviewState('degraded');
      const status = $('xtermStatus');
      if (status) { status.textContent = 'Stopped'; status.style.color = '#f85149'; }
    }, { owner: 'nx-orchestrator' });

    /* ── STREAM_ERROR ── */
    NxBus.on(E.STREAM_ERROR, (d) => {
      _setPreviewState('failed');
      _appendToInspector('escalation', d.error || 'Execution error', d);
      const status = $('xtermStatus');
      if (status) { status.textContent = 'Error'; status.style.color = '#f85149'; }
    }, { owner: 'nx-orchestrator' });

    /* ── SESSION continuity ── */
    NxBus.on(E.SESSION_RESTORED, (d) => {
      _setPreviewState('healthy');
      const status = $('xtermStatus');
      if (status) { status.textContent = 'Session restored'; status.style.color = '#8b949e'; }
    }, { owner: 'nx-orchestrator' });

    /* ── SSE reconnect → surfaces reflect recovering state ── */
    NxBus.on(E.WS_STATUS, (d) => {
      if (d.state === 'reconnecting') {
        _setPreviewState('retrying');
        const status = $('xtermStatus');
        if (status) { status.textContent = 'Reconnecting...'; status.style.color = '#f59e0b'; }
      } else if (d.state === 'connected' && _execActive) {
        _setPreviewState('validating');
      }
    }, { owner: 'nx-orchestrator' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 300));
  } else {
    setTimeout(_wire, 300);
  }

  window.NxOrchestrator = { setPreviewState: _setPreviewState };

})();
