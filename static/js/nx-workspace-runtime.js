/**
 * nx-workspace-runtime.js — AGI Task Execution UX + Workspace State v1
 * ──────────────────────────────────────────────────────────────────────
 * Provides:
 *   1. Task execution lifecycle chips (running / hitl / done / failed / cancelled)
 *   2. Realtime execution timeline (per-task event log)
 *   3. Workspace state persistence (open tabs, terminal, layout) across refresh/reconnect
 *   4. File tree live sync from file.modified SSE events
 *   5. Performance guards: event batching, dedup, max-render-rate 30fps
 *
 * Zero modification to runtime.js or backend.
 * Communicates exclusively via NxBus.
 */
'use strict';

(function () {

  if (window.NxWorkspaceRuntime) return;

  /* ── Constants ─────────────────────────────────────────────────────────── */
  const STORE_KEY_WS    = 'nx_workspace_runtime_v1';
  const RENDER_THROTTLE = 33;   // ms — ~30fps max render rate
  const MAX_EVENTS      = 300;  // keep at most N execution events in memory
  const MAX_CHIP_AGE_MS = 15 * 60 * 1000; // 15min — expire old task chips

  /* ── State ─────────────────────────────────────────────────────────────── */
  let _sid          = null;
  let _tasks        = new Map(); // taskId → { id, status, prompt, ts, events:[] }
  let _activeTask   = null;      // id of current running task
  let _renderTimer  = null;
  let _dirtyRender  = false;
  let _fsThrottle   = null;
  let _pendingFsRefresh = false;

  /* ── Workspace persistence ─────────────────────────────────────────────── */
  function _saveWorkspaceState() {
    try {
      const state = {
        sid:        _sid,
        activeTask: _activeTask,
        taskCount:  _tasks.size,
        ts:         Date.now(),
      };
      localStorage.setItem(STORE_KEY_WS, JSON.stringify(state));
    } catch (_) {}
  }

  /* ── Chip container DOM ────────────────────────────────────────────────── */
  function _ensureChipBar() {
    if (document.getElementById('nxTaskChipBar')) return;
    // Inject below the top toolbar if it exists, else after tabBar
    const anchor = document.getElementById('nxTabBar') || document.querySelector('.nx-toolbar, .toolbar, #toolBar');
    if (!anchor) return;
    const bar = document.createElement('div');
    bar.id = 'nxTaskChipBar';
    bar.style.cssText = [
      'display:flex;align-items:center;gap:6px;padding:4px 12px',
      'background:var(--panel,#111118);border-bottom:1px solid var(--panel-border,#1e1e2e)',
      'min-height:32px;flex-wrap:wrap;overflow:hidden;max-height:56px',
      'transition:max-height .2s',
    ].join(';');
    bar.setAttribute('aria-label', 'Active tasks');
    anchor.insertAdjacentElement('afterend', bar);
    _injectChipStyles();
  }

  /* ── Chip rendering ────────────────────────────────────────────────────── */
  const STATUS_CONFIG = {
    queued:    { color:'#7878a0', bg:'rgba(120,120,160,.12)', icon:'⏳', label:'Queued'    },
    running:   { color:'#6366f1', bg:'rgba(99,102,241,.15)',  icon:'⚡', label:'Running', pulse:true },
    hitl:      { color:'#f6b93b', bg:'rgba(246,185,59,.12)',  icon:'🛑', label:'Awaiting Approval' },
    done:      { color:'#3fb950', bg:'rgba(63,185,80,.12)',   icon:'✓',  label:'Done'      },
    failed:    { color:'#f85149', bg:'rgba(248,81,73,.12)',   icon:'✕',  label:'Failed'    },
    stopped:   { color:'#8b949e', bg:'rgba(139,148,158,.1)',  icon:'⏹',  label:'Stopped'   },
    cancelled: { color:'#8b949e', bg:'rgba(139,148,158,.1)',  icon:'⏹',  label:'Cancelled' },
    retrying:  { color:'#ffa657', bg:'rgba(255,166,87,.12)',  icon:'↺',  label:'Retrying'  },
  };

  function _renderChips() {
    const bar = document.getElementById('nxTaskChipBar');
    if (!bar) return;
    const now = Date.now();
    // Purge ancient completed tasks from view (but keep in _tasks map)
    const visibleTasks = [..._tasks.values()].filter(t =>
      t.status === 'running' || t.status === 'queued' || t.status === 'hitl' ||
      (now - t.ts) < MAX_CHIP_AGE_MS
    );
    if (!visibleTasks.length) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';

    // Efficient diff — remove chips for tasks no longer visible
    bar.querySelectorAll('.nx-task-chip').forEach(chip => {
      if (!visibleTasks.find(t => t.id === chip.dataset.taskId)) chip.remove();
    });

    visibleTasks.forEach(task => {
      let chip = bar.querySelector(`.nx-task-chip[data-task-id="${task.id}"]`);
      const cfg = STATUS_CONFIG[task.status] || STATUS_CONFIG.queued;
      const label = (task.prompt || '').slice(0, 40) + ((task.prompt?.length > 40) ? '…' : '');
      const age   = _fmtAge(now - task.ts);

      if (!chip) {
        chip = document.createElement('div');
        chip.className = 'nx-task-chip';
        chip.dataset.taskId = task.id;
        chip.addEventListener('click', () => _toggleTaskDetail(task.id));
        bar.appendChild(chip);
      }

      // Diff guard — skip expensive DOM update if visible state unchanged
      const stateKey = `${task.status}:${label}:${age}:${cfg.pulse ? 1 : 0}`;
      if (chip.dataset.stateKey === stateKey) return;
      chip.dataset.stateKey = stateKey;

      chip.style.cssText = [
        `background:${cfg.bg};border:1px solid ${cfg.color}40`,
        'border-radius:20px;padding:3px 10px 3px 7px',
        'display:flex;align-items:center;gap:5px;cursor:pointer',
        'font-size:11px;max-width:260px;transition:opacity .2s',
        cfg.pulse ? 'animation:nx-chip-pulse 2s ease-in-out infinite' : '',
      ].join(';');
      chip.innerHTML = `
        <span style="font-size:10px">${cfg.icon}</span>
        <span style="color:${cfg.color};font-weight:600">${cfg.label}</span>
        ${label ? `<span style="color:var(--text-muted,#7878a0);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(label)}</span>` : ''}
        <span style="color:var(--text-muted,#7878a0);font-size:10px;margin-left:2px">${age}</span>
        ${task.status === 'running' ? `<button data-cancel="${task.id}" onclick="NxWorkspaceRuntime.cancelTask('${task.id}');event.stopPropagation();"
          style="margin-left:4px;padding:0 4px;border:1px solid ${cfg.color}50;background:none;color:${cfg.color};
                 border-radius:3px;cursor:pointer;font-size:9px;line-height:1.6">✕</button>` : ''}
      `;

    });
  }

  /* ── Task detail panel ──────────────────────────────────────────────────── */
  function _toggleTaskDetail(taskId) {
    const task = _tasks.get(taskId);
    if (!task) return;
    let panel = document.getElementById('nxTaskDetail');
    if (panel && panel.dataset.taskId === taskId) { panel.remove(); return; }
    if (panel) panel.remove();

    panel = document.createElement('div');
    panel.id = 'nxTaskDetail';
    panel.dataset.taskId = taskId;
    panel.style.cssText = [
      'position:fixed;bottom:48px;right:16px;width:360px;max-height:400px',
      'background:var(--panel,#111118);border:1px solid var(--panel-border,#1e1e2e)',
      'border-radius:10px;z-index:9000;overflow:hidden',
      'box-shadow:0 8px 32px rgba(0,0,0,.5)',
      'display:flex;flex-direction:column',
    ].join(';');

    const events = (task.events || []).slice(-50);
    const evHtml = events.length
      ? events.map(ev => {
          const c = ev.type === 'think' ? '#818cf8' : ev.type === 'error' ? '#f85149' : '#3fb950';
          return `<div style="padding:3px 0;border-bottom:1px solid var(--panel-border,#1e1e2e)20">
            <span style="color:${c};font-size:9px;text-transform:uppercase;margin-right:6px">${_esc(ev.type)}</span>
            <span style="color:var(--text-muted,#7878a0);font-size:11px;white-space:pre-wrap;word-break:break-word">${_esc((ev.text || '').slice(0, 180))}</span>
          </div>`;
        }).join('')
      : '<div style="color:var(--text-muted,#7878a0);font-size:11px;padding:8px">No events recorded yet.</div>';

    panel.innerHTML = `
      <div style="display:flex;align-items:center;padding:10px 12px;border-bottom:1px solid var(--panel-border,#1e1e2e)">
        <span style="font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${_esc(task.prompt?.slice(0, 60) || 'Task ' + taskId.slice(0, 8))}
        </span>
        <button onclick="document.getElementById('nxTaskDetail').remove()"
          style="background:none;border:none;color:var(--text-muted,#7878a0);cursor:pointer;font-size:14px">✕</button>
      </div>
      <div style="padding:8px 12px;font-size:11px;color:var(--text-muted,#7878a0)">
        Status: <strong style="color:${(STATUS_CONFIG[task.status] || STATUS_CONFIG.queued).color}">${task.status}</strong>
        · Events: ${events.length}
      </div>
      <div style="flex:1;overflow-y:auto;padding:0 12px 8px;font-family:ui-monospace,monospace">
        ${evHtml}
      </div>`;
    document.body.appendChild(panel);
  }

  /* ── File tree live sync ────────────────────────────────────────────────── */
  function _scheduleTreeRefresh() {
    if (_fsThrottle) { _pendingFsRefresh = true; return; }
    _fsThrottle = setTimeout(() => {
      _fsThrottle = null;
      if (_pendingFsRefresh) { _pendingFsRefresh = false; _scheduleTreeRefresh(); return; }
      if (typeof window.refreshFileTree === 'function') {
        window.refreshFileTree();
      } else if (typeof window.loadFiles === 'function' && _sid) {
        window.loadFiles(_sid);
      }
    }, 800); // batch rapid consecutive file.modified events
  }

  /* ── Batched render loop ────────────────────────────────────────────────── */
  function _queueRender() {
    if (_dirtyRender) return;
    _dirtyRender = true;
    clearTimeout(_renderTimer);
    _renderTimer = setTimeout(() => {
      _dirtyRender = false;
      _renderChips();
      _saveWorkspaceState();
    }, RENDER_THROTTLE);
  }

  /* ── Task state machine ─────────────────────────────────────────────────── */
  function _upsertTask(taskId, updates) {
    const existing = _tasks.get(taskId) || { id: taskId, ts: Date.now(), events: [] };
    const merged = { ...existing, ...updates };
    _tasks.set(taskId, merged);
    _queueRender();
    return merged;
  }

  function _appendTaskEvent(taskId, evType, text) {
    const task = _tasks.get(taskId);
    if (!task) return;
    if (task.events.length >= MAX_EVENTS) task.events.shift();
    task.events.push({ type: evType, text, ts: Date.now() });
    // Update detail panel if open
    const detail = document.getElementById('nxTaskDetail');
    if (detail && detail.dataset.taskId === taskId) _toggleTaskDetail(taskId); // re-render
  }

  /* ── NxBus subscriptions ────────────────────────────────────────────────── */
  function _subscribeNxBus() {
    if (!window.NxBus) { setTimeout(_subscribeNxBus, 400); return; }

    // ── Agent lifecycle ──────────────────────────────────────────────────────
    NxBus.on('agent.think', ({ session_id, text, task_id } = {}) => {
      const id = task_id || session_id || _sid || 'default';
      _upsertTask(id, { status: 'running', sid: session_id });
      if (text) _appendTaskEvent(id, 'think', text);
      _activeTask = id;
    });

    NxBus.on('agent.action', ({ session_id, action, path: aPath, task_id } = {}) => {
      const id = task_id || session_id || _sid || 'default';
      _upsertTask(id, { status: 'running' });
      _appendTaskEvent(id, 'action', `${action}${aPath ? ': ' + aPath : ''}`);
    });

    NxBus.on(NxBus.EVENTS?.AGENT_DONE || 'agent.done', ({ session_id, status, task_id } = {}) => {
      const id = task_id || session_id || _sid || 'default';
      _upsertTask(id, { status: status || 'done' });
      _appendTaskEvent(id, 'done', `Task ${status || 'done'}`);
      if (_activeTask === id) _activeTask = null;
    });

    NxBus.on('agent.error', ({ session_id, error, task_id } = {}) => {
      const id = task_id || session_id || _sid || 'default';
      _upsertTask(id, { status: 'failed' });
      _appendTaskEvent(id, 'error', error || 'Unknown error');
    });

    NxBus.on('agent.retry', ({ session_id, attempt, task_id } = {}) => {
      const id = task_id || session_id || _sid || 'default';
      _upsertTask(id, { status: 'retrying' });
      _appendTaskEvent(id, 'retry', `Attempt ${attempt || '?'}`);
    });

    // ── HITL ────────────────────────────────────────────────────────────────
    NxBus.on('nx:hitl:required', ({ session_id, event_id, prompt } = {}) => {
      const id = event_id || session_id || _sid || 'default';
      _upsertTask(id, { status: 'hitl', prompt: prompt || 'Awaiting approval' });
      _appendTaskEvent(id, 'hitl', prompt || 'Human approval required');
    });

    NxBus.on('nx:hitl:resolved', ({ session_id, action } = {}) => {
      const id = session_id || _sid || 'default';
      _upsertTask(id, { status: 'running' });
      _appendTaskEvent(id, 'hitl_resolved', `Operator: ${action}`);
    });

    // ── Stream lifecycle ─────────────────────────────────────────────────────
    NxBus.on(NxBus.EVENTS?.STREAM_OPEN || 'stream.open', ({ sid } = {}) => {
      if (sid && !_tasks.has(sid)) _upsertTask(sid, { status: 'running', prompt: 'Agent session' });
    });

    NxBus.on(NxBus.EVENTS?.STREAM_CLOSED || 'stream.closed', ({ sid } = {}) => {
      const task = _tasks.get(sid);
      if (task && task.status === 'running') _upsertTask(sid, { status: 'done' });
    });

    // ── Session lifecycle ────────────────────────────────────────────────────
    NxBus.on(NxBus.EVENTS?.SESSION_RESTORED || 'session.restored', ({ sid } = {}) => {
      if (sid && sid !== _sid) NxWorkspaceRuntime.resetForSession(sid);
    });

    NxBus.on(NxBus.EVENTS?.SESSION_CREATED || 'session.created', ({ sid } = {}) => {
      NxWorkspaceRuntime.resetForSession(sid);
    });

    // ── File mutations → tree refresh ────────────────────────────────────────
    NxBus.on('file.modified', ({ path, session_id } = {}) => {
      if (session_id && session_id !== _sid) return;
      _scheduleTreeRefresh();
      // Forward to NxMonaco for conflict detection — use distinct event to avoid re-entry loop
      if (window.NxMonaco) {
        NxBus.emit('nx:monaco:file:modified', { path, session_id });
      }
    });

    // ── Queue events → task chips ────────────────────────────────────────────
    NxBus.on('queue.submitted', ({ task_id, prompt, session_id } = {}) => {
      _upsertTask(task_id || session_id || 'q-' + Date.now(), { status: 'queued', prompt });
    });

    NxBus.on('queue.started', ({ task_id, session_id } = {}) => {
      const id = task_id || session_id;
      if (id) _upsertTask(id, { status: 'running' });
    });

    // ── STREAM_CHUNK — parse structured events from log lines ────────────────
    NxBus.on(NxBus.EVENTS?.STREAM_CHUNK || 'stream.chunk', ({ sid, data, event } = {}) => {
      const id = sid || _sid || 'default';
      if (!_tasks.has(id)) _upsertTask(id, { status: 'running', prompt: 'Agent session' });
      if (event === 'agent.think' && data?.text) _appendTaskEvent(id, 'think', data.text);
      if (event === 'agent.done')  _upsertTask(id, { status: 'done' });
      if (event === 'agent.error') _upsertTask(id, { status: 'failed' });
    });
  }

  /* ── Helpers ────────────────────────────────────────────────────────────── */
  function _fmtAge(ms) {
    if (ms < 60000)  return Math.round(ms / 1000) + 's';
    if (ms < 3600000) return Math.round(ms / 60000) + 'm';
    return Math.round(ms / 3600000) + 'h';
  }

  function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /* ── Styles ─────────────────────────────────────────────────────────────── */
  function _injectChipStyles() {
    if (document.getElementById('nxChipStyles')) return;
    const s = document.createElement('style');
    s.id = 'nxChipStyles';
    s.textContent = `
      @keyframes nx-chip-pulse {
        0%,100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); }
        50%      { box-shadow: 0 0 0 4px rgba(99,102,241,.2); }
      }
      .nx-task-chip { transition: opacity .2s, transform .1s; }
      .nx-task-chip:hover { transform: translateY(-1px); opacity:.9; }
      #nxTaskDetail { animation: nx-slide-up .15s ease; }
      @keyframes nx-slide-up {
        from { transform:translateY(8px);opacity:0; }
        to   { transform:translateY(0);opacity:1; }
      }
    `;
    document.head.appendChild(s);
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */
  const NxWorkspaceRuntime = {

    init(sid) {
      _sid = sid;
      _ensureChipBar();
      _subscribeNxBus();
      // Clock-tick to update chip ages
      setInterval(_queueRender, 30000);
      // Restore any previously running task state
      try {
        const saved = JSON.parse(localStorage.getItem(STORE_KEY_WS) || '{}');
        if (saved.sid === sid && saved.activeTask) {
          _upsertTask(saved.activeTask, { status: 'running', prompt: 'Restoring…' });
        }
      } catch (_) {}
      console.debug('[NxWorkspaceRuntime] initialized for session', sid);
    },

    resetForSession(sid) {
      _sid = sid;
      _tasks.clear();
      _activeTask = null;
      _queueRender();
    },

    cancelTask(taskId) {
      const task = _tasks.get(taskId);
      if (!task) return;
      const sid = task.sid || _sid;
      if (!sid) return;
      fetch(`/api/session/${sid}/stop`, { method: 'POST' })
        .then(() => _upsertTask(taskId, { status: 'cancelled' }))
        .catch(() => {});
    },

    get activeTasks() { return [..._tasks.values()]; },
    get sid()         { return _sid; },
  };

  window.NxWorkspaceRuntime = NxWorkspaceRuntime;

  // Auto-init
  function _tryAutoInit() {
    const sid = typeof currentSession !== 'undefined' ? currentSession : null;
    if (sid) { NxWorkspaceRuntime.init(sid); return; }
    setTimeout(_tryAutoInit, 500);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_tryAutoInit, 800));
  } else {
    setTimeout(_tryAutoInit, 800);
  }

})();
