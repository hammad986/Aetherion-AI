/**
 * nx-activity.js — Nexora Activity Layer v1
 * ═══════════════════════════════════════════════════════════════════════
 * Modules (each self-contained IIFE, no external deps beyond window.NX):
 *
 *   1. NxStatusBar   — lightweight bottom status strip
 *   2. NxActivity    — AI activity timeline with batched DOM updates
 *   3. NxInspector   — contextual right-panel mode switcher
 *   4. NxSnapshots   — save/restore workspace layout snapshots
 *
 * All use NX_BOOT_TASKS for deterministic init ordering.
 * Zero heavy animation — CSS handles all transitions.
 * ═══════════════════════════════════════════════════════════════════════
 */

/* ═══════════════════════════════════════════════════════════════════════
   1. NxStatusBar — bottom 22px status strip
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── HTML template injected before </body> ────────────────────────────
  const BAR_ID = 'nxStatusBar';
  const ITEMS = {
    ws:      'nxSbWs',
    model:   'nxSbModel',
    task:    'nxSbTask',
    stream:  'nxSbStream',
    bg:      'nxSbBg',
  };

  function _inject() {
    if (document.getElementById(BAR_ID)) return;
    const bar = document.createElement('div');
    bar.id = BAR_ID;
    bar.innerHTML =
      `<div class="nx-sb-item" id="${ITEMS.ws}" title="WebSocket / SSE status">
        <span class="nx-sb-dot" id="nxSbWsDot"></span>
        <span id="nxSbWsText">Connecting…</span>
       </div>
       <div class="nx-sb-item" id="${ITEMS.model}" title="Active model">
        <span id="nxSbModelText">—</span>
       </div>
       <div class="nx-sb-item" id="${ITEMS.task}" title="Task state">
        <span id="nxSbTaskText">Idle</span>
       </div>
       <div class="nx-sb-item" id="${ITEMS.stream}" title="Streaming" style="display:none">
        <span class="nx-sb-dot pulse green" id="nxSbStreamDot"></span>
        <span id="nxSbStreamText">Streaming…</span>
       </div>
       <div class="nx-sb-spacer"></div>
       <div class="nx-sb-item nx-sb-right" id="${ITEMS.bg}" title="Background activity" style="display:none">
        <span id="nxSbBgText"></span>
       </div>`;

    // Insert as last child of body, after .nx-body
    document.body.appendChild(bar);
  }

  // ── Public API ───────────────────────────────────────────────────────
  const NxStatusBar = {
    setWs(state) {  // 'connected' | 'disconnected' | 'error'
      const dot  = document.getElementById('nxSbWsDot');
      const text = document.getElementById('nxSbWsText');
      if (!dot || !text) return;
      const map = {
        connected:    { cls: 'green',  label: 'Connected' },
        disconnected: { cls: '',       label: 'Disconnected' },
        error:        { cls: 'red',    label: 'Error' },
        connecting:   { cls: 'yellow', label: 'Connecting…' },
      };
      const cfg = map[state] || map.connecting;
      dot.className = 'nx-sb-dot ' + cfg.cls;
      text.textContent = cfg.label;
    },

    setModel(name) {
      const el = document.getElementById('nxSbModelText');
      if (el) el.textContent = name || '—';
    },

    setTask(state, detail) {
      const el = document.getElementById('nxSbTaskText');
      if (!el) return;
      const labels = {
        idle:     'Idle',
        running:  detail ? `Running — ${detail}` : 'Running',
        done:     'Done',
        error:    'Error',
        stopped:  'Stopped',
        planning: 'Planning…',
        thinking: 'Thinking…',
      };
      el.textContent = labels[state] || state;
    },

    setStreaming(active, hint) {
      const wrap = document.getElementById(ITEMS.stream);
      const text = document.getElementById('nxSbStreamText');
      if (!wrap) return;
      wrap.style.display = active ? '' : 'none';
      if (text) text.textContent = hint || 'Streaming…';
    },

    setBg(text) {
      const wrap = document.getElementById(ITEMS.bg);
      const label = document.getElementById('nxSbBgText');
      if (!wrap) return;
      if (text) {
        wrap.style.display = '';
        if (label) label.textContent = text;
      } else {
        wrap.style.display = 'none';
      }
    },
  };

  window.NxStatusBar = NxStatusBar;

  // ── Wire into existing runtime events ────────────────────────────────
  function _hookRuntime() {
    // Patch nxSetStatus if it exists
    const origSetStatus = window.nxSetStatus;
    window.nxSetStatus = function(s, detail) {
      if (origSetStatus) origSetStatus(s, detail);
      const taskMap = { running: 'running', idle: 'idle', error: 'error', done: 'done', stopped: 'stopped' };
      NxStatusBar.setTask(taskMap[s] || s, detail);
    };

    // Watch NX.model for changes (simple polling — cheap)
    let _lastModel = '';
    setInterval(() => {
      const m = window.NX && (window.NX.model || window.NX.activeModel || '');
      if (m && m !== _lastModel) { _lastModel = m; NxStatusBar.setModel(m); }
    }, 2000);

    // Detect EventSource / WebSocket state — best effort
    const origES = window.EventSource;
    if (origES) {
      window.EventSource = function(url, cfg) {
        const es = new origES(url, cfg);
        NxStatusBar.setWs('connecting');
        es.addEventListener('open',  () => NxStatusBar.setWs('connected'));
        es.addEventListener('error', () => NxStatusBar.setWs('error'));
        return es;
      };
      Object.assign(window.EventSource, origES);
    }
  }

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_inject);
    window.NX_BOOT_TASKS.push(_hookRuntime);
  } else {
    document.addEventListener('DOMContentLoaded', () => { _inject(); _hookRuntime(); });
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   2. NxActivity — AI activity timeline
   Renders into #nxActivityTimeline (inside left panel or bottom dock)
   Uses a 60ms batched flush to avoid DOM spam during fast events.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const MAX_ENTRIES = 80;   // keep DOM lean
  const FLUSH_MS    = 60;   // batch window

  // Entry type → icon text + CSS class
  const TYPE_META = {
    think:   { icon: '◉', cls: 'think',  label: 'Thinking' },
    plan:    { icon: '◈', cls: 'plan',   label: 'Planning' },
    tool:    { icon: '⚙', cls: 'tool',   label: 'Tool' },
    file:    { icon: '✎', cls: 'file',   label: 'File' },
    run:     { icon: '▶', cls: 'run',    label: 'Running' },
    error:   { icon: '✕', cls: 'error',  label: 'Error' },
    wait:    { icon: '◌', cls: 'wait',   label: 'Waiting' },
    model:   { icon: '◆', cls: 'think',  label: 'Model' },
    default: { icon: '·', cls: 'wait',   label: '' },
  };

  let _queue   = [];     // pending entries
  let _timer   = null;   // batch timer handle
  let _count   = 0;      // total rendered

  function _container() {
    return document.getElementById('nxActivityTimeline');
  }

  function _fmt(text, maxLen = 60) {
    if (!text) return '';
    const s = String(text).trim();
    return s.length > maxLen ? s.slice(0, maxLen) + '…' : s;
  }

  function _timeStr() {
    const d = new Date();
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }

  function _flush() {
    _timer = null;
    if (!_queue.length) return;
    const container = _container();
    if (!container) { _queue = []; return; }

    // Build fragment for batch insert
    const frag = document.createDocumentFragment();
    _queue.forEach(entry => {
      const meta = TYPE_META[entry.type] || TYPE_META.default;
      const el = document.createElement('div');
      el.className = 'nx-at-entry';
      el.innerHTML =
        `<div class="nx-at-icon ${meta.cls}">${meta.icon}</div>
         <div class="nx-at-body">
           <div class="nx-at-label">${meta.label}${entry.label ? ` — ${entry.label}` : ''}</div>
           ${entry.detail ? `<div class="nx-at-detail">${_fmt(entry.detail)}</div>` : ''}
         </div>
         <div class="nx-at-time">${entry.time}</div>`;
      frag.appendChild(el);
      _count++;
    });
    _queue = [];

    container.appendChild(frag);

    // Trim old entries if over max
    while (container.children.length > MAX_ENTRIES) {
      container.removeChild(container.firstChild);
    }

    // Scroll to bottom (requestAnimationFrame avoids layout thrash)
    requestAnimationFrame(() => {
      if (container.scrollHeight - container.scrollTop < container.clientHeight + 120) {
        container.scrollTop = container.scrollHeight;
      }
    });
  }

  function _push(entry) {
    _queue.push({ ...entry, time: _timeStr() });
    if (!_timer) _timer = setTimeout(_flush, FLUSH_MS);
  }

  // ── Public API ───────────────────────────────────────────────────────
  const NxActivity = {
    think(label, detail)  { _push({ type: 'think', label, detail }); },
    plan(label, detail)   { _push({ type: 'plan',  label, detail }); },
    tool(name, detail)    { _push({ type: 'tool',  label: name, detail }); },
    file(path, action)    { _push({ type: 'file',  label: action || 'edit', detail: path }); },
    run(cmd, detail)      { _push({ type: 'run',   label: cmd,  detail }); },
    error(msg, detail)    { _push({ type: 'error', label: msg,  detail }); },
    wait(reason)          { _push({ type: 'wait',  label: reason }); },
    model(name, detail)   { _push({ type: 'model', label: name, detail }); },
    log(type, label, det) { _push({ type: type || 'default', label, detail: det }); },
    clear() {
      _queue = [];
      const c = _container();
      if (c) c.innerHTML = '';
      _count = 0;
    },
    count() { return _count; },
  };

  window.NxActivity = NxActivity;

  // ── Wire into existing log/streaming events ──────────────────────────
  function _hookStreams() {
    // Intercept nxLog if available
    const origLog = window.nxLog;
    if (origLog && typeof origLog === 'function') {
      window.nxLog = function(type, msg, detail) {
        origLog(type, msg, detail);
        // Map known log types to activity events
        const map = {
          thought: 'think', decision: 'plan', tool_call: 'tool',
          file_write: 'file', command: 'run', error: 'error',
        };
        const at = map[type];
        if (at) NxActivity.log(at, msg, detail);
      };
    }

    // Listen for custom events dispatched by runtime
    document.addEventListener('nx:activity', e => {
      const d = e.detail || {};
      NxActivity.log(d.type || 'default', d.label, d.detail);
    });

    // Listen for SSE log events
    document.addEventListener('nx:log', e => {
      const d = e.detail || {};
      if (d.type) NxActivity.log(d.type, d.msg || d.label, d.detail);
    });

    // Watch NxStatusBar for streaming state changes
    if (window.NxStatusBar) {
      const orig = NxStatusBar.setStreaming;
      NxStatusBar.setStreaming = function(active, hint) {
        orig.call(NxStatusBar, active, hint);
        if (active) NxActivity.wait(hint || 'Waiting for model…');
      };
    }
  }

  // ── Inject container into left panel if not present ──────────────────
  function _injectContainer() {
    if (document.getElementById('nxActivityTimeline')) return;

    // Prefer left panel body
    const leftBody = document.getElementById('nxLeftBody');
    if (!leftBody) return;

    const wrap = document.createElement('div');
    wrap.style.cssText = 'border-top:1px solid var(--panel-border);margin-top:8px;';
    wrap.innerHTML = `
      <div class="nx-think-label" style="padding:8px 12px 4px;display:flex;align-items:center;justify-content:space-between;">
        <span>Activity</span>
        <button onclick="NxActivity.clear()" style="background:none;border:none;cursor:pointer;font-size:9px;color:var(--text-dim);padding:2px 4px;border-radius:3px;" title="Clear activity">clear</button>
      </div>
      <div id="nxActivityTimeline" style="max-height:260px;overflow-y:auto;"></div>`;
    leftBody.appendChild(wrap);
  }

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_injectContainer);
    window.NX_BOOT_TASKS.push(_hookStreams);
  } else {
    document.addEventListener('DOMContentLoaded', () => { _injectContainer(); _hookStreams(); });
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   3. NxInspector — Contextual right-panel mode
   Listens for tab changes and renders context-specific tool sections
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const MODES = {
    preview: {
      label: 'Preview Tools',
      sections: [
        {
          title: 'Preview',
          actions: [
            { icon: '↺', label: 'Refresh Preview', fn: () => { const f = document.getElementById('nxPreviewFrame'); if (f) f.src = f.src; } },
            { icon: '⛶', label: 'Open in New Tab', fn: () => { const f = document.getElementById('nxPreviewFrame'); if (f) window.open(f.src); } },
          ],
        },
      ],
    },
    code: {
      label: 'Code Tools',
      sections: [
        {
          title: 'File',
          actions: [
            { icon: '💾', label: 'Save File',    fn: () => typeof saveCurrentFile === 'function' && saveCurrentFile() },
            { icon: '▶',  label: 'Run File',     fn: () => typeof nxRunCurrentFile === 'function' && nxRunCurrentFile() },
            { icon: '⚙',  label: 'Format Code',  fn: () => typeof nxFormatCode === 'function' && nxFormatCode() },
          ],
        },
      ],
    },
    logs: {
      label: 'Agent Tools',
      sections: [
        {
          title: 'Agent Control',
          rows: [
            { label: 'Status',  valId: 'nxCurStatus' },
            { label: 'Session', valId: 'nxCurSid'    },
          ],
          actions: [
            { icon: '⏹', label: 'Stop Agent',      fn: () => typeof stopSession === 'function' && stopSession() },
            { icon: '◉', label: 'View Reasoning',  fn: () => typeof nxSetTab === 'function' && nxSetTab('steps') },
          ],
        },
      ],
    },
    terminal: {
      label: 'Debug Tools',
      sections: [
        {
          title: 'Diagnostics',
          actions: [
            { icon: '📋', label: 'View Logs',    fn: () => typeof nxSetTab === 'function' && nxSetTab('logs') },
            { icon: '📊', label: 'View Metrics', fn: () => typeof nxSetTab === 'function' && nxSetTab('metrics') },
          ],
        },
      ],
    },
  };

  const INSP_ID = 'nxContextInspector';

  function _buildHTML(mode) {
    const def = MODES[mode] || MODES.logs;
    let html = '';
    (def.sections || []).forEach(sec => {
      html += `<div class="nx-insp-section">
        <div class="nx-insp-section-title">${sec.title}</div>`;
      if (sec.rows) {
        sec.rows.forEach(row => {
          const val = document.getElementById(row.valId);
          const v = val ? val.textContent.trim() : '—';
          html += `<div class="nx-insp-row">
            <span class="nx-insp-label">${row.label}</span>
            <span class="nx-insp-val">${v}</span>
          </div>`;
        });
      }
      html += `</div>`;
      if (sec.actions && sec.actions.length) {
        html += `<div class="nx-insp-actions">`;
        sec.actions.forEach((a, i) => {
          html += `<button class="nx-insp-btn" data-insp-action="${mode}-${i}">${a.icon} ${a.label}</button>`;
        });
        html += `</div>`;
      }
    });
    return html;
  }

  function _render(tabId) {
    const container = document.getElementById(INSP_ID);
    if (!container) return;
    container.innerHTML = _buildHTML(tabId || 'logs');

    // Wire action buttons
    const def = MODES[tabId] || MODES.logs;
    (def.sections || []).forEach(sec => {
      (sec.actions || []).forEach((a, i) => {
        const btn = container.querySelector(`[data-insp-action="${tabId}-${i}"]`);
        if (btn) btn.addEventListener('click', a.fn);
      });
    });
  }

  function _injectContainer() {
    if (document.getElementById(INSP_ID)) return;
    const rightBody = document.getElementById('nxRightBody') ||
                      document.querySelector('.nx-right .nx-panel-body');
    if (!rightBody) return;

    const wrap = document.createElement('div');
    wrap.id = INSP_ID;
    wrap.style.cssText = 'border-top:1px solid var(--panel-border);margin-top:4px;';
    rightBody.appendChild(wrap);
    _render('logs');
  }

  function _hookTabChange() {
    // Patch nxSetTab to notify inspector
    const orig = window.nxSetTab;
    if (typeof orig === 'function') {
      window.nxSetTab = function(tabId) {
        orig(tabId);
        _render(tabId);
      };
    }
    // Also respond to custom events
    document.addEventListener('nx:tabchange', e => _render(e.detail && e.detail.tab));
  }

  window.NxInspector = { render: _render, MODES };

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_injectContainer);
    window.NX_BOOT_TASKS.push(_hookTabChange);
  } else {
    document.addEventListener('DOMContentLoaded', () => { _injectContainer(); _hookTabChange(); });
  }
})();


/* ═══════════════════════════════════════════════════════════════════════
   4. NxSnapshots — Save / restore named workspace snapshots
   Stored in localStorage under 'nx_snapshots_v1'
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const STORE_KEY = 'nx_snapshots_v1';
  const MAX_SNAPS = 10;

  function _load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch(_) { return []; }
  }
  function _save(snaps) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(snaps)); } catch(_) {}
  }

  function capture(name) {
    if (!window.NxWorkspace) return null;
    const ws   = window.NxWorkspace.getState();
    const snap = {
      id:        Date.now(),
      name:      name || `Snapshot ${new Date().toLocaleTimeString()}`,
      ts:        Date.now(),
      ws:        { ...ws },
      activeTab: (window.NX && window.NX.activeTab) || 'logs',
    };
    const snaps = _load();
    snaps.unshift(snap);
    if (snaps.length > MAX_SNAPS) snaps.length = MAX_SNAPS;
    _save(snaps);
    if (typeof nxToast === 'function') nxToast(`📸 Snapshot saved: ${snap.name}`);
    _refreshChips();
    return snap;
  }

  function restore(id) {
    const snaps = _load();
    const snap  = snaps.find(s => s.id === id);
    if (!snap || !window.NxWorkspace) return false;

    const ws = window.NxWorkspace;
    const s  = snap.ws;
    document.body.classList.add('nx-preset-transitioning');

    if (typeof s.leftW  !== 'undefined') ws.setLeftWidth(s.leftW);
    if (typeof s.rightW !== 'undefined') ws.setRightWidth(s.rightW);
    if (s.closedTabs) {
      // Restore all first
      const all = ['preview','code','live','terminal','metrics','agents','timeline','steps','learning','goals','graph','scheduler'];
      all.forEach(t => { if (!ws.isTabOpen(t)) ws.openTab(t); });
      s.closedTabs.forEach(t => ws.closeTab(t));
    }
    if (snap.activeTab && typeof nxSetTab === 'function') nxSetTab(snap.activeTab);

    setTimeout(() => document.body.classList.remove('nx-preset-transitioning'), 500);
    if (typeof nxToast === 'function') nxToast(`↩ Restored: ${snap.name}`);
    return true;
  }

  function deleteSnap(id) {
    const snaps = _load().filter(s => s.id !== id);
    _save(snaps);
    _refreshChips();
  }

  function list() { return _load(); }

  // ── Inject snapshot chip row into More dropdown ──────────────────────
  function _refreshChips() {
    const dd = document.getElementById('nxMoreDropdown');
    if (!dd) return;

    // Remove old snapshot section
    dd.querySelectorAll('[data-snap-section]').forEach(el => el.remove());

    const snaps = _load();
    if (!snaps.length) return;

    const sep = document.createElement('div');
    sep.dataset.snapSection = '1';
    sep.style.cssText = 'border-top:1px solid var(--panel-border);margin:3px 0;padding:4px 10px;font-size:9px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em;pointer-events:none';
    sep.textContent = 'Snapshots';
    dd.appendChild(sep);

    snaps.slice(0, 5).forEach(snap => {
      const item = document.createElement('div');
      item.className = 'nx-more-item';
      item.dataset.snapSection = '1';
      item.innerHTML = `<span style="opacity:.55">📸</span>${snap.name}<span style="float:right;font-size:10px;color:var(--text-dim);cursor:pointer" data-del="${snap.id}">✕</span>`;
      item.addEventListener('click', e => {
        if (e.target.dataset.del) { e.stopPropagation(); deleteSnap(Number(e.target.dataset.del)); return; }
        restore(snap.id);
        if (typeof nxCloseMore === 'function') nxCloseMore();
      });
      dd.appendChild(item);
    });

    // Save current button
    const saveBtn = document.createElement('div');
    saveBtn.className = 'nx-more-item';
    saveBtn.dataset.snapSection = '1';
    saveBtn.innerHTML = '<span style="opacity:.55">+</span> Save Snapshot';
    saveBtn.addEventListener('click', () => {
      capture();
      if (typeof nxCloseMore === 'function') nxCloseMore();
    });
    dd.appendChild(saveBtn);
  }

  // Keyboard shortcut: Ctrl+Shift+S = save snapshot
  document.addEventListener('keydown', e => {
    if (e.ctrlKey && e.shiftKey && e.key === 'S') {
      e.preventDefault();
      capture();
    }
  });

  window.NxSnapshots = { capture, restore, delete: deleteSnap, list };
  window.nxCaptureSnapshot = capture;
  window.nxRestoreSnapshot = restore;

  if (Array.isArray(window.NX_BOOT_TASKS)) {
    window.NX_BOOT_TASKS.push(_refreshChips);
  } else {
    document.addEventListener('DOMContentLoaded', _refreshChips);
  }
})();
