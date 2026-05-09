/* ══════════════════════════════════════════════════════════════════
     Phase 12 — Conversational Context + Editable Prompt System
     ══════════════════════════════════════════════════════════════════ */
  (function () {

    let _p12CurrentSid = null;  // session whose chat we're showing
    let _p12EditMsgId = null;  // message id being edited
    let _p12EditSid = null;  // session containing the message being edited
    let _p12PollTimer = null;

    /* ── helpers ─────────────────────────────────────────────────────── */
    function _p12escHtml(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function _p12fmt(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /* ── Load & render chat history ──────────────────────────────────── */
    window.p12LoadChat = async function (sid) {
      if (!sid) { p12RenderEmpty(); return; }
      _p12CurrentSid = sid;
      try {
        const r = await fetch(`/api/chat/${sid}?limit=50`);
        const d = await r.json();
        if (d.ok) {
          p12RenderMessages(d.messages || []);
          // Show the chat tab dot if there are messages
          const dot = document.getElementById('p12ChatDot');
          if (dot) dot.style.opacity = d.messages && d.messages.length ? '1' : '0';
        }
      } catch (e) {
        console.warn('[Phase 12] chat load error', e);
      }
    };

    function p12RenderEmpty() {
      const box = document.getElementById('p12ChatMsgs');
      const footer = document.getElementById('p12ChatFooter');
      if (box) box.innerHTML = `<div class="p12-empty"><div class="p12-empty-icon">💬</div><div>No conversation yet.</div><div style="font-size:0.75rem;color:var(--muted);margin-top:4px">Run a task to start the conversation.</div></div>`;
      if (footer) footer.style.display = 'none';
    }

    window.p12RenderMessages = function (messages) {
      const box = document.getElementById('p12ChatMsgs');
      const footer = document.getElementById('p12ChatFooter');
      if (!box) return;
      if (!messages || !messages.length) { p12RenderEmpty(); return; }

      const isLastUser = messages.length && messages[messages.length - 1].role === 'user';
      const html = messages.map((m, idx) => {
        const meta = (() => { try { return JSON.parse(m.meta_json || '{}'); } catch (e) { return {}; } })();
        const isLast = idx === messages.length - 1;
        const editBtn = (m.role === 'user')
          ? `<button class="p12-msg-edit-btn" title="Edit & re-run" onclick="p12OpenEditModal(${m.id},'${_p12escHtml(m.content.replace(/'/g, "\\'"))}')">✏️ Edit</button>`
          : '';
        const statusDot = (m.role === 'assistant' && meta.status)
          ? `<span class="p12-status-dot ${meta.status}" title="${meta.status}"></span>`
          : '';
        const elapsed = meta.elapsed ? `<span>${Math.round(meta.elapsed)}s</span>` : '';
        const files = (meta.files && meta.files.length)
          ? `<span title="${meta.files.join(', ')}">${meta.files.length} file${meta.files.length > 1 ? 's' : ''}</span>`
          : '';
        const linkedSid = meta.session_id || '';
        const goBtn = (m.role === 'user' && linkedSid)
          ? `<span style="cursor:pointer;color:var(--accent)" title="Go to session" onclick="if(typeof selectSession==='function')selectSession('${linkedSid}')">→ session</span>`
          : '';
        return `<div class="p12-msg ${m.role}" data-mid="${m.id}">
  <div class="p12-msg-bubble">${_p12escHtml(m.content)}</div>
  <div class="p12-msg-meta">
    ${statusDot}
    <span>${_p12fmt(m.ts)}</span>
    ${elapsed}${files}${goBtn}${editBtn}
  </div>
</div>`;
      }).join('');

      box.innerHTML = html;
      box.scrollTop = box.scrollHeight;
      if (footer) footer.style.display = 'flex';
      // Update continue button label
      const cb = document.getElementById('p12ContinueBtn');
      if (cb) cb.innerHTML = `<span>↩</span> Continue / modify this session…`;
    };

    /* ── Continue button: prefill task input ─────────────────────────── */
    window.p12Continue = function () {
      const ta = document.getElementById('taskInput');
      if (ta) {
        ta.focus();
        ta.placeholder = 'Describe what to change or continue…';
      }
      // switch back to logs tab so user sees the run
      if (typeof nxSetTab === 'function') nxSetTab('logs');
    };

    /* ── Edit modal ──────────────────────────────────────────────────── */
    window.p12OpenEditModal = function (msgId, currentContent) {
      _p12EditMsgId = msgId;
      _p12EditSid = _p12CurrentSid;
      const ta = document.getElementById('p12EditTextarea');
      if (ta) ta.value = currentContent;
      const ov = document.getElementById('p12EditOverlay');
      if (ov) ov.style.display = 'flex';
      if (ta) ta.focus();
    };

    window.p12CloseEditModal = function (e) {
      if (e && e.target !== document.getElementById('p12EditOverlay')) return;
      document.getElementById('p12EditOverlay').style.display = 'none';
      _p12EditMsgId = null;
      _p12EditSid = null;
    };

    window.p12SubmitEdit = async function () {
      const ta = document.getElementById('p12EditTextarea');
      const newPrompt = (ta ? ta.value : '').trim();
      if (!newPrompt) return;
      if (!_p12EditMsgId || !_p12EditSid) return;

      // 1. Delete this message and all after it from chat history
      try {
        await fetch(`/api/chat/${_p12EditSid}/edit/${_p12EditMsgId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: newPrompt })
        });
      } catch (e) { console.warn('[Phase 12] edit delete error', e); }

      // 2. Close modal
      document.getElementById('p12EditOverlay').style.display = 'none';

      // 3. Put prompt in taskInput and queue with context from the edited session
      const taskInput = document.getElementById('taskInput');
      if (taskInput) taskInput.value = newPrompt;
      _p12QueueWithContext(newPrompt, _p12EditSid);

      _p12EditMsgId = null;
      _p12EditSid = null;
    };

    /* ── Clear chat ──────────────────────────────────────────────────── */
    window.p12ClearChat = async function () {
      if (!_p12CurrentSid) return;
      if (!confirm('Clear the conversation history for this session?')) return;
      try {
        await fetch(`/api/chat/${_p12CurrentSid}`, { method: 'DELETE' });
        p12RenderEmpty();
        const dot = document.getElementById('p12ChatDot');
        if (dot) dot.style.opacity = '0';
      } catch (e) { console.warn('[Phase 12] clear error', e); }
    };

    /* ── Queue task with conversation context ─────────────────────────── */
    async function _p12QueueWithContext(task, continueSid) {
      const model = document.getElementById('modelSelect') ? document.getElementById('modelSelect').value : '';
      const planMode = (window.NX && window.NX.planMode) ? window.NX.planMode : 'elite';

      const body = { task, model: model || null, plan_mode: planMode };
      if (continueSid) body.continue_sid = continueSid;

      try {
        const resp = await fetch('/api/queue-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) {
          const toast = window.toast || window.showToast;
          if (typeof toast === 'function') toast(data.error || 'Failed to queue task', 'err');
          return;
        }
        // Clear the task input
        const taskInput = document.getElementById('taskInput');
        if (taskInput) taskInput.value = '';
        // Update NX global state
        if (window.NX) window.NX.activeSid = data.session_id;
        if (typeof nxSetGlobalStatus === 'function') nxSetGlobalStatus('running');
        // Update plan mode label
        const planEl = document.getElementById('nxActivePlanMode');
        if (planEl && window.NX_PLANS) {
          const p = window.NX_PLANS[planMode] || window.NX_PLANS.elite;
          if (p) { planEl.textContent = p.label; planEl.style.color = p.color; }
        }
        // Select the new session
        if (typeof selectSession === 'function') selectSession(data.session_id);
        if (typeof loadSessions === 'function') loadSessions();
        if (typeof loadQueue === 'function') loadQueue();
        // Reload chat after a short delay to show the new user message
        setTimeout(() => p12LoadChat(data.chat_sid || data.session_id), 600);
        // Switch to logs to see execution
        if (typeof nxSetTab === 'function') nxSetTab('logs');
      } catch (e) {
        console.error('[Phase 12] queue error', e);
      }
    }

    /* ── Patch the global task runners to route through Phase 12 ─────── */
    // Patch nxQueueTask (used by the Run button)
    const _p12_origNxQueueTask = window.nxQueueTask;
    window.nxQueueTask = async function () {
      const taskInput = document.getElementById('taskInput');
      const task = taskInput ? taskInput.value.trim() : '';
      if (!task) {
        if (typeof toast === 'function') toast('Please describe a task first.', 'err');
        return;
      }
      // Inject uploaded context if any (preserve Phase 31 behaviour)
      if (typeof _uploadedContext !== 'undefined' && _uploadedContext.length > 0) {
        const ctx = _uploadedContext.map(u => u.context).join('\n\n');
        if (taskInput && ctx) taskInput.value = ctx + '\n\n' + taskInput.value;
        _uploadedContext = [];
        if (typeof renderUploadChips === 'function') renderUploadChips();
        if (typeof nxSyncContextBadgesFromChips === 'function') nxSyncContextBadgesFromChips();
      }
      const finalTask = taskInput ? taskInput.value.trim() : task;
      const continueSid = (typeof currentSession !== 'undefined' && currentSession) || null;
      await _p12QueueWithContext(finalTask, continueSid);
    };

    // Also patch window.queueTask (used by keyboard shortcuts and other callers)
    const _p12_origQueueTask = window.queueTask;
    window.queueTask = async function () {
      const taskInput = document.getElementById('taskInput');
      const task = taskInput ? taskInput.value.trim() : '';
      if (!task) {
        if (typeof toast === 'function') toast('Please enter a task.', 'err');
        return;
      }
      const continueSid = (typeof currentSession !== 'undefined' && currentSession) || null;
      await _p12QueueWithContext(task, continueSid);
    };

    /* ── Auto-reload chat when session is selected ────────────────────── */
    const _p12_origSelectSession = window.selectSession;
    window.selectSession = function (sid) {
      if (typeof _p12_origSelectSession === 'function') _p12_origSelectSession(sid);
      _p12CurrentSid = sid;
      // If chat tab is active, reload chat
      const chatTab = document.querySelector('[data-nxtab="chat"].active');
      if (chatTab) p12LoadChat(sid);
      // Show dot if this session has messages
      fetch(`/api/chat/${sid}?limit=1`).then(r => r.json()).then(d => {
        const dot = document.getElementById('p12ChatDot');
        if (dot) dot.style.opacity = (d.ok && d.messages && d.messages.length) ? '1' : '0';
      }).catch(() => { });
    };

    /* ── Poll chat when running (to pick up assistant replies) ────────── */
    function _p12StartPoll(sid) {
      _p12StopPoll();
      _p12PollTimer = setInterval(() => {
        if (!_p12CurrentSid) return;
        const chatTab = document.querySelector('[data-nxtab="chat"].active');
        if (chatTab) p12LoadChat(_p12CurrentSid);
      }, 3000);
    }
    function _p12StopPoll() {
      if (_p12PollTimer) { clearInterval(_p12PollTimer); _p12PollTimer = null; }
    }

    /* ── Init ──────────────────────────────────────────────────────────── */
    window.NX_LOAD_TASKS.push( function () {
      console.log('[Phase 12] Conversational Context + Editable Prompt System active.');
      // Keyboard shortcut: Escape closes edit modal
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          const ov = document.getElementById('p12EditOverlay');
          if (ov && ov.style.display !== 'none') {
            ov.style.display = 'none';
            _p12EditMsgId = null; _p12EditSid = null;
          }
        }
      });
    });

  })();
