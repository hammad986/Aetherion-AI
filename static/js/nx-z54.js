/* ════════════════════════════════════════════════════════════════════════
   nx-z54.js — Phase Z54: Real Operationalization + Interaction Completion
   Every visible UI element must have real behavior, real feedback,
   real operational purpose. No fake UX. No dead controls.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const set = (id, val) => { const e = $(id); if (e) e.textContent = val; };

  /* ═══════════════════════════════════════════════════════════════════
     Z54A — DEAD CONTROL ELIMINATION
     Every control must either work or be hidden.
     ═══════════════════════════════════════════════════════════════════ */

  function z54AuditDeadControls() {
    // 1. Stop button: only show while a task is running
    const stopBtn = qs('.nx-topbar-stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';

    // 2. Voice button: no voice API connected — hide completely
    const voiceBtn = $('nxVoiceBtn');
    if (voiceBtn) {
      voiceBtn.style.display = 'none';
      voiceBtn.setAttribute('aria-hidden', 'true');
    }

    // 3. Context bar: show only when attachments exist
    const ctxBar = $('nxContextBar');
    const ctxBadges = $('nxCtxBadges');
    if (ctxBar && ctxBadges && !ctxBadges.children.length) {
      ctxBar.style.display = 'none';
    }

    // 4. Plus menu: wire toggle properly
    z54WirePlusMenu();

    // 5. Quick action chips: ensure nxSetTask is available
    if (typeof window.nxSetTask !== 'function') {
      window.nxSetTask = function (text) {
        const input = $('taskInput');
        if (!input) return;
        input.value = text;
        input.focus();
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      };
    }

    // 6. Context bar observer: show/hide when badges are added/removed
    if (ctxBar && ctxBadges) {
      new MutationObserver(() => {
        ctxBar.style.display = ctxBadges.children.length ? 'flex' : 'none';
      }).observe(ctxBadges, { childList: true });
    }
  }

  function z54WirePlusMenu() {
    const plusBtn = qs('.nx-plus-btn, #nxPlusBtn, [data-menu="plus"]') ||
                    qs('button[title*="tach"], button[aria-label*="tach"]');
    const menu = $('nxPlusMenu');
    if (!plusBtn || !menu) return;
    if (plusBtn.dataset.z54wired) return;
    plusBtn.dataset.z54wired = '1';
    plusBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      const open = menu.style.display === 'block';
      menu.style.display = open ? 'none' : 'block';
    });
    document.addEventListener('click', () => { if (menu) menu.style.display = 'none'; });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54B — TASK EXECUTION LIFECYCLE
     idle → queued → running → complete | failed
     ═══════════════════════════════════════════════════════════════════ */

  let _z54SSEConn = null;
  let _z54CurrentSid = null;
  let _z54ExecState = 'idle';

  function z54SetExecState(state, sid) {
    const prev = _z54ExecState;
    _z54ExecState = state;
    if (sid) _z54CurrentSid = sid;

    // Stop button visibility
    const stopBtn = qs('.nx-topbar-stop-btn');
    if (stopBtn) stopBtn.style.display = state === 'running' ? '' : 'none';

    // Pipeline bar
    const pipeline = $('nxLogsPipeline');
    if (pipeline) {
      if (state === 'running' || state === 'queued') {
        pipeline.style.display = 'flex';
      } else if (state === 'complete' || state === 'failed') {
        setTimeout(() => { if (pipeline) pipeline.style.display = 'none'; }, 4000);
      }
    }

    // Workspace state data attribute for CSS-driven states
    const body = document.body;
    if (body) body.dataset.z54state = state;

    // Refresh panels that may be open
    if (state === 'complete' || state === 'failed') {
      z54RefreshIdleRecent();
      z54RefreshOpenPanel();
    }
  }

  function z54RefreshOpenPanel() {
    // Refresh whichever slide panel is open after execution
    ['files', 'chat', 'history'].forEach(id => {
      const panel = $('nxPanel-' + id);
      if (panel && panel.style.display && panel.style.display !== 'none') {
        if (id === 'files') z54RefreshFiles();
        if (id === 'chat')  z54LoadChat();
        if (id === 'history') z54RefreshHistory();
      }
    });
  }

  // Intercept fetch to capture session IDs from queue-task responses
  function z54InterceptFetch() {
    const origFetch = window.fetch;
    window.fetch = async function (url, opts) {
      const res = await origFetch.apply(this, arguments);
      const urlStr = typeof url === 'string' ? url : (url?.url || '');
      if (urlStr.includes('/api/queue-task') && opts?.method === 'POST') {
        try {
          const clone = res.clone();
          clone.json().then(data => {
            if (data?.ok && data?.session_id) z54OnTaskQueued(data.session_id);
          }).catch(() => {});
        } catch (_) {}
      }
      return res;
    };
  }

  function z54OnTaskQueued(sid) {
    _z54CurrentSid = sid;
    z54SetExecState('running', sid);
    z54ConnectSSE(sid);
    // Broadcast for downstream listeners (Z55+)
    document.dispatchEvent(new CustomEvent('nx:exec:start', { detail: { sid } }));
    // Refresh idle recent after a delay so the new session appears
    setTimeout(z54RefreshIdleRecent, 2000);
    setTimeout(z54RefreshIdleRecent, 8000);
  }

  function z54ConnectSSE(sid) {
    if (_z54SSEConn) {
      try { _z54SSEConn.close(); } catch (_) {}
      _z54SSEConn = null;
    }
    try {
      const src = new EventSource('/api/stream/' + sid);
      _z54SSEConn = src;

      src.onmessage = e => {
        try { z54HandleSSEEvent(JSON.parse(e.data)); } catch (_) {}
      };
      ['thought', 'action', 'result', 'file_write', 'tool_call'].forEach(evtType => {
        src.addEventListener(evtType, e => {
          try { z54HandleSSEEvent({ type: evtType, ...JSON.parse(e.data || '{}') }); } catch (_) {}
        });
      });
      src.addEventListener('done', () => {
        z54SetExecState('complete', sid);
        document.dispatchEvent(new CustomEvent('nx:exec:end', { detail: { state: 'complete', sid } }));
        if (_z54SSEConn) { try { _z54SSEConn.close(); } catch (_) {} _z54SSEConn = null; }
        z54RefreshChatIfOpen();
      });
      src.addEventListener('error_event', e => {
        z54SetExecState('failed', sid);
        document.dispatchEvent(new CustomEvent('nx:exec:end', { detail: { state: 'failed', sid } }));
        if (_z54SSEConn) { try { _z54SSEConn.close(); } catch (_) {} _z54SSEConn = null; }
      });
      src.onerror = () => {
        // Tolerate SSE close — session may have completed
        if (_z54ExecState === 'running') {
          setTimeout(() => {
            if (_z54ExecState === 'running') z54SetExecState('idle');
          }, 3000);
        }
      };
    } catch (_) {}
  }

  function z54HandleSSEEvent(data) {
    if (!data) return;
    // Broadcast for downstream listeners (Z55+)
    document.dispatchEvent(new CustomEvent('nx:exec:sse', { detail: data }));
    const type = (data.type || '').toLowerCase();

    // Drive pipeline bar stages
    if (type === 'thought' || type === 'planning') {
      z54PipelineStage('planning', 'active');
    }
    if (type === 'action' || type === 'tool_call' || type === 'code' || type === 'file_write') {
      z54PipelineStage('planning', 'complete');
      z54PipelineStage('coding', 'active');
    }
    if (type === 'error_event') {
      z54PipelineStage('debugging', 'active');
    }
    if (type === 'result' && data.status === 'success') {
      z54PipelineStage('coding', 'complete');
      z54PipelineStage('done', 'active');
    }

    // Feed live chat activity
    if (['thought', 'action', 'result', 'file_write'].includes(type)) {
      z54AppendChatActivity(data);
    }
  }

  function z54PipelineStage(stage, state) {
    const el = $('nlp-' + stage);
    if (!el) return;
    // Remove prior states
    el.classList.remove('active', 'complete', 'failed');
    if (state !== 'idle') el.classList.add(state);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54C — WORKSPACE IDLE HERO — Real recent runs
     ═══════════════════════════════════════════════════════════════════ */

  async function z54RefreshIdleRecent() {
    const el = $('nxIdleRecent');
    if (!el) return;
    try {
      const r = await fetch('/api/sessions?limit=8');
      if (!r.ok) return;
      const sessions = await r.json();
      if (!sessions || !sessions.length) {
        el.innerHTML = '<div class="nx-iw-recent-empty">No recent runs — type a task and press Run to start.</div>';
        return;
      }
      el.innerHTML = '';
      sessions.slice(0, 6).forEach(s => {
        const status = s.status || 'idle';
        const statusCls = status === 'completed' ? 'ok'
          : (status === 'error' || status === 'failed') ? 'err'
          : status === 'running' ? 'run' : 'idle';
        const task = s.task_preview || s.project_name || s.task || ('Session ' + (s.sid || '').slice(-6));
        const ts = s.created_at ? z54RelTime(s.created_at) : '';
        const dur = s.duration_s ? z54Dur(s.duration_s) : '';
        const row = document.createElement('div');
        row.className = 'z54-recent-row';
        row.innerHTML = `
          <span class="z54-recent-dot ${statusCls}"></span>
          <span class="z54-recent-task">${z54esc(task)}</span>
          <span class="z54-recent-meta">${dur ? dur + ' · ' : ''}${ts}</span>`;
        row.title = 'Click to load session';
        row.addEventListener('click', () => {
          if (typeof loadSession === 'function') loadSession(s.sid);
          else if (typeof p4LoadSession === 'function') p4LoadSession(s.sid);
          else if (window.NX) window.NX.activeSid = s.sid;
        });
        el.appendChild(row);
      });
    } catch (_) {}
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54D — REAL CHAT PANEL
     Loads actual session chat history. Allows injecting messages.
     ═══════════════════════════════════════════════════════════════════ */

  function z54BuildChatPanel(el) {
    el.innerHTML = `
      <div class="z54-chat-wrap">
        <div class="z54-chat-header">
          <span class="z54-chat-title">Session Chat</span>
          <button class="nx-tiny-btn" onclick="z54LoadChat()" title="Refresh history">↻</button>
        </div>
        <div class="z54-chat-messages" id="z54ChatMessages">
          <div class="z54-chat-empty">
            <div class="z54-chat-empty-icon">💬</div>
            <div class="z54-chat-empty-label">Run a task to see agent activity here.</div>
          </div>
        </div>
        <div class="z54-chat-composer">
          <textarea class="z54-chat-input" id="z54ChatInput"
            placeholder="Inject instruction to running agent… (Ctrl+Enter to send)"
            rows="2" maxlength="2000"></textarea>
          <button class="z54-chat-send" id="z54ChatSend" title="Send (Ctrl+Enter)">↑</button>
        </div>
        <div class="z54-chat-hint">Messages are injected into the active session. Start a task first.</div>
      </div>`;

    const sendBtn = $('z54ChatSend');
    if (sendBtn) sendBtn.onclick = z54SendChatMessage;

    const input = $('z54ChatInput');
    if (input) {
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          z54SendChatMessage();
        }
      });
    }

    z54LoadChat();
  }

  window.z54LoadChat = async function () {
    const sid = window.NX?.activeSid || _z54CurrentSid
      || (typeof currentSession !== 'undefined' ? currentSession : null);
    const msgs = $('z54ChatMessages');
    if (!msgs) return;

    if (!sid) {
      msgs.innerHTML = `<div class="z54-chat-empty">
        <div class="z54-chat-empty-icon">💬</div>
        <div class="z54-chat-empty-label">No active session.</div>
        <div class="z54-chat-empty-hint">Run a task above to begin.</div>
      </div>`;
      return;
    }

    try {
      const r = await fetch('/api/chat/' + sid);
      if (!r.ok) throw new Error('failed');
      const data = await r.json();
      const messages = data.messages || data.chat || (Array.isArray(data) ? data : []);
      if (!messages.length) {
        msgs.innerHTML = `<div class="z54-chat-empty">
          <div class="z54-chat-empty-icon">💬</div>
          <div class="z54-chat-empty-label">No messages yet in this session.</div>
        </div>`;
        return;
      }
      msgs.innerHTML = '';
      messages.slice(-50).forEach(m => z54RenderChatMsg(m, msgs));
      msgs.scrollTop = msgs.scrollHeight;
    } catch (_) {
      msgs.innerHTML = `<div class="z54-chat-empty">
        <div class="z54-chat-empty-label">Unable to load chat history.</div>
        <button class="nx-tiny-btn" onclick="z54LoadChat()" style="margin-top:6px">Retry</button>
      </div>`;
    }
  };

  function z54RenderChatMsg(m, container) {
    const div = document.createElement('div');
    const role = m.role || m.type || 'system';
    div.className = 'z54-msg ' + (role === 'user' ? 'user' : role === 'assistant' || role === 'agent' ? 'agent' : 'sys');
    const content = String(m.content || m.text || m.message || '').slice(0, 600);
    const ts = m.timestamp || m.created_at;
    const timeStr = ts ? new Date(ts * 1000 || ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    div.innerHTML = `
      <div class="z54-msg-body">${z54esc(content)}</div>
      ${timeStr ? `<div class="z54-msg-time">${timeStr}</div>` : ''}`;
    if (container) container.appendChild(div);
    return div;
  }

  function z54AppendChatActivity(data) {
    const msgs = $('z54ChatMessages');
    if (!msgs) return;
    // Remove the empty state if present
    const empty = msgs.querySelector('.z54-chat-empty');
    if (empty) empty.remove();

    const type = (data.type || '').toLowerCase();
    const content = String(data.text || data.content || data.action || data.result || '').trim();
    if (!content || content.length < 2) return;

    const div = document.createElement('div');
    div.className = 'z54-msg sys live';
    const labels = { thought: '🧠 Thinking', action: '⚡ Action', file_write: '📄 Writing file', result: '✓ Result', tool_call: '🔧 Tool' };
    const label = labels[type] || type;
    div.innerHTML = `
      <div class="z54-msg-label">${label}</div>
      <div class="z54-msg-body">${z54esc(content.slice(0, 300))}</div>`;
    msgs.appendChild(div);
    // Keep last 100 messages, remove oldest
    const allMsgs = msgs.querySelectorAll('.z54-msg');
    if (allMsgs.length > 100) allMsgs[0].remove();
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function z54SendChatMessage() {
    const input = $('z54ChatInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    const sid = window.NX?.activeSid || _z54CurrentSid;
    if (!sid) {
      if (typeof toast === 'function') toast('No active session — run a task first', 'warn');
      return;
    }

    input.disabled = true;
    const sendBtn = $('z54ChatSend');
    if (sendBtn) sendBtn.disabled = true;

    try {
      const r = await fetch('/api/session/' + sid + '/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      const data = await r.json();
      if (data.ok || data.success) {
        input.value = '';
        // Append to chat immediately
        const msgs = $('z54ChatMessages');
        if (msgs) {
          const empty = msgs.querySelector('.z54-chat-empty');
          if (empty) empty.remove();
          const div = document.createElement('div');
          div.className = 'z54-msg user';
          div.innerHTML = `<div class="z54-msg-body">${z54esc(text)}</div>`;
          msgs.appendChild(div);
          msgs.scrollTop = msgs.scrollHeight;
        }
        if (typeof toast === 'function') toast('Message injected into session', 'ok');
      } else {
        if (typeof toast === 'function') toast(data.error || 'Failed to send message', 'err');
      }
    } catch (_) {
      if (typeof toast === 'function') toast('Network error — message not sent', 'err');
    } finally {
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      input.focus();
    }
  }

  function z54RefreshChatIfOpen() {
    const chatPanel = $('nxPanel-chat');
    if (chatPanel && chatPanel.style.display && chatPanel.style.display !== 'none') {
      z54LoadChat();
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54D — REAL FILES PANEL
     Real file tree with open, download, metadata.
     ═══════════════════════════════════════════════════════════════════ */

  function z54BuildFilesPanel(el) {
    el.innerHTML = `
      <div class="z54-files-wrap">
        <div class="z54-files-toolbar">
          <input id="z54FileSearch" class="z54-files-search"
            placeholder="Filter files…" autocomplete="off" spellcheck="false" />
          <button class="nx-tiny-btn" onclick="z54RefreshFiles()" title="Refresh">↻</button>
          <button class="nx-tiny-btn" onclick="nxClosePanels?.();nxSetTab?.('code');"
            title="Open Code tab">Tab →</button>
        </div>
        <div class="z54-files-tree" id="z54FileTree">
          <div class="z54-empty-state">
            <div class="z54-empty-icon">📁</div>
            <div class="z54-empty-label">No session active</div>
            <div class="z54-empty-hint">Run a task to generate workspace files.</div>
          </div>
        </div>
      </div>`;

    const search = $('z54FileSearch');
    if (search) {
      search.addEventListener('input', function () {
        const q = this.value.toLowerCase();
        qsa('.z54-file-row', $('z54FileTree')).forEach(row => {
          row.style.display = (q && !row.dataset.name.toLowerCase().includes(q)) ? 'none' : '';
        });
      });
    }
    z54RefreshFiles();
  }

  window.z54RefreshFiles = async function () {
    const sid = window.NX?.activeSid || _z54CurrentSid
      || (typeof currentSession !== 'undefined' ? currentSession : null);
    const tree = $('z54FileTree');
    if (!tree) return;

    if (!sid) {
      tree.innerHTML = `<div class="z54-empty-state">
        <div class="z54-empty-icon">📁</div>
        <div class="z54-empty-label">No active session</div>
        <div class="z54-empty-hint">Run a task to see workspace files.</div>
      </div>`;
      return;
    }

    tree.innerHTML = '<div class="z54-loading">Loading files…</div>';
    try {
      const r = await fetch('/api/files?sid=' + sid);
      if (!r.ok) throw new Error('failed');
      const d = await r.json();
      const files = (d.files || d.tree || []).slice(0, 200);
      if (!files.length) {
        tree.innerHTML = `<div class="z54-empty-state">
          <div class="z54-empty-icon">📁</div>
          <div class="z54-empty-label">No files yet</div>
          <div class="z54-empty-hint">Files appear here as the agent writes them.</div>
        </div>`;
        return;
      }
      tree.innerHTML = '';
      files.forEach(f => {
        const name = typeof f === 'string' ? f : (f.path || f.name || '');
        if (!name) return;
        const isDir = name.endsWith('/');
        const sizeFmt = f.size ? z54FileSize(f.size) : '';
        const row = document.createElement('div');
        row.className = 'z54-file-row' + (isDir ? ' is-dir' : '');
        row.dataset.name = name;
        row.title = name;
        row.innerHTML = `
          <span class="z54-file-icon">${isDir ? '📂' : z54FileIcon(name)}</span>
          <span class="z54-file-name">${z54esc(name.split('/').filter(Boolean).pop() || name)}</span>
          ${sizeFmt ? `<span class="z54-file-size">${sizeFmt}</span>` : ''}
          ${!isDir ? `<div class="z54-file-btns">
            <button class="z54-file-btn" title="Open in editor"
              onclick="z54OpenFile('${z54esc(name)}');event.stopPropagation()">↗</button>
            <a class="z54-file-btn" title="Download"
              href="/api/file/${sid}?path=${encodeURIComponent(name)}"
              download="${z54esc(name.split('/').pop())}"
              onclick="event.stopPropagation()">⬇</a>
          </div>` : ''}`;
        row.addEventListener('click', () => {
          if (isDir) return;
          qsa('.z54-file-row', tree).forEach(r => r.classList.remove('active'));
          row.classList.add('active');
          z54OpenFile(name);
        });
        tree.appendChild(row);
      });
    } catch (_) {
      tree.innerHTML = `<div class="z54-empty-state">
        <div class="z54-empty-label">Failed to load files.</div>
        <button class="nx-tiny-btn" onclick="z54RefreshFiles()" style="margin-top:8px">Retry</button>
      </div>`;
    }
  };

  window.z54OpenFile = function (name) {
    if (typeof nxClosePanels === 'function') nxClosePanels();
    if (typeof nxSetTab === 'function') nxSetTab('code');
    setTimeout(() => {
      if (typeof openFileInEditor === 'function') openFileInEditor(name);
      else if (typeof nxOpenFile === 'function') nxOpenFile(name);
    }, 80);
  };

  function z54FileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const map = { js: '🟨', ts: '🔷', jsx: '⚛', tsx: '⚛', py: '🐍', html: '🌐', css: '🎨', scss: '🎨',
      json: '📋', md: '📝', sh: '⚡', bash: '⚡', txt: '📄', yaml: '⚙', yml: '⚙',
      png: '🖼', jpg: '🖼', jpeg: '🖼', svg: '🖼', gif: '🖼', webp: '🖼',
      pdf: '📕', zip: '📦', sql: '🗃', rs: '🦀', go: '🐹', rb: '💎', php: '🐘' };
    return map[ext] || '📄';
  }

  function z54FileSize(b) {
    if (b < 1024) return b + 'B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + 'KB';
    return (b / (1024 * 1024)).toFixed(1) + 'MB';
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54D — REAL HISTORY PANEL
     Status-grouped sessions with replay and load actions.
     ═══════════════════════════════════════════════════════════════════ */

  function z54BuildHistoryPanel(el) {
    el.innerHTML = `
      <div class="z54-hist-wrap">
        <div class="z54-hist-toolbar">
          <span class="z54-hist-title">Session History</span>
          <button class="nx-tiny-btn" onclick="z54RefreshHistory()" title="Refresh">↻</button>
        </div>
        <div class="z54-hist-filters" id="z54HistFilters">
          <button class="z54-filt active" onclick="z54HistFilter(this,'all')">All</button>
          <button class="z54-filt" onclick="z54HistFilter(this,'completed')">✓ Done</button>
          <button class="z54-filt" onclick="z54HistFilter(this,'error')">✗ Failed</button>
          <button class="z54-filt" onclick="z54HistFilter(this,'running')">● Live</button>
        </div>
        <div class="z54-hist-list" id="z54HistList">
          <div class="z54-loading">Loading…</div>
        </div>
      </div>`;
    z54RefreshHistory();
  }

  let _z54HistData = [];
  let _z54HistFilt = 'all';

  window.z54RefreshHistory = async function () {
    const list = $('z54HistList');
    if (!list) return;
    list.innerHTML = '<div class="z54-loading">Loading…</div>';
    try {
      const r = await fetch('/api/sessions?limit=60');
      if (!r.ok) throw new Error('failed');
      _z54HistData = await r.json();
      z54RenderHistory();
    } catch (_) {
      if (list) list.innerHTML = `<div class="z54-empty-state">
        <div class="z54-empty-label">Failed to load.</div>
        <button class="nx-tiny-btn" onclick="z54RefreshHistory()" style="margin-top:8px">Retry</button>
      </div>`;
    }
  };

  window.z54HistFilter = function (btn, filter) {
    _z54HistFilt = filter;
    qsa('.z54-filt', $('z54HistFilters')).forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    z54RenderHistory();
  };

  function z54RenderHistory() {
    const list = $('z54HistList');
    if (!list) return;
    let sessions = _z54HistData;
    if (_z54HistFilt !== 'all') {
      sessions = sessions.filter(s => {
        const st = s.status || 'idle';
        if (_z54HistFilt === 'error') return st === 'error' || st === 'failed';
        return st === _z54HistFilt;
      });
    }
    if (!sessions.length) {
      list.innerHTML = `<div class="z54-empty-state">
        <div class="z54-empty-icon">🕐</div>
        <div class="z54-empty-label">${_z54HistFilt === 'all' ? 'No sessions yet.' : 'No ' + _z54HistFilt + ' sessions.'}</div>
      </div>`;
      return;
    }
    list.innerHTML = '';
    [...sessions].reverse().slice(0, 40).forEach(s => {
      const status = s.status || 'idle';
      const statusCls = status === 'completed' ? 'ok'
        : (status === 'error' || status === 'failed') ? 'err'
        : status === 'running' ? 'run' : 'idle';
      const task = s.task_preview || s.project_name || s.task || ('Session ' + (s.sid || '').slice(-6));
      const ts = s.created_at ? z54RelTime(s.created_at) : '';
      const dur = s.duration_s ? z54Dur(s.duration_s) : '';
      const item = document.createElement('div');
      item.className = 'z54-hist-item';
      item.innerHTML = `
        <div class="z54-hist-head">
          <span class="z54-hist-dot ${statusCls}"></span>
          <span class="z54-hist-task">${z54esc(task)}</span>
        </div>
        <div class="z54-hist-meta">${ts}${dur ? ' · ' + dur : ''}</div>
        <div class="z54-hist-actions">
          <button class="z54-hist-btn" onclick="z54LoadHistSess('${z54esc(s.sid)}')">Load →</button>
          ${status === 'completed' ? `<button class="z54-hist-btn replay" onclick="z54ReplaySess('${z54esc(s.sid)}')">⏮ Replay</button>` : ''}
        </div>`;
      list.appendChild(item);
    });
  }

  window.z54LoadHistSess = function (sid) {
    if (typeof nxClosePanels === 'function') nxClosePanels();
    if (typeof loadSession === 'function') loadSession(sid);
    else if (typeof p4LoadSession === 'function') p4LoadSession(sid);
    else if (window.NX) window.NX.activeSid = sid;
  };

  window.z54ReplaySess = function (sid) {
    if (typeof nxClosePanels === 'function') nxClosePanels();
    if (typeof nxSetTab === 'function') nxSetTab('live');
    setTimeout(() => {
      if (typeof z31LoadReplay === 'function') z31LoadReplay(sid);
    }, 100);
    if (typeof toast === 'function') toast('Loading session replay…', 'ok');
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z54E — SETTINGS SLIDE PANEL REALIZATION
     Real model info, API mode, system status, theme toggle.
     ═══════════════════════════════════════════════════════════════════ */

  function z54BuildSettingsPanel(el) {
    el.innerHTML = `
      <div class="z54-set-wrap">

        <div class="z54-set-section">
          <div class="z54-set-label">Active Model</div>
          <div class="z54-set-model-card" id="z54SetModelCard">
            <div class="z54-set-model-name" id="z54SetModelName">Loading…</div>
            <div class="z54-set-model-meta" id="z54SetModelMeta">—</div>
          </div>
          <button class="nx-tiny-btn z54-set-link"
            onclick="nxClosePanels?.();openSettings?.('intelligence')">
            Change Model →
          </button>
        </div>

        <div class="z54-set-section">
          <div class="z54-set-label">API Mode</div>
          <div class="z54-set-api-row" id="z54SetApiRow">
            <span class="z54-set-api-dot" id="z54SetApiDot"></span>
            <span id="z54SetApiLabel">Loading…</span>
          </div>
          <div class="z54-set-api-keys" id="z54SetApiKeys"></div>
          <button class="nx-tiny-btn z54-set-link"
            onclick="nxClosePanels?.();openSettings?.('api')">
            Configure Keys →
          </button>
        </div>

        <div class="z54-set-section">
          <div class="z54-set-label">Appearance</div>
          <button class="nx-tiny-btn z54-set-theme-btn" id="z54SetThemeBtn"
            onclick="p4ToggleTheme?.();z54UpdateThemeLabel();">
            Loading…
          </button>
        </div>

        <div class="z54-set-section z54-set-status">
          <div class="z54-set-label">System</div>
          <div id="z54SetSysStatus" class="z54-set-sysrows">Loading…</div>
        </div>

        <div class="z54-set-footer">
          <button class="nx-tiny-btn z54-set-full"
            onclick="nxClosePanels?.();openSettings?.()">
            Open Full Settings →
          </button>
          <button class="nx-tiny-btn z54-set-full"
            onclick="nxClosePanels?.();p8ShowUpgradeModal?.()">
            Plans &amp; Billing →
          </button>
        </div>
      </div>`;

    z54LoadSettingsPanel();
    z54UpdateThemeLabel();
  }

  async function z54LoadSettingsPanel() {
    try {
      const [mr, cr] = await Promise.all([
        fetch('/api/system/metrics'),
        fetch('/api/config').catch(() => null),
      ]);

      if (mr.ok) {
        const md = await mr.json();
        const providers = md.providers || [];
        const avail = providers.find(p => p.available) || providers[0];
        if (avail) {
          set('z54SetModelName', avail.model || avail.provider || 'Unknown model');
          set('z54SetModelMeta', avail.provider || '');
        } else {
          set('z54SetModelName', 'No provider configured');
          set('z54SetModelMeta', 'Add API keys to enable AI');
        }
        // System status
        const sys = md.system || {};
        const statusEl = $('z54SetSysStatus');
        if (statusEl) {
          statusEl.innerHTML = `
            <div class="z54-set-row"><span>Status</span><span style="color:var(--green)">● Online</span></div>
            <div class="z54-set-row"><span>Sessions</span><span>${md.sessions?.total ?? md.session_count ?? 0}</span></div>
            <div class="z54-set-row"><span>CPU</span><span>${(sys.cpu_pct || 0).toFixed(1)}%</span></div>
            <div class="z54-set-row"><span>Memory</span><span>${(sys.mem_used_pct || 0).toFixed(1)}%</span></div>`;
        }
      }

      if (cr && cr.ok) {
        const cfg = await cr.json();
        const mode = cfg.mode || 'managed';
        const dot = $('z54SetApiDot');
        const label = $('z54SetApiLabel');
        const keysEl = $('z54SetApiKeys');
        if (dot) dot.dataset.mode = mode;
        if (label) label.textContent = mode === 'byok' ? 'BYOK — Your API Keys' : 'Managed — Platform Keys';
        if (keysEl && mode === 'byok') {
          const keysSet = Object.keys(cfg.api_keys_set || {}).filter(k => cfg.api_keys_set[k]);
          keysEl.textContent = keysSet.length
            ? keysSet.length + ' provider' + (keysSet.length === 1 ? '' : 's') + ' active'
            : 'No keys configured';
        }
      }
    } catch (_) {
      set('z54SetModelName', 'Unable to load');
    }
  }

  window.z54UpdateThemeLabel = function () {
    const btn = $('z54SetThemeBtn');
    if (!btn) return;
    const isLight = document.documentElement.classList.contains('light') ||
                    document.body.classList.contains('light-mode') ||
                    document.body.dataset.theme === 'light';
    btn.textContent = isLight ? '🌙 Switch to Dark Mode' : '☀️ Switch to Light Mode';
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z54F — OPERATIONAL FEEDBACK
     Toast deduplication. Suppress noise. Real execution events.
     ═══════════════════════════════════════════════════════════════════ */

  const _z54ToastTrack = new Map();

  function z54HookToastDedup() {
    const origToast = window.toast;
    if (typeof origToast !== 'function') {
      setTimeout(z54HookToastDedup, 400);
      return;
    }
    if (window._z54ToastHooked) return;
    window._z54ToastHooked = true;
    window.toast = function (msg, type, dur) {
      const key = (type || 'ok') + ':' + String(msg || '').slice(0, 80);
      const last = _z54ToastTrack.get(key) || 0;
      if (Date.now() - last < 2500) return; // suppress within 2.5s
      _z54ToastTrack.set(key, Date.now());
      // Prune old entries
      if (_z54ToastTrack.size > 40) {
        const cutoff = Date.now() - 15000;
        _z54ToastTrack.forEach((t, k) => { if (t < cutoff) _z54ToastTrack.delete(k); });
      }
      return origToast.call(this, msg, type, dur);
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z54G — TRUST PASS
     Model button real name. Stop button state. nxSetTask real.
     ═══════════════════════════════════════════════════════════════════ */

  function z54TrustPass() {
    z54RefreshModelButton();
    z54WireStopButton();
  }

  async function z54RefreshModelButton() {
    const nameEl = $('nxModelName');
    if (!nameEl || (nameEl.textContent && nameEl.textContent !== 'Loading…')) return;
    try {
      const r = await fetch('/api/system/metrics');
      if (!r.ok) return;
      const d = await r.json();
      const providers = d.providers || [];
      const avail = providers.find(p => p.available) || providers[0];
      if (avail) {
        const name = avail.model || avail.provider || 'No model';
        nameEl.textContent = name.length > 20 ? name.slice(0, 18) + '…' : name;
      } else {
        nameEl.textContent = 'No provider';
      }
    } catch (_) {
      if (nameEl.textContent === 'Loading…') nameEl.textContent = 'Configure…';
    }
  }

  function z54WireStopButton() {
    const stopBtn = qs('.nx-topbar-stop-btn');
    if (!stopBtn || stopBtn.dataset.z54wired) return;
    stopBtn.dataset.z54wired = '1';
    stopBtn.onclick = function () {
      const sid = window.NX?.activeSid || _z54CurrentSid;
      if (typeof stopSession === 'function') {
        stopSession();
      } else if (sid) {
        fetch('/api/session/' + sid + '/stop', { method: 'POST' })
          .then(() => {
            z54SetExecState('idle');
            if (typeof toast === 'function') toast('Session stopped', 'ok');
          }).catch(() => {});
      }
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     PANEL BUILDER OVERRIDE
     Override nx-z50 panel builders to use Z54 real versions.
     ═══════════════════════════════════════════════════════════════════ */

  function z54OverridePanels() {
    const orig = window.nxTogglePanel;
    if (typeof orig !== 'function') {
      setTimeout(z54OverridePanels, 250);
      return;
    }
    if (window._z54PanelsHooked) return;
    window._z54PanelsHooked = true;

    window.nxTogglePanel = function (panelId) {
      // Clear both z50 and z54 load flags so z54 always renders its richer
      // content on every open. Without this, z50 rebuilds its simpler DOM on
      // re-open (because z50loaded was cleared) and z54 then tries to refresh
      // elements that no longer exist (because z54built was still set).
      const contentEl = $('nxPanelContent-' + panelId);
      if (contentEl) {
        delete contentEl.dataset.z50loaded;
        delete contentEl.dataset.z54built;
      }

      orig.call(this, panelId);

      // After z50 toggles the panel (display:flex), populate with z54 content
      const panel = $('nxPanel-' + panelId);
      const isOpen = panel && panel.style.display !== 'none' && panel.style.display !== '';
      if (!isOpen || !contentEl) return;

      // Always build fresh — each builder fetches live data via its own refresh call
      contentEl.dataset.z54built = '1';
      switch (panelId) {
        case 'chat':     z54BuildChatPanel(contentEl);     break;
        case 'files':    z54BuildFilesPanel(contentEl);    break;
        case 'history':  z54BuildHistoryPanel(contentEl);  break;
        case 'settings': z54BuildSettingsPanel(contentEl); break;
      }
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     UTILITIES
     ═══════════════════════════════════════════════════════════════════ */

  function z54RelTime(ts) {
    const diff = Math.floor(Date.now() / 1000 - (ts > 1e10 ? ts / 1000 : ts));
    if (diff < 5)  return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function z54Dur(s) {
    if (!s || s < 1) return '';
    if (s < 60) return s + 's';
    return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  }

  function z54esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ═══════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════ */

  function z54Boot() {
    z54AuditDeadControls();
    z54TrustPass();
    z54HookToastDedup();
    z54InterceptFetch();
    z54OverridePanels();
    z54RefreshIdleRecent();
    console.debug('[Phase Z54] Real Operationalization + Interaction Completion active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z54Boot);
  } else {
    setTimeout(z54Boot, 250); // after z50 boot
  }

  // Public API
  window._z54 = {
    setExecState:    z54SetExecState,
    connectSSE:      z54ConnectSSE,
    refreshRecent:   z54RefreshIdleRecent,
    loadChat:        z54LoadChat,
    refreshFiles:    window.z54RefreshFiles,
    refreshHistory:  window.z54RefreshHistory,
    updateTheme:     window.z54UpdateThemeLabel,
  };

})();
