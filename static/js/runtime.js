'use strict';

      let currentSession = null;
      let lastLogSeq = 0;
      let logStream = null;   // EventSource for live SSE log tail
      let logBuffer = [];     // full log history for current session
      let pendingLogRows = []; // rows waiting to be flushed to DOM
      let pendingReasoningCats = new Set(); // categories needing rerender
      let uiUpdateRequested = false;
      let planUserSteps = [];     // last-seen plan_user bullets, by session
      let activeSettingsTab = localStorage.getItem('settingsTab') || 'api';
      let providerCatalogue = [];   // [{id, label, needs_key}]
      let workingConfig = null;     // local mutable BYOK config

      // ─── Phase 6.6 state-stability flags ───────────────────────────────────
      // settingsModalOpen: while true, ALL background polling that could touch
      //   the Settings DOM is skipped. Toggled in openSettings/closeSettings.
      // userInteracting: set whenever the user types in / focuses / clicks
      //   inside the BYOK panel; cleared by a debounce. Polls also skip while set.
      let settingsModalOpen = false;
      let userInteracting = false;
      let _interactTimer = null;
      let runtimeInitialized = false;
      const MAX_LOG_LINES = 1500;
      function markInteracting() {
        userInteracting = true;
        if (_interactTimer) clearTimeout(_interactTimer);
        _interactTimer = setTimeout(() => { userInteracting = false; }, 4000);
      }

      const $ = (id) => document.getElementById(id);
      const nxDiag = () => window.NX || {};

      async function api(method, path, body) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const startedAt = performance.now();
        try {
          const res = await fetch(path, opts);
          let data;
          try { data = await res.json(); } catch { data = {}; }
          const durationMs = Math.round(performance.now() - startedAt);
          if (typeof nxDiag().logApiDiagnostic === 'function') {
            nxDiag().logApiDiagnostic({
              path,
              method,
              ok: res.ok,
              status: res.status,
              durationMs,
            });
          }
          return { ok: res.ok, status: res.status, data, durationMs };
        } catch (error) {
          const durationMs = Math.round(performance.now() - startedAt);
          if (typeof nxDiag().logApiDiagnostic === 'function') {
            nxDiag().logApiDiagnostic({
              path,
              method,
              ok: false,
              status: 0,
              durationMs,
              error: String(error && error.message ? error.message : error),
            });
          }
          throw error;
        }
      }

      function fmtTime(epoch) {
        if (!epoch) return '—';
        const d = new Date(epoch * 1000);
        return d.toLocaleString();
      }
      function fmtDur(s) {
        if (!s || s < 0) return '—';
        const m = Math.floor(s / 60), sec = Math.floor(s % 60);
        return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
      }
      function setVal(id, val) {
        const node = $(id);
        if (!node) return;
        if (val === null || val === undefined || val === '') {
          node.textContent = '—'; node.classList.add('muted');
        } else {
          node.textContent = val; node.classList.remove('muted');
        }
      }
      function escapeHtml(s) {
        return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      }
      function toast(msg, kind = 'ok') {
        const t = $('toast');
        t.textContent = msg;
        t.className = 'toast show ' + kind;
        setTimeout(() => t.className = 'toast ' + kind, 2400);
      }

      function setTask(t) { $('taskInput').value = t; $('taskInput').focus(); }

      // Phase 6.8 — provider-specific key prefix hints.
      // These are non-blocking client-side checks; the backend still does its
      // own validation and the key is only ever truly verified via Test Key.
      const KEY_HINTS = {
        gemini: { prefix: 'AIza', label: 'Gemini keys usually start with "AIza"' },
        openrouter: { prefix: 'sk-or-', label: 'OpenRouter keys start with "sk-or-"' },
        groq: { prefix: 'gsk_', label: 'Groq keys start with "gsk_"' },
      };

      function validateKeyInput(pid) {
        const inp = $('key-' + pid);
        const warn = $('warn-' + pid);
        if (!inp || !warn) return;
        const v = inp.value || '';
        const hint = KEY_HINTS[pid];
        if (!hint || !v) { warn.style.display = 'none'; return; }
        if (v.startsWith(hint.prefix)) { warn.style.display = 'none'; return; }
        warn.textContent = '⚠ ' + hint.label + '. This may not be a valid key.';
        warn.style.display = '';
      }



      // ─── Notification Bell ──────────────────────────────────────────────────────
      (function () {
        const POLL_MS = 12000;
        const TYPE_ICON = { task: '🤖', support: '🎫', billing: '💳', system: '⚙️' };
        const PRIO_ICON = { warning: '⚠️', critical: '🔴' };
        let _items = [];
        let _panelOpen = false;
        let _sseConn = null;

        function _badge(n) {
          const el = document.getElementById('nxBellBadge');
          const btn = document.getElementById('nxBellBtn');
          if (!el || !btn) return;
          if (n > 0) {
            el.style.display = '';
            el.textContent = n > 99 ? '99+' : String(n);
            btn.classList.add('has-unread');
          } else {
            el.style.display = 'none';
            btn.classList.remove('has-unread');
          }
        }

        function _timeAgo(iso) {
          if (!iso) return '';
          const d = new Date(iso + 'Z');
          const sec = Math.floor((Date.now() - d.getTime()) / 1000);
          if (sec < 60) return 'just now';
          if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
          if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
          return Math.floor(sec / 86400) + 'd ago';
        }

        function _render() {
          const list = document.getElementById('nxNotifList');
          const empty = document.getElementById('nxNotifEmpty');
          if (!list) return;
          if (!_items.length) {
            empty && (empty.style.display = '');
            list.querySelectorAll('.nx-notif-item').forEach(el => el.remove());
            return;
          }
          empty && (empty.style.display = 'none');
          list.querySelectorAll('.nx-notif-item').forEach(el => el.remove());
          _items.forEach(n => {
            const div = document.createElement('div');
            div.className = 'nx-notif-item' +
              (n.is_read ? '' : ' unread') +
              ' priority-' + (n.priority || 'info');
            div.dataset.id = n.id;
            const icon = PRIO_ICON[n.priority] || TYPE_ICON[n.type] || '🔔';
            div.innerHTML = `
        <div class="nx-notif-icon">${icon}</div>
        <div class="nx-notif-body">
          <div class="nx-notif-item-title">${_esc(n.title)}</div>
          <div class="nx-notif-item-msg">${_esc(n.message)}</div>
          <div class="nx-notif-item-time">${_timeAgo(n.created_at)}</div>
        </div>`;
            div.onclick = () => _clickItem(n);
            list.appendChild(div);
          });
        }

        function _esc(s) {
          return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        function _clickItem(n) {
          if (!n.is_read) {
            fetch('/api/notifications/' + n.id + '/read', { method: 'PATCH' }).then(r => r.json()).then(d => {
              n.is_read = true;
              _badge(d.unread || 0);
              _render();
            }).catch(() => { });
          }
          if (n.link) {
            if (n.link.startsWith('#')) {
              const tab = n.link.slice(1);
              if (typeof nxOpenPanel === 'function') nxOpenPanel(tab);
              else if (tab === 'support' && typeof openSupportTab === 'function') openSupportTab();
            } else {
              window.location.href = n.link;
            }
          }
          _closePanel();
        }

        function _closePanel() {
          _panelOpen = false;
          const p = document.getElementById('nxNotifPanel');
          if (p) p.style.display = 'none';
        }

        window.nxBellToggle = function (e) {
          e && e.stopPropagation();
          const p = document.getElementById('nxNotifPanel');
          if (!p) return;
          _panelOpen = !_panelOpen;
          p.style.display = _panelOpen ? '' : 'none';
          if (_panelOpen) _fetchNotifications();
        };

        window.nxNotifMarkAllRead = function () {
          fetch('/api/notifications/read-all', { method: 'POST' }).then(r => r.json()).then(() => {
            _items.forEach(n => n.is_read = true);
            _badge(0);
            _render();
          }).catch(() => { });
        };

        function _fetchNotifications() {
          fetch('/api/notifications?limit=30').then(r => r.json()).then(d => {
            if (!d.ok) return;
            _items = d.notifications || [];
            _badge(d.unread || 0);
            _render();
          }).catch(() => { });
        }

        function _addItem(n) {
          _items.unshift(n);
          if (_items.length > 30) _items.pop();
          const unread = _items.filter(x => !x.is_read).length;
          _badge(unread);
          _render();
          _toast(n);
        }

        function _toast(n) {
          const icon = PRIO_ICON[n.priority] || TYPE_ICON[n.type] || '🔔';
          const t = document.createElement('div');
          t.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:99999;' +
            'background:#161b22;border:1px solid #30363d;border-radius:10px;' +
            'padding:12px 16px;max-width:300px;box-shadow:0 4px 20px rgba(0,0,0,.5);' +
            'display:flex;gap:10px;align-items:flex-start;cursor:pointer;animation:nxFadeIn .25s ease;';
          t.innerHTML = `<span style="font-size:18px">${icon}</span>` +
            `<div><div style="font-size:12px;font-weight:600;color:#e6edf3;margin-bottom:2px">${_esc(n.title)}</div>` +
            `<div style="font-size:11px;color:#8b949e">${_esc(n.message.slice(0, 80))}</div></div>`;
          t.onclick = () => { t.remove(); _clickItem(n); };
          setTimeout(() => { try { t.remove(); } catch (_) { } }, 6000);
          document.body.appendChild(t);
        }

        function _connectSSE() {
          if (_sseConn) { try { _sseConn.close(); } catch (_) { } }
          try {
            const es = new EventSource('/api/notifications/stream');
            _sseConn = es;
            es.addEventListener('init', e => {
              try {
                const d = JSON.parse(e.data);
                _badge(d.unread || 0);
              } catch (_) { }
            });
            es.onmessage = e => {
              try { _addItem(JSON.parse(e.data)); } catch (_) { }
            };
            es.onerror = () => {
              es.close();
              _sseConn = null;
              setTimeout(_connectSSE, 15000);
            };
          } catch (_) { }
        }

        // Add keyframe if not present
        if (!document.getElementById('nxBellStyle')) {
          const s = document.createElement('style');
          s.id = 'nxBellStyle';
          s.textContent = '@keyframes nxFadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}';
          document.head.appendChild(s);
        }

        // Close panel when clicking outside
        document.addEventListener('click', e => {
          if (!_panelOpen) return;
          const wrap = document.getElementById('nxBellWrap');
          if (wrap && !wrap.contains(e.target)) _closePanel();
        });

        // Boot: fetch + start SSE + poll fallback
        function _boot() {
          _fetchNotifications();
          _connectSSE();
          setInterval(() => {
            if (document.hidden) return;
            _fetchNotifications();
          }, POLL_MS);
        }

        window.NX_LOAD_TASKS.push(_boot);
      })();


      // ─── Settings modal ────────────────────────────────────────────────────
      function openSettings(tab) {
        settingsModalOpen = true;
        $('settingsBackdrop').classList.add('show');
        // ALWAYS restore the tab from localStorage first; only override if the
        // caller passed an explicit tab argument (e.g. the Sessions shortcut).
        const stored = localStorage.getItem('settingsTab');
        const target = tab || stored || activeSettingsTab || 'api';
        switchSettingsTab(target);
        requestAnimationFrame(() => {
          const first = document.querySelector('#settingsModal .settings-tab.active, #settingsModal .settings-tab, #settingsModal button, #settingsModal input, #settingsModal textarea, #settingsModal select');
          if (first) first.focus();
        });
      }
      function closeSettings() {
        settingsModalOpen = false;
        $('settingsBackdrop').classList.remove('show');
      }
      function onBackdropClick(e) {
        if (e.target.id === 'settingsBackdrop') closeSettings();
      }
      function switchSettingsTab(name) {
        activeSettingsTab = name;
        localStorage.setItem('settingsTab', name);
        document.querySelectorAll('.settings-tab').forEach(t => {
          t.classList.toggle('active', t.dataset.stab === name);
        });
        document.querySelectorAll('.settings-pane').forEach(p => {
          p.classList.toggle('active', p.id === 'spane-' + name);
        });
        // Lazy-load tab data ONCE per click; never via background polls.
        if (name === 'sessions') loadSessions();
        if (name === 'memory') loadMemory();
        if (name === 'api') loadConfig(true);
        if (name === 'advanced') {
          loadBrowserAllowlist();
          loadReviewPolicy();
          loadModelRouting();
        }
        if (name === 'memory') loadLessons();
        if (name === 'billing-setup') loadWebhookStatus();
      }

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && $('settingsBackdrop').classList.contains('show')) {
          closeSettings();
          return;
        }
        if (e.key === 'Tab' && $('settingsBackdrop').classList.contains('show')) {
          const modal = document.getElementById('settingsModal') || document.querySelector('#settingsBackdrop .modal');
          if (!modal) return;
          const focusables = Array.from(modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
            .filter((el) => !el.disabled && el.offsetParent !== null);
          if (!focusables.length) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      });

      // ─── Billing Setup Guide ───────────────────────────────────────────────
      async function loadWebhookStatus() {
        const dot = $('webhookStatusDot');
        const label = $('webhookStatusLabel');
        const detail = $('webhookStatusDetail');
        const urlInput = $('webhookUrlInput');
        if (label) label.textContent = 'Checking…';
        try {
          const r = await api('GET', '/api/billing/webhook-status');
          if (!r.ok) return;
          const d = r.data;
          if (urlInput) urlInput.value = d.webhook_url || '';
          const colors = { connected: 'var(--green)', partial: 'var(--yellow)', not_configured: 'var(--red)' };
          if (dot) dot.style.background = colors[d.status] || 'var(--muted)';
          if (label) label.textContent = d.status_label || d.status;
          const parts = [];
          if (!d.has_key_id) parts.push('RAZORPAY_KEY_ID missing');
          if (!d.has_key_secret) parts.push('RAZORPAY_KEY_SECRET missing');
          if (!d.has_webhook_secret) parts.push('RAZORPAY_WEBHOOK_SECRET missing');
          if (detail) detail.textContent = parts.length ? parts.join(' · ') : 'All secrets configured.';
        } catch (e) {
          if (label) label.textContent = 'Error loading status';
        }
      }

      function copyWebhookUrl() {
        const inp = $('webhookUrlInput');
        if (!inp || !inp.value) return;
        navigator.clipboard.writeText(inp.value).then(() => showToast('Webhook URL copied!')).catch(() => {
          inp.select();
          document.execCommand('copy');
          showToast('Webhook URL copied!');
        });
      }

      // ─── Config / mode handling ────────────────────────────────────────────
      async function loadProviders() {
        if (providerCatalogue.length) return;
        const r = await api('GET', '/api/providers');
        if (r.ok) providerCatalogue = r.data.providers || [];
      }

      // Detect whether the user is currently editing the BYOK panel — if so,
      // skip re-rendering it so we don't blow away their keystrokes or focus.
      function byokPanelHasUserInput() {
        const panel = $('byokPanel');
        if (!panel) return false;
        if (panel.style.display === 'none') return false;
        const focused = document.activeElement;
        if (focused && panel.contains(focused) &&
          (focused.tagName === 'INPUT' || focused.tagName === 'TEXTAREA')) {
          return true;
        }
        for (const inp of panel.querySelectorAll('input[type="password"], input[type="text"]')) {
          if (inp.value && !inp.placeholder.startsWith('Stored:')) return true;
        }
        return false;
      }

      async function loadConfig(rebuildUi = false) {
        // Phase 6.6 hard guard #1: a background poll must NEVER touch the Settings
        // DOM while the modal is open or while the user is interacting with it.
        if (!rebuildUi && (settingsModalOpen || userInteracting)) return;

        await loadProviders();
        const r = await api('GET', '/api/get-config');
        if (!r.ok) return;
        const cfg = r.data.config;

        // Phase 6.6 hard guard #2: even on an explicit rebuild request, if the
        // user is mid-edit OR has typed values in BYOK inputs, refresh only the
        // lightweight bits (header pill + summary). Skips the full DOM rebuild
        // that would tear down the panel and lose focus / typed text.
        const editing = userInteracting || byokPanelHasUserInput();
        if (editing) {
          const mp = $('headerMode');
          mp.textContent = cfg.mode === 'byok' ? 'BYOK' : 'Managed';
          mp.className = 'mode-pill' + (cfg.mode === 'byok' ? ' byok' : '');
          summarizeMode(cfg);
          return;
        }

        workingConfig = {
          mode: cfg.mode,
          providers: [...cfg.providers],
          fallback_order: [...cfg.fallback_order],
          api_keys_set: { ...cfg.api_keys_set },
          api_keys_masked: { ...cfg.api_keys_masked },
          thinking_mode: cfg.thinking_mode !== false,
          // Phase 19 — auto goals settings
          auto_goals: cfg.auto_goals || {
            enabled: false, max_per_day: 3,
            min_confidence: 0.6, cooldown_seconds: 1800,
          },
        };
        // Phase 8 — sync the Thinking Mode toggle to the loaded config.
        const tm = $('thinkingModeToggle');
        if (tm) tm.checked = workingConfig.thinking_mode;
        // Phase 19 — sync the auto-goals toggle + render the live panel.
        const ag = $('autoGoalsToggle');
        if (ag) ag.checked = !!workingConfig.auto_goals.enabled;
        refreshGoalsPanel();

        const mp = $('headerMode');
        mp.textContent = cfg.mode === 'byok' ? 'BYOK' : 'Managed';
        mp.className = 'mode-pill' + (cfg.mode === 'byok' ? ' byok' : '');

        summarizeMode(cfg);
        renderManagedStatus(r.data.managed_available, r.data.managed_limits, r.data.managed_recent_runs);

        // Sync both mode toggles (the one in the left rail and the one in Settings)
        syncModeToggleUi(cfg.mode);

        if (rebuildUi || cfg.mode === 'byok') renderProviderList();
      }

      function syncModeToggleUi(mode) {
        $('modeManaged').classList.toggle('active', mode === 'managed');
        $('modeByok').classList.toggle('active', mode === 'byok');
        const sm = $('settingsModeManaged'), sb = $('settingsModeByok');
        if (sm) sm.classList.toggle('active', mode === 'managed');
        if (sb) sb.classList.toggle('active', mode === 'byok');
        $('managedPanel').style.display = (mode === 'managed') ? 'block' : 'none';
        $('byokPanel').style.display = (mode === 'byok') ? 'block' : 'none';
      }

      function summarizeMode(cfg) {
        const box = $('modeSummary');
        if (cfg.mode === 'managed') {
          box.innerHTML = `<b style="color:#79c0ff">Managed</b> · platform keys.<br>
            <span style="font-size:0.72rem">Open Settings to switch to your own keys.</span>`;
          box.className = 'info-box';
        } else {
          const order = (cfg.fallback_order || []).map(escapeHtml).join(' → ') || '—';
          box.innerHTML = `<b style="color:#ffa657">BYOK</b> · ${order}`;
          box.className = 'info-box';
        }
      }

      function renderManagedStatus(available, limits, recent) {
        const list = $('apiList'); list.innerHTML = '';
        let any = false;
        for (const p of providerCatalogue) {
          const ok = available[p.id];
          if (ok) any = true;
          list.insertAdjacentHTML('beforeend', `
            <div class="api-row">
                <div class="dot ${ok ? 'on' : 'off'}"></div>
                <span>${escapeHtml(p.label)}</span>
                <span class="meta">${ok ? '✔ Connected' : (p.needs_key ? '❌ No key' : '— optional')}</span>
            </div>`);
        }
        $('warnBox').style.display = any ? 'none' : 'block';
        $('limitsBox').innerHTML = `
        Max queue: <b>${limits.max_pending_in_queue}</b><br>
        Rate: <b>${limits.max_tasks_per_window}</b> tasks /
        <b>${Math.round(limits.rate_window_seconds / 60)}</b> min &nbsp;·&nbsp;
        used: <b>${recent}</b>`;
      }

      function toggleThinkingMode(enabled) {
        if (!workingConfig) return;
        markInteracting();
        workingConfig.thinking_mode = !!enabled;
        saveConfig(true, /*skipReload=*/true);
        toast(enabled ? 'Thinking mode ON' : 'Thinking mode OFF', 'ok');
      }

      // ─── Phase 19 — Self-driven goals UI ─────────────────────────────────
      async function toggleAutoGoals(enabled) {
        markInteracting();
        // Optimistic local update so the UI feels instant.
        if (workingConfig && workingConfig.auto_goals) {
          workingConfig.auto_goals.enabled = !!enabled;
        }
        const r = await api('POST', '/api/goals/toggle', { enabled: !!enabled });
        if (!r.ok) {
          toast(r.data.error || 'Failed to toggle auto-goals', 'err');
          // Roll back the toggle to the server's truth on next refresh.
          return;
        }
        if (workingConfig) workingConfig.auto_goals = r.data.auto_goals;
        toast(enabled ? 'Auto-goals ON' : 'Auto-goals OFF', 'ok');
        refreshGoalsPanel();
      }

      async function refreshGoalsPanel() {
        const statusBox = $('autoGoalsStatus');
        const listBox = $('autoGoalsList');
        if (!statusBox || !listBox) return;
        const r = await api('GET', '/api/goals?limit=20');
        if (!r.ok) {
          statusBox.textContent = r.data.error
            || 'Engine unavailable.';
          listBox.innerHTML = '';
          return;
        }
        const s = r.data.status;
        if (s) {
          const cool = s.cooldown_remaining_seconds;
          const coolStr = cool > 0
            ? `${Math.ceil(cool / 60)}m cooldown left`
            : 'ready';
          statusBox.innerHTML =
            `Status: <b>${s.enabled ? 'ON' : 'OFF'}</b> · `
            + `today ${s.today_used}/${s.settings.max_per_day} · `
            + `${escapeHtml(coolStr)} · `
            + `min confidence ${s.settings.min_confidence}` +
            (s.last_skip_reason
              ? `<br><small style="color:var(--muted)">last skip: ${escapeHtml(s.last_skip_reason)}</small>`
              : '');
        } else {
          statusBox.textContent = r.data.engine_init_error
            || 'Engine not initialised yet.';
        }
        const chains = r.data.chains || [];
        if (!chains.length) {
          listBox.innerHTML =
            '<div class="info-box" style="font-size:0.78rem">'
            + 'No system-generated goals yet.</div>';
          return;
        }
        listBox.innerHTML = chains.map(c => {
          const p = c.progress || {};
          const done = (p.completed || 0) + (p.failed || 0) + (p.skipped || 0);
          const tot = p.total || 0;
          return `
        <div class="info-box" style="font-size:0.78rem;margin-top:6px">
            <div><b>#${c.id}</b> · ${escapeHtml(c.status)}
                · conf ${Number(c.confidence).toFixed(2)}
                · <span style="color:var(--muted)">${escapeHtml(c.auto_source || '')}</span>
            </div>
            <div style="margin-top:4px">${escapeHtml((c.goal || '').slice(0, 160))}</div>
            <div style="margin-top:4px;color:var(--muted)">
                progress: ${done}/${tot}
                · created ${escapeHtml(c.created_at || '')}
            </div>
        </div>`;
        }).join('');
      }

      async function runGoalsNow() {
        const r = await api('POST', '/api/goals/run-now', {});
        if (!r.ok) {
          toast(r.data.error || 'Engine unavailable', 'err');
          return;
        }
        const reason = (r.data.result && r.data.result.reason) || '';
        const spawned = (r.data.result && r.data.result.spawned) || [];
        toast(spawned.length
          ? `Generated ${spawned.length} goal(s)`
          : (reason || 'No goals generated'), spawned.length ? 'ok' : 'info');
        refreshGoalsPanel();
      }

      function setMode(mode) {
        if (!workingConfig) return;
        // Phase 6.6: this is an EXPLICIT user click. Update local state + UI
        // immediately, but never trigger a full loadConfig reload here — that
        // was the source of the "auto switch" feel. Persisting the mode is done
        // silently in the background; UI is not rebuilt on success.
        markInteracting();
        workingConfig.mode = mode;
        syncModeToggleUi(mode);

        if (mode === 'managed') {
          saveConfig(true, /*skipReload=*/true);
        } else {
          renderProviderList();
          // If toggled from the compact left rail, open Settings so user can
          // add keys. If already inside Settings, stay put — never auto-jump.
          if (!settingsModalOpen) openSettings('api');
        }
      }

      function renderProviderList() {
        const root = $('providerList');
        if (!root || !workingConfig) return;
        root.innerHTML = '';
        const enabled = workingConfig.fallback_order.filter(p =>
          workingConfig.providers.includes(p));
        const disabled = providerCatalogue
          .map(x => x.id)
          .filter(p => !enabled.includes(p));
        const ordered = [...enabled, ...disabled];

        ordered.forEach((pid) => {
          const meta = providerCatalogue.find(x => x.id === pid);
          if (!meta) return;
          const isEnabled = workingConfig.providers.includes(pid);
          const keySet = !!workingConfig.api_keys_set[pid];
          const masked = workingConfig.api_keys_masked[pid] || '';
          const idxInEnabled = enabled.indexOf(pid);

          // Phase 6.7 — a provider is "inactive" when the user enabled it
          // but no key is stored (and it requires one). It will be auto-skipped
          // by the router but is kept visible so the user can paste a key.
          const isInactive = isEnabled && meta.needs_key && !keySet;

          const row = document.createElement('div');
          row.className = 'provider-row' + (isInactive ? ' inactive' : '');
          row.dataset.pid = pid;
          row.innerHTML = `
            <div class="provider-head">
                <input type="checkbox" id="chk-${pid}" ${isEnabled ? 'checked' : ''}>
                <label for="chk-${pid}" style="cursor:pointer;flex:1">
                    ${escapeHtml(meta.label)}
                    ${meta.needs_key ? '' : '<span style="color:var(--muted);font-size:0.7rem;margin-left:4px">(no key required)</span>'}
                    ${isInactive ? '<span class="inactive-tag" title="No key stored — this provider is skipped at runtime">No key — skipped</span>' : ''}
                </label>
                <div class="order-btns">
                    <button class="order-btn" data-act="up" ${(!isEnabled || idxInEnabled <= 0) ? 'disabled' : ''}>▲</button>
                    <button class="order-btn" data-act="down" ${(!isEnabled || idxInEnabled === enabled.length - 1) ? 'disabled' : ''}>▼</button>
                </div>
            </div>
            ${meta.needs_key ? `
            <div class="key-input-wrap">
                <input type="password" id="key-${pid}"
                    placeholder="${keySet ? 'Stored: ' + escapeHtml(masked) + '  (leave empty to keep)' : 'API key for ' + escapeHtml(meta.label)}"
                    autocomplete="off">
                <button class="btn tiny mask-toggle" data-act="show" data-pid="${pid}">show</button>
                <button class="btn tiny key-test" data-pid="${pid}" title="Verify the key against the provider">test</button>
                <button class="btn tiny danger key-delete" data-pid="${pid}" ${keySet ? '' : 'disabled'} title="Delete the stored key">delete</button>
            </div>
            <div class="key-warn" id="warn-${pid}" style="display:none"></div>
            <div class="key-test-result" id="ktres-${pid}" style="display:none"></div>
            <div class="key-status ${keySet ? 'ok' : ''}">${keySet ? '✔ key stored (' + escapeHtml(masked) + ')' : '○ no key stored yet'}</div>
            ` : '<div class="key-status" style="margin-bottom:6px">No key required (uses local Ollama). Models configured in Model Routing below.</div>'}
        `;
          root.appendChild(row);

          // Phase 6.6: every interactive control inside the BYOK panel marks
          // the user as interacting AND stops propagation so it can never
          // bubble up to the modal/backdrop or trigger an outer refresh.
          const chk = row.querySelector(`#chk-${pid}`);
          chk.addEventListener('click', (e) => { e.stopPropagation(); markInteracting(); });
          chk.addEventListener('change', (e) => {
            e.stopPropagation();
            markInteracting();
            toggleProvider(pid, e.target.checked);
          });
          row.querySelectorAll('.order-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              markInteracting();
              moveProvider(pid, btn.dataset.act);
            });
          });
          const showBtn = row.querySelector('.mask-toggle');
          if (showBtn) {
            showBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              markInteracting();
              const inp = $(`key-${pid}`);
              if (inp.type === 'password') { inp.type = 'text'; showBtn.textContent = 'hide'; }
              else { inp.type = 'password'; showBtn.textContent = 'show'; }
            });
          }
          const keyInp = row.querySelector(`#key-${pid}`);
          if (keyInp) {
            ['focus', 'input', 'keydown', 'click'].forEach(evt =>
              keyInp.addEventListener(evt, (e) => { e.stopPropagation(); markInteracting(); }));
            keyInp.addEventListener('input', () => validateKeyInput(pid));
          }

          // Phase 6.8 — Test Key
          const testBtn = row.querySelector('.key-test');
          if (testBtn) {
            testBtn.addEventListener('click', async (e) => {
              e.stopPropagation();
              markInteracting();
              await testProviderKey(pid);
            });
          }

          // Phase 6.8 — Delete Key
          const delBtn = row.querySelector('.key-delete');
          if (delBtn) {
            delBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              markInteracting();
              confirmAction(
                `Delete the saved API key for ${escapeHtml(meta.label)}?`,
                'This removes the stored key. You can paste a new one anytime.',
                () => deleteProviderKey(pid)
              );
            });
          }


        });
      }

      // ─── Phase 6.8 — Key + Ollama actions ─────────────────────────────────
      async function testProviderKey(pid) {
        const out = $('ktres-' + pid);
        if (!out) return;
        const inp = $('key-' + pid);
        const typed = (inp && inp.value || '').trim();
        out.style.display = '';
        out.className = 'key-test-result pending';
        out.textContent = '⏳ Testing…';
        const r = await api('POST', '/api/test-key', {
          provider: pid,
          key: typed,   // empty string → backend uses stored key
        });
        if (r.ok && r.data.ok) {
          out.className = 'key-test-result ok';
          out.textContent = '✔ ' + (r.data.message || 'Key works');
        } else {
          out.className = 'key-test-result err';
          out.textContent = '✘ ' + ((r.data && (r.data.message || r.data.error)) || 'Test failed');
        }
      }

      async function deleteProviderKey(pid) {
        const r = await api('DELETE', '/api/key/' + pid);
        if (!r.ok) { toast((r.data && r.data.error) || 'Failed to delete key', 'err'); return; }
        toast('API key deleted', 'ok');
        // Update local state in place so we don't tear down the panel.
        if (workingConfig) {
          workingConfig.api_keys_set[pid] = false;
          workingConfig.api_keys_masked[pid] = '';
          if (r.data && r.data.config && r.data.config.mode) {
            workingConfig.mode = r.data.config.mode;
          }
        }
        renderProviderList();
      }

      async function checkOllamaStatus() {
        const out = $('ollamaCheckResult');
        if (!out) return;
        out.style.display = '';
        out.className = 'key-test-result pending';
        out.textContent = '⏳ Checking…';
        const r = await api('GET', '/api/check-ollama');
        if (r.ok && r.data.ok) {
          const models = (r.data.models || []).slice(0, 8).join(', ');
          out.className = 'key-test-result ok';
          out.textContent = '✔ Reachable at ' + r.data.host + (models ? ' · models: ' + models : '');
        } else {
          out.className = 'key-test-result err';
          out.textContent = '✘ ' + ((r.data && (r.data.error || r.data.message)) || 'Not reachable');
        }
      }

      // ─── Phase 6.8 — In-app confirmation modal ─────────────────────────────
      function confirmAction(title, body, onConfirm) {
        let bd = $('confirmBackdrop');
        if (!bd) {
          bd = document.createElement('div');
          bd.id = 'confirmBackdrop';
          bd.className = 'modal-backdrop';
          bd.innerHTML = `
            <div class="modal small" id="confirmModal" onclick="event.stopPropagation()">
                <div class="modal-header">
                    <h3 id="confirmTitle"></h3>
                </div>
                <div class="modal-body">
                    <p id="confirmBody" style="color:#cdd6e0;line-height:1.4"></p>
                    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
                        <button class="btn" id="confirmCancel">Cancel</button>
                        <button class="btn danger" id="confirmOk">Delete</button>
                    </div>
                </div>
            </div>`;
          document.body.appendChild(bd);
          bd.addEventListener('click', (e) => {
            if (e.target === bd) closeConfirm();
          });
        }
        $('confirmTitle').textContent = title;
        $('confirmBody').textContent = body;
        bd.classList.add('show');
        const okBtn = $('confirmOk');
        const cancelBtn = $('confirmCancel');
        const cleanup = () => { okBtn.onclick = null; cancelBtn.onclick = null; closeConfirm(); };
        okBtn.onclick = (e) => { e.stopPropagation(); cleanup(); try { onConfirm(); } catch (err) { console.error(err); } };
        cancelBtn.onclick = (e) => { e.stopPropagation(); cleanup(); };
      }

      function closeConfirm() {
        const bd = $('confirmBackdrop');
        if (bd) bd.classList.remove('show');
      }

      function toggleProvider(pid, enabled) {
        if (enabled) {
          if (!workingConfig.providers.includes(pid))
            workingConfig.providers.push(pid);
          if (!workingConfig.fallback_order.includes(pid))
            workingConfig.fallback_order.push(pid);
        } else {
          workingConfig.providers = workingConfig.providers.filter(p => p !== pid);
          workingConfig.fallback_order = workingConfig.fallback_order.filter(p => p !== pid);
        }
        renderProviderList();
      }

      function moveProvider(pid, dir) {
        const order = [...workingConfig.fallback_order];
        const i = order.indexOf(pid);
        if (i < 0) return;
        const j = dir === 'up' ? i - 1 : i + 1;
        if (j < 0 || j >= order.length) return;
        [order[i], order[j]] = [order[j], order[i]];
        workingConfig.fallback_order = order;
        renderProviderList();
      }

      async function saveConfig(silent = false, skipReload = false) {
        if (!workingConfig) return;
        const api_keys = {};
        for (const p of providerCatalogue) {
          if (!p.needs_key) continue;
          const inp = $(`key-${p.id}`);
          if (!inp) continue;
          const v = inp.value;
          if (v === '' && workingConfig.api_keys_set[p.id]) {
            api_keys[p.id] = '__keep__';
          } else if (v !== '') {
            api_keys[p.id] = v;
          }
        }
        const payload = {
          mode: workingConfig.mode,
          providers: workingConfig.providers,
          fallback_order: workingConfig.fallback_order,
          api_keys,
          thinking_mode: workingConfig.thinking_mode !== false,
          //Phase 19 — persist auto_goals settings round-trip
          auto_goals: workingConfig.auto_goals || {
            enabled: false, max_per_day: 3,
            min_confidence: 0.6, cooldown_seconds: 1800,
          },
        };
        const r = await api('POST', '/api/set-config', payload);
        if (!r.ok) {
          toast(r.data.error || 'Failed to save config', 'err');
          return;
        }
        if (!silent) toast('Configuration saved', 'ok');

        // Phase 6.9 — fix "no key stored" state mismatch after save.
        // The set-config response IS the source of truth; trust it directly
        // instead of going through loadConfig (which bails out via the
        // byokPanelHasUserInput guard while the user's typed key is still
        // sitting in the input field). We also clear the inputs here so the
        // panel reflects "stored: ****" on the very next render.
        const cfg = r.data && r.data.config;
        if (cfg) {
          workingConfig.mode = cfg.mode;
          workingConfig.providers = [...cfg.providers];
          workingConfig.fallback_order = [...cfg.fallback_order];
          workingConfig.api_keys_set = { ...cfg.api_keys_set };
          workingConfig.api_keys_masked = { ...cfg.api_keys_masked };
          // Clear typed key inputs — their values are now persisted server-side.
          for (const p of providerCatalogue) {
            if (!p.needs_key) continue;
            const inp = $(`key-${p.id}`);
            if (inp) inp.value = '';
            const warn = $('warn-' + p.id);
            if (warn) warn.style.display = 'none';
          }
          // Re-release the interaction lock now that inputs are clean —
          // loadConfig() / polling can resume normally.
          userInteracting = false;
          renderProviderList();
          // Update the header pill + summary so the rest of the UI stays in sync.
          const mp = $('headerMode');
          if (mp) {
            mp.textContent = cfg.mode === 'byok' ? 'BYOK' : 'Managed';
            mp.className = 'mode-pill' + (cfg.mode === 'byok' ? ' byok' : '');
          }
          summarizeMode(cfg);
        } else if (!skipReload) {
          // Fallback path — only used if the server omitted the config echo.
          await loadConfig(true);
        }
      }

      // ─── Tasks / Sessions ──────────────────────────────────────────────────
      async function queueTask() {
        const task = $('taskInput').value.trim();
        if (!task) { toast('Please enter a task.', 'err'); return; }
        const model = $('modelSelect') ? $('modelSelect').value : '';
        const r = await api('POST', '/api/queue-task', { task, model: model || null });
        if (!r.ok) { toast(r.data.error || 'Failed to queue', 'err'); return; }
        $('taskInput').value = '';
        selectSession(r.data.session_id);
        loadSessions(); loadQueue();
      }

      async function loadQueue() {
        const r = await api('GET', '/api/queue');
        if (!r.ok) return;
        const ql = $('queueList');
        const pending = r.data.pending || [];
        const run = r.data.running;
        const btn = $('runBtn');
        const lbl = $('runBtnLabel');
        if (!run && !pending.length) {
          ql.textContent = 'Idle';
          ql.style.color = 'var(--muted)';
          if (btn) btn.classList.remove('is-running');
          if (lbl) lbl.textContent = '▶ Run Task';
        } else {
          let parts = [];
          if (run) parts.push('Running');
          if (pending.length) parts.push(`${pending.length} pending`);
          ql.textContent = parts.join(' · ');
          ql.style.color = 'var(--accent)';
          // Phase 6.7 — turn the Run button into a live indicator while
          // a task is running. Keep it clickable so users can queue more.
          if (btn) btn.classList.toggle('is-running', !!run);
          if (lbl) lbl.textContent = run ? 'Running…  (queue more)' : '▶ Queue Task';
        }
      }

      // Phase 6.7 — running indicator + step + model badges
      function syncRunningIndicators(s, model) {
        const headerPill = $('headerMode');
        const stepBadge = $('stStepBadge');
        const modelBadge = $('stModelBadge');
        const runBtn = $('runBtn');

        if (s.is_running) {
          headerPill.textContent = 'Running';
          headerPill.className = 'mode-pill running';
          if (runBtn) runBtn.classList.add('is-running');
        } else {
          // Restore the mode pill to whatever the working config says
          const md = (workingConfig && workingConfig.mode) || s.mode || 'managed';
          headerPill.textContent = md === 'byok' ? 'BYOK' : 'Managed';
          headerPill.className = 'mode-pill' + (md === 'byok' ? ' byok' : '');
        }

        if (s.step) {
          stepBadge.textContent = 'Step ' + s.step;
          stepBadge.style.display = '';
        } else {
          stepBadge.style.display = 'none';
        }

        if (model) {
          modelBadge.textContent = model;
          modelBadge.style.display = '';
        } else {
          modelBadge.style.display = 'none';
        }
      }

      async function loadSessions() {
        const r = await api('GET', '/api/sessions');
        if (!r.ok) return;
        const list = r.data.sessions || [];
        $('sessCount').textContent = list.length;
        const root = $('sessionsList');
        if (!root) return;
        if (!list.length) {
          root.innerHTML = '<div class="empty" style="padding:20px 0">No sessions yet.</div>';
          return;
        }
        root.innerHTML = list.map(s => {
          const st = s.status || 'idle';
          const md = s.mode || 'managed';
          return `
        <div class="session-item ${currentSession === s.id ? 'active' : ''}" onclick="selectSession('${s.id}'); closeSettings();">
            <div class="title" title="${escapeHtml(s.task)}">${escapeHtml(s.task)}</div>
            <div class="meta">
                <span class="pill ${st}">${st}</span>
                <span class="pill ${md}">${md}</span>
                <span class="ts">${fmtTime(s.created_at)}</span>
            </div>
        </div>`;
        }).join('');
      }

      async function selectSession(sid) {
        currentSession = sid;
        lastLogSeq = 0;
        logBuffer = [];
        planUserSteps = [];
        renderPlanUser([]);
        closeLogStream();
        // Phase 7.2 — reset per-session UI state when switching sessions so we
        // don't leak the previous session's tree, viewer, or preview iframe.
        lastFilesSig = '';
        openFilePath = null;
        // Phase 21.1 — also drop tree selection so the FS toolbar doesn't act
        // on a node that belonged to the previous session, and refresh the
        // toolbar so create-buttons enable as soon as a session exists.
        selectedNodePath = null;
        selectedNodeKind = null;
        _updateFsToolbarState();
        $('fileTree').innerHTML = '<div class="ft-empty">Loading…</div>';
        $('fileViewerPath').textContent = 'Pick a file from the tree';
        $('fileViewerMeta').textContent = '';
        $('fileViewerBody').innerHTML =
          '<div class="empty">Select a file on the left to view its contents.</div>';
        if (window.CodeEditor) window.CodeEditor.reset();
        $('previewIframe').src = 'about:blank';
        $('logArea').innerHTML = '<div class="empty" id="logsEmpty">Loading logs…</div>';
        $('decisionList').innerHTML = '<div class="empty" style="padding:16px 0">Loading…</div>';
        setVal('stSession', sid);
        document.querySelectorAll('.session-item').forEach(el => {
          el.classList.toggle('active', el.getAttribute('onclick')?.includes(sid));
        });
        // Phase 13 — open the live SSE tail AFTER the initial poll so we
        // resume from a known cursor (lastLogSeq is set by renderLogs).
        // FIX: Add safety timeout to clear "Loading..." states if network stalls
        const _fallback = setTimeout(() => {
          if ($('fileTree').innerHTML.includes('Loading')) $('fileTree').innerHTML = '<div class="ft-empty">No files available.</div>';
          if ($('logArea').innerHTML.includes('Loading logs')) $('logArea').innerHTML = '<div class="empty" id="logsEmpty">No logs available.</div>';
          if ($('decisionList').innerHTML.includes('Loading')) $('decisionList').innerHTML = '<div class="empty" style="padding:16px 0">No decisions yet.</div>';
        }, 3000);

        refreshSession().then(() => {
          clearTimeout(_fallback);
          // Make sure empty placeholders are set if data was empty
          if ($('fileTree').innerHTML.includes('Loading')) $('fileTree').innerHTML = '<div class="ft-empty">Empty project</div>';
          if ($('logArea').innerHTML.includes('Loading logs')) $('logArea').innerHTML = '<div class="empty" id="logsEmpty">Waiting for activity...</div>';
          if ($('decisionList').innerHTML.includes('Loading')) $('decisionList').innerHTML = '<div class="empty" style="padding:16px 0">No decisions yet.</div>';
          openLogStream(sid);
        }).catch(err => {
          clearTimeout(_fallback);
          console.error("[Session] Error refreshing session state:", err);
        });
      }

      async function refreshSession() {
        if (!currentSession) return;
        const [s, l, d, p] = await Promise.all([
          api('GET', `/api/session/${currentSession}`),
          api('GET', `/api/logs?session_id=${currentSession}&since=${lastLogSeq}`),
          api('GET', `/api/decisions?session_id=${currentSession}`),
          api('GET', `/api/preview/${currentSession}`),
        ]);
        if (s.ok) renderStatus(s.data);
        if (l.ok) renderLogs(l.data);
        if (d.ok) renderDecisions(d.data.decisions || []);
        if (p.ok) onFilesUpdate(p.data);
      }

      function renderStatus(s) {
        const pill = $('stStatus');
        const cls = s.status || 'idle';
        pill.className = 'pill ' + cls;
        pill.textContent = cls.charAt(0).toUpperCase() + cls.slice(1);
        $('pulse').classList.toggle('live', s.is_running);

        const md = s.mode || (s.config && s.config.mode) || 'managed';
        setVal('stMode', md);

        setVal('stStage', s.stage);
        setVal('stStep', s.step);
        const model = s.current_model || s.model;
        setVal('stModel', model);
        $('stRetry').textContent = s.retry_count || 0;
        setVal('stError', s.error_category);
        if (s.is_running && s.started_at) {
          $('stElapsed').textContent = fmtDur(Date.now() / 1000 - s.started_at);
        } else if (s.started_at && s.finished_at) {
          $('stElapsed').textContent = fmtDur(s.finished_at - s.started_at);
        } else {
          $('stElapsed').textContent = '—';
        }

        // Phase 6.7 — live feedback in header + status header
        syncRunningIndicators(s, model);

        const valBox = $('outValidation');
        if (s.validation) { valBox.textContent = s.validation; valBox.classList.remove('muted'); }
        else { valBox.textContent = 'Nothing yet.'; valBox.classList.add('muted'); }

        const resBox = $('outResult');
        if (s.result) { resBox.textContent = s.result; resBox.classList.remove('muted'); }
        else if (!s.is_running && s.exit_code !== null && s.exit_code !== undefined) {
          resBox.textContent = `Task ended (exit code ${s.exit_code}).`;
          resBox.classList.remove('muted');
        } else { resBox.textContent = 'Nothing yet.'; resBox.classList.add('muted'); }

        renderUsage(s.usage, s);

        $('actStop').disabled = !(s.is_running || s.is_queued);
        $('actRestart').disabled = false;
        $('actDelete').disabled = s.is_running;

        // Phase 21.1 polish — surface the derived project name in the header
        // pill.  Server returns `name` on /api/session/<sid> via _derive_project_name.
        const projPill = $('projectPill'), projName = $('projectName');
        if (projPill && projName) {
          if (s.name) {
            projName.textContent = s.name;
            projName.title = (s.task || '').slice(0, 200);
            projPill.style.display = 'inline-flex';
          } else {
            projPill.style.display = 'none';
          }
        }
      }

      // Quiet mode: keep only key markers + errors/validation/success
      const QUIET_KEEP_RE = /(ROUTE|VALIDATION|ESCALATION|FINAL CHECK|STAGE|STEP|MODE|FALLBACK|RETRY|ERROR|FAIL|SUCCESS)/i;
      function shouldShowLine(e) {
        if (!$('quietMode').checked) return true;
        if (e.level === 'error' || e.level === 'validation' || e.level === 'success') return true;
        if (e.level === 'system') return true;
        return QUIET_KEEP_RE.test(e.text || '');
      }

      let pendingLogs = [];
      let logUpdateTimer = null;

      function appendLogLine(area, e) {
        const div = document.createElement('div');
        div.className = 'log-line ' + (e.level || 'log');
        div.textContent = e.text;
        area.appendChild(div);
      }

      async function loadOlderLogs() {
        const btn = $('loadMoreLogs');
        if (!logBuffer.length) return;
        const firstSeq = logBuffer[0].seq;
        if (btn) {
          btn.textContent = 'Loading...';
          btn.disabled = true;
        }
        const r = await api('GET', `/api/logs?session_id=${currentSession}&before=${firstSeq}`);
        if (r.ok && r.data.logs && r.data.logs.length) {
          logBuffer.unshift(...r.data.logs);
          rerenderLogsFromBuffer();
        } else {
          if ($('loadMoreLogs')) $('loadMoreLogs').remove();
        }
      }

      function rerenderLogsFromBuffer() {
        const area = $('logArea');
        area.innerHTML = '';
        if (!logBuffer.length) {
          const d = document.createElement('div');
          d.className = 'empty'; d.id = 'logsEmpty';
          d.textContent = 'Enter a task and click Run to start 🚀';
          area.appendChild(d);
          return;
        }

        if (logBuffer[0].seq > 1) {
          const btn = document.createElement('button');
          btn.id = 'loadMoreLogs';
          btn.className = 'btn tiny';
          btn.textContent = 'Load older logs...';
          btn.style.marginBottom = '10px';
          btn.onclick = loadOlderLogs;
          area.appendChild(btn);
        }

        for (const e of logBuffer) {
          if (shouldShowLine(e)) appendLogLine(area, e);
        }
        if ($('autoScroll').checked) area.scrollTop = area.scrollHeight;
      }

      function renderLogs(data) {
        const logs = data.logs || [];
        if (logs.length) pendingLogs.push(...logs);
        lastLogSeq = data.last_seq || lastLogSeq;

        if (!logUpdateTimer) {
          logUpdateTimer = setTimeout(() => {
            const area = $('logArea');
            const empty = $('logsEmpty');
            if (!pendingLogs.length) {
              logUpdateTimer = null;
              return;
            }
            if (empty) empty.remove();

            if (logBuffer.length === 0 && pendingLogs.length > 0 && pendingLogs[0].seq > 1) {
              if (!$('loadMoreLogs')) {
                const btn = document.createElement('button');
                btn.id = 'loadMoreLogs';
                btn.className = 'btn tiny';
                btn.textContent = 'Load older logs...';
                btn.style.marginBottom = '10px';
                btn.onclick = loadOlderLogs;
                area.appendChild(btn);
              }
            }

            for (const e of pendingLogs) ingestLogRow(e, area);
            pendingLogs = [];
            logUpdateTimer = null;
            if ($('autoScroll').checked) area.scrollTop = area.scrollHeight;
          }, 500);
        }
      }

      // Phase 13 — single ingestion path used by BOTH polling (renderLogs)
      // and SSE (openLogStream). Keeps de-dup, quietMode, and plan_user
      // parsing in one place so the two sources stay perfectly in sync.
      function ingestLogRow(e, area) {
        if (!e || typeof e.seq !== 'number') return;
        if (logBuffer.length && e.seq <= logBuffer[logBuffer.length - 1].seq) return;
        if (e.seq <= lastLogSeq) return;
        
        logBuffer.push(e);
        if (e.seq > lastLogSeq) lastLogSeq = e.seq;

        // If area is provided, we are doing a bulk rerender (not streaming)
        if (area) {
           processLogSideEffects(e);
           if (shouldShowLine(e)) appendLogLine(area, e);
           return;
        }

        // Streaming mode: batch the updates
        pendingLogRows.push(e);
        requestUIUpdate();
      }

      function requestUIUpdate() {
        if (uiUpdateRequested) return;
        uiUpdateRequested = true;
        requestAnimationFrame(flushUIUpdates);
      }

      function flushUIUpdates() {
        uiUpdateRequested = false;
        
        // 1. Flush Logs
        if (pendingLogRows.length) {
          const area = $('logArea');
          if (area) {
            const fragment = document.createDocumentFragment();
            const rows = pendingLogRows;
            pendingLogRows = [];
            rows.forEach(e => {
              processLogSideEffects(e);
              if (shouldShowLine(e)) {
                const div = document.createElement('div');
                div.className = 'log-line ' + (e.level || 'log');
                div.textContent = e.text;
                fragment.appendChild(div);
              }
            });
            area.appendChild(fragment);

            // Rolling log cap to prevent DOM bloat in long sessions
            if (area.children.length > MAX_LOG_LINES) {
              const toRemove = area.children.length - MAX_LOG_LINES;
              for (let i = 0; i < toRemove; i++) {
                if (area.firstChild) area.removeChild(area.firstChild);
              }
            }

            if ($('autoScroll') && $('autoScroll').checked) {
              area.scrollTop = area.scrollHeight;
            }
          } else {
            pendingLogRows = [];
          }
        }

        // 2. Flush Reasoning Tabs
        if (pendingReasoningCats.size > 0) {
          const cats = Array.from(pendingReasoningCats);
          pendingReasoningCats.clear();
          cats.forEach(renderReasoningCategory);
        }
      }

      function processLogSideEffects(e) {
        // Phase 30 Observability parsing
        if (e.text && e.text.startsWith('[Phase 30 Observability]')) {
          try {
            const obs = JSON.parse(e.text.substring(e.text.indexOf('{')));
            if (obs.iterations !== undefined) setVal('stIterations', obs.iterations);
            if (obs.commands !== undefined) setVal('stCommands', obs.commands);
            if (obs.termination_reason) setVal('stTermReason', obs.termination_reason);
          } catch (err) { }
        }

        const evt = parseOrchestratorEvent(e.text || '');
        if (evt) routeReasoningEvent(e, evt);
      }

      // Phase 13 (A) — single parser for any log line of the form
      //   "[ORCHESTRATOR] {kind:'...', ...payload}"
      // Returns the parsed object or null. Strict prefix check stops
      // arbitrary log producers from spoofing the reasoning panels.
      function parseOrchestratorEvent(text) {
        if (!text || !text.startsWith('[ORCHESTRATOR]')) return null;
        const i = text.indexOf('{');
        if (i < 0) return null;
        try {
          const obj = JSON.parse(text.slice(i));
          return (obj && typeof obj.kind === 'string') ? obj : null;
        } catch (_) { return null; }
      }

      // Map orchestrator event kinds → reasoning-tab category.
      // Kinds not in this table are ignored by the reasoning UI (they
      // still appear in the regular Logs tab).
      const REASONING_CATEGORY = {
        think: 'thoughts',
        meta_decide: 'thoughts',
        decide: 'decisions',
        adapt: 'decisions',
        replan: 'decisions',
        // Phase 14 — Tool Decision Layer events surface in Decisions
        // because picking *which tool to pre-fetch* is conceptually a
        // strategy choice, not a post-hoc reflection.
        tool_decision: 'decisions',
        tool_execution: 'decisions',
        reflect: 'reflections',
        verify: 'reflections',
        // Phase 15 — tool-outcome learning lands in Reflections because
        // it's a *post-execution* judgement ("was this tool helpful?")
        // analogous to verify() and reflect(). The orchestrator emits
        // one tool_learning per finished prefetched sub-task.
        tool_learning: 'reflections',
        // Phase 16 — Multi-Agent Collaboration role banners. Each event
        // carries {kind, role, payload} so the UI can show a uniform
        // "agent X did Y" trail without inventing a new section:
        //   • planner  → Decisions  (planning IS a decision)
        //   • executor → Thoughts   (in-flight work + outcome trail)
        //   • critic   → Reflections (post-hoc judgement + suggestion)
        //   • memory   → Recall      (semantic recall + tool history)
        agent_planner: 'decisions',
        agent_executor: 'thoughts',
        agent_critic: 'reflections',
        agent_memory: 'recall',
        // Phase 17 — Autonomous Task Chains. Each chain task moves through
        // pending → running → completed, surfaced through three events:
        //   • task_created   → Decisions   (planner-driven decomposition)
        //   • task_progress  → Thoughts    (in-flight transitions)
        //   • task_completed → Reflections (terminal verdict per task)
        //   • task_priority  → Decisions   (Phase 18 — scheduling choice)
        //   • task_retry     → Thoughts    (Phase 18 — corrective in-flight)
        //   • task_replan    → Reflections (Phase 18 — post-failure rethink)
        task_created: 'decisions',
        task_progress: 'thoughts',
        task_completed: 'reflections',
        task_priority: 'decisions',
        task_retry: 'thoughts',
        task_replan: 'reflections',
      };

      function routeReasoningEvent(e, payload) {
        if (!payload) return;
        if (payload.kind === 'plan_user') {
          const steps = Array.isArray(payload.steps) ? payload.steps : [];
          if (steps.length) {
            planUserSteps = steps.slice(0, 5).map(String);
            renderPlanUser(planUserSteps);
          }
          return;
        }
        // Phase 13C — recall has a structured payload (per-collection
        // hits + top doc) that doesn't fit the generic "1 line per kind"
        // shape. Route it into its own grouped renderer instead of
        // squashing it through summarizeReasoningPayload.
        if (payload.kind === 'recall') {
          pushRecallEntry(e.ts, payload);
          return;
        }
        const cat = REASONING_CATEGORY[payload.kind];
        if (!cat) return;
        pushReasoningEntry(cat, e.ts, payload);
      }

      function renderPlanUser(steps) {
        const box = $('planUserBox');
        const list = $('planUserList');
        if (!box || !list) return;
        if (!steps || !steps.length) {
          list.innerHTML = '';
          box.classList.add('hidden');
          return;
        }
        list.innerHTML = steps.map(s => {
          const div = document.createElement('li');
          div.textContent = s;
          return div.outerHTML;
        }).join('');
        box.classList.remove('hidden');
      }

      // ── Phase 13 (A) — Reasoning panels ─────────────────────────────────
      const REASONING_CAP = 50;          // max entries kept per category
      const reasoningBuffers = { thoughts: [], decisions: [], reflections: [] };
      let reasoningUnseen = 0;           // events since user last viewed tab

      // Pull the most informative human-readable line from a reasoning
      // payload. We never trust LLM-shaped payloads to render as HTML —
      // always go through textContent on the DOM side.
      function summarizeReasoningPayload(p) {
        if (!p || typeof p !== 'object') return '';
        // Most-specific fields first, in order of usefulness.
        const candidates = [
          p.reason, p.reasoning, p.summary, p.message, p.note,
          p.choice, p.next_strategy, p.strategy, p.role, p.subtask,
          p.warn, p.error,
        ];
        for (const c of candidates) {
          if (typeof c === 'string' && c.trim()) return c.trim();
        }
        // Verify-style payloads have ok / details.
        if (typeof p.ok === 'boolean') {
          const tail = p.details ? ` — ${String(p.details).slice(0, 120)}` : '';
          return (p.ok ? 'verified ✓' : 'verify failed ✗') + tail;
        }
        // Fallback: compact JSON of payload minus the kind key.
        const { kind, ...rest } = p;
        try { return JSON.stringify(rest).slice(0, 240); }
        catch (_) { return ''; }
      }

      function pushReasoningEntry(category, ts, payload) {
        const buf = reasoningBuffers[category];
        if (!buf) return;
        const entry = {
          ts: ts || (Date.now() / 1000),
          kind: payload.kind,
          text: summarizeReasoningPayload(payload),
        };
        if (!entry.text) return;
        buf.push(entry);
        if (buf.length > REASONING_CAP) buf.shift();
        
        // Queue the category for batch render
        pendingReasoningCats.add(category);
        requestUIUpdate();

        if (activeTab !== 'reasoning') {
          reasoningUnseen += 1;
          const badge = $('reasoningBadge');
          if (badge) {
            badge.textContent = reasoningUnseen > 99 ? '99+' : String(reasoningUnseen);
            badge.classList.add('show');
          }
        }
      }

      function renderReasoningCategory(category) {
        const idMap = {
          thoughts: ['rsListThoughts', 'rsCountThoughts'],
          decisions: ['rsListDecisions', 'rsCountDecisions'],
          reflections: ['rsListReflections', 'rsCountReflections'],
        };
        const ids = idMap[category];
        if (!ids) return;
        const list = $(ids[0]); const count = $(ids[1]);
        const buf = reasoningBuffers[category];
        if (count) count.textContent = String(buf.length);
        if (!list) return;
        if (!buf.length) {
          list.innerHTML = '<div class="rs-empty">Nothing here yet.</div>';
          return;
        }
        // Render newest at the top so live updates are visible without scrolling.
        list.innerHTML = '';
        for (let i = buf.length - 1; i >= 0; i--) {
          const e = buf[i];
          const row = document.createElement('div'); row.className = 'rs-item';
          const time = document.createElement('span'); time.className = 'rs-time';
          time.textContent = formatHHMMSS(e.ts);
          const kind = document.createElement('span'); kind.className = 'rs-kind';
          kind.textContent = e.kind;
          const txt = document.createElement('span'); txt.className = 'rs-text';
          txt.textContent = e.text;
          row.appendChild(time); row.appendChild(kind); row.appendChild(txt);
          list.appendChild(row);
        }
      }

      function formatHHMMSS(ts) {
        const d = new Date((ts || 0) * 1000);
        if (isNaN(d.getTime())) return '';
        const pad = n => String(n).padStart(2, '0');
        return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      }

      // ── Phase 13C — Prior Experience (semantic recall) buffer + render
      // The orchestrator emits ONE recall event per run, structured as:
      //   {kind:"recall", task, hits:{tasks:N, solutions:N, errors:N,
      //    reflections:N}, top:{tasks:"…", solutions:"…", …}}
      // We render each event as a card with one row per group, showing the
      // top match (truncated) and the total hit count. Empty groups are
      // rendered greyed-out so the user can see *why* a group had no
      // influence on the plan (vs. a missing UI bug).
      const RECALL_CAP = 8;
      const RECALL_GROUPS = ['tasks', 'solutions', 'errors', 'reflections'];
      const recallBuffer = [];

      function pushRecallEntry(ts, payload) {
        const hits = (payload && payload.hits) || {};
        const top = (payload && payload.top) || {};
        // Skip a totally-empty recall — the vector store had nothing to
        // say about this task and there's no transparency value in
        // showing four blank rows.
        const total = RECALL_GROUPS.reduce(
          (a, k) => a + (Number(hits[k]) || 0), 0);
        if (total === 0) return;
        recallBuffer.push({
          ts: ts || (Date.now() / 1000),
          task: typeof payload.task === 'string' ? payload.task : '',
          hits, top, total,
        });
        if (recallBuffer.length > RECALL_CAP) recallBuffer.shift();
        renderRecall();
        if (activeTab !== 'reasoning') {
          reasoningUnseen += 1;
          const badge = $('reasoningBadge');
          if (badge) {
            badge.textContent = reasoningUnseen > 99 ? '99+' : String(reasoningUnseen);
            badge.classList.add('show');
          }
        }
      }

      function renderRecall() {
        const list = $('rsListRecall');
        const count = $('rsCountRecall');
        if (count) count.textContent = String(recallBuffer.length);
        if (!list) return;
        if (!recallBuffer.length) {
          list.innerHTML = '<div class="rs-empty">No recalls yet — memory pulls related past tasks at the start of each run.</div>';
          return;
        }
        list.innerHTML = '';
        // Newest at the top so the most recent recall is always visible.
        for (let i = recallBuffer.length - 1; i >= 0; i--) {
          const r = recallBuffer[i];
          const card = document.createElement('div'); card.className = 'recall-card';

          const head = document.createElement('div'); head.className = 'recall-card-head';
          const taskEl = document.createElement('span'); taskEl.className = 'recall-task';
          taskEl.textContent = r.task ? `for: ${r.task}` : 'recall';
          const timeEl = document.createElement('span'); timeEl.className = 'recall-time';
          timeEl.textContent = formatHHMMSS(r.ts);
          head.appendChild(taskEl); head.appendChild(timeEl);
          card.appendChild(head);

          const groups = document.createElement('div'); groups.className = 'recall-groups';
          for (const key of RECALL_GROUPS) {
            const n = Number(r.hits[key]) || 0;
            const doc = (typeof r.top[key] === 'string') ? r.top[key].trim() : '';
            const g = document.createElement('div'); g.className = 'recall-group';
            const gh = document.createElement('div'); gh.className = 'recall-group-head';
            const lab = document.createElement('span'); lab.className = 'recall-label';
            lab.textContent = key;
            const hits = document.createElement('span'); hits.className = 'recall-hits';
            hits.textContent = n + ' hit' + (n === 1 ? '' : 's');
            gh.appendChild(lab); gh.appendChild(hits);
            const dEl = document.createElement('div');
            dEl.className = 'recall-doc' + (doc && n > 0 ? '' : ' empty');
            // Collapse internal whitespace so the 2-line clamp shows
            // as much signal as possible per row.
            dEl.textContent = (doc && n > 0)
              ? doc.replace(/\s+/g, ' ')
              : '— no match —';
            g.appendChild(gh); g.appendChild(dEl);
            groups.appendChild(g);
          }
          card.appendChild(groups);
          list.appendChild(card);
        }
      }

      function clearReasoningBadge() {
        reasoningUnseen = 0;
        const badge = $('reasoningBadge');
        if (badge) { badge.textContent = ''; badge.classList.remove('show'); }
      }

      function clearReasoning() {
        for (const k of Object.keys(reasoningBuffers)) {
          reasoningBuffers[k] = [];
          renderReasoningCategory(k);
        }
        recallBuffer.length = 0;
        renderRecall();
        clearReasoningBadge();
      }

      function openLogStream(sid) {
        // Race guard: refreshSession() is async; if the user clicked a
        // different session before it resolved, our `sid` is stale —
        // bail out so we don't open a stream for a session the user has
        // already left (and don't close the active session's stream).
        if (!sid || currentSession !== sid) return;
        closeLogStream();
        if (!('EventSource' in window)) return;
        let es;
        try {
          es = new EventSource(`/api/session/${sid}/stream?since=${lastLogSeq}`);
        } catch (_) { return; }
        logStream = es;
        es.onmessage = (ev) => {
          if (currentSession !== sid) {
            try { es.close(); } catch (_) { }
            if (logStream === es) logStream = null;
            return;
          }
          let row;
          try { row = JSON.parse(ev.data); } catch (_) { return; }
          ingestLogRow(row);
        };
        es.addEventListener('end', () => closeLogStream());
        es.onerror = () => {
          // Proxy drop / idle timeout / server restart — let the 1.2s
          // polling loop pick up where we left off. No data lost
          // because /api/logs?since=lastLogSeq is the same cursor.
          closeLogStream();
        };
      }

      function closeLogStream() {
        if (logStream) {
          try { logStream.close(); } catch (_) { }
          logStream = null;
        }
      }

      function renderUsage(u, s) {
        const box = $('outUsage');
        if (!u || (!u.calls && !u.tokens_out_est && !u.elapsed_seconds)) {
          box.textContent = 'No calls yet.'; box.classList.add('muted'); return;
        }
        box.classList.remove('muted');
        const inT = u.tokens_in_est || 0, outT = u.tokens_out_est || 0;
        const elapsed = u.elapsed_seconds != null
          ? `${u.elapsed_seconds}s`
          : (s && s.is_running && s.started_at)
            ? `${Math.round(Date.now() / 1000 - s.started_at)}s (live)`
            : '—';
        const byModel = u.by_model || {};
        const modelLines = Object.keys(byModel).length
          ? Object.entries(byModel).map(([m, v]) =>
            `  · ${m}: ${v.calls} call${v.calls === 1 ? '' : 's'}, ~${v.tokens_out_est} out`).join('\n')
          : '  · (no model calls recorded yet)';
        box.textContent =
          `calls: ${u.calls || 0}\n` +
          `tokens (est): ${inT} in + ${outT} out = ${inT + outT}\n` +
          `elapsed: ${elapsed}\n` +
          `by model:\n${modelLines}`;
      }

      function renderDecisions(decisions) {
        const root = $('decisionList');
        if (!decisions.length) {
          root.innerHTML = '<div class="empty" style="padding:16px 0">No decisions yet.</div>';
          return;
        }
        root.innerHTML = decisions.map(d => {
          const cls = (d.kind || '').toLowerCase().replace(' ', '');
          const map = { route: 'route', escalation: 'escalation', fallback: 'fallback', validation: 'validation', finalcheck: 'final' };
          const css = map[cls] || 'route';
          const t = new Date(d.ts * 1000).toLocaleTimeString();
          return `
        <div class="decision ${css}">
            <span class="kind">${escapeHtml(d.kind)}</span>
            <span class="ts">${t}</span>
            <div class="detail">${escapeHtml(d.detail)}</div>
        </div>`;
        }).join('');
      }

      // ── Phase 7.2 — tabbed dev environment (Logs / Preview / Files) ─────────────
      //
      // Tab activation, file tree, file viewer, preview iframe, download, and
      // auto-refresh-on-file-change all live below. The old "Result Preview" pane
      // (file-chips + sandboxed iframe in a split row) is gone — Preview is now
      // just an iframe pointing at /preview/<sid>/ which auto-picks index.html.

      let activeTab = localStorage.getItem('activeTab') || 'logs';
      let lastFilesSig = '';        // hash of last-seen file list — drives refresh
      let openFilePath = null;      // currently displayed file in Files tab

      function setPreviewState(state, title, copy) {
        const shell = $('previewShell');
        const overlay = $('previewState');
        const titleEl = $('previewStateTitle');
        const copyEl = $('previewStateCopy');
        if (!shell || !overlay) return;
        overlay.dataset.state = state;
        overlay.classList.toggle('is-hidden', state === 'ready');
        if (titleEl && title) titleEl.textContent = title;
        if (copyEl && copy) copyEl.textContent = copy;
        if (typeof nxDiag().logLayoutDiagnostic === 'function') {
          nxDiag().logLayoutDiagnostic({ source: 'preview-state', state });
        }
      }

      function setActiveTab(name) {
        activeTab = name;
        localStorage.setItem('activeTab', name);
        document.querySelectorAll('.tab-btn').forEach(btn => {
          const on = btn.dataset.tab === name;
          btn.classList.toggle('active', on);
          if (on) btn.classList.remove('has-update');
        });
        const map = {
          logs: ['tabLogs', 'tabActLogs'],
          preview: ['tabPreview', 'tabActPreview'],
          files: ['tabFiles', 'tabActFiles'],
          reasoning: ['tabReasoning', 'tabActReasoning'],
          terminal: ['tabTerminal', 'tabActTerminal'],
          agents: ['tabAgents', 'tabActAgents'],
          timeline: ['tabTimeline', 'tabActTimeline'],
        };
        Object.entries(map).forEach(([t, [content, actions]]) => {
          const c = $(content); const a = $(actions);
          if (c) c.classList.toggle('hidden', t !== name);
          if (a) a.style.display = (t === name) ? 'flex' : 'none';
        });
        if (name === 'preview') refreshPreview(false);
        if (name === 'files') loadFilesTree();
        if (name === 'reasoning') clearReasoningBadge();
        if (name === 'terminal') {
          setTimeout(() => { const i = $('terminalInput'); if (i) i.focus(); }, 50);
          // Phase 24 — refresh the mode badge whenever the user opens the
          // terminal so it always reflects the persisted server-side state
          // (another tab/agent may have flipped it).
          if (typeof window.terminalLoadMode === 'function') window.terminalLoadMode();
        }
        // The Phase 22 tab handlers (loadAgentsState, loadTimeline) live in
        // an IIFE that runs LATER in the file, so they may not yet be on
        // `window` the first time setActiveTab fires during page init or
        // when the user clicks a tab before that script has parsed. Guard
        // with `typeof` so an early click no-ops instead of throwing
        // ReferenceError ("loadAgentsState is not defined").
        if (name === 'agents' && typeof window.loadAgentsState === 'function') window.loadAgentsState();
        if (name === 'timeline' && typeof window.loadTimeline === 'function') window.loadTimeline();
      }

      // ── Preview iframe ──────────────────────────────────────────────────────────
      // `force` bumps a cache-buster query param so the iframe actually re-fetches
      // even if the URL is otherwise identical.
      function refreshPreview(force) {
        const ifr = $('previewIframe');
        if (!currentSession) {
          setPreviewState('empty', 'No preview yet', 'Run a task or open a session with previewable files.');
          if (ifr) ifr.src = 'about:blank';
          return;
        }
        const base = `/preview/${currentSession}/`;
        const url = force ? `${base}?t=${Date.now()}` : base;
        setPreviewState('loading', 'Loading preview', 'Preparing the latest workspace output…');
        if (force || ifr.src.split('?')[0] !== window.location.origin + base) {
          ifr.src = url;
        }
      }
      function reloadPreview() { refreshPreview(true); }
      function openPreviewWindow() {
        if (!currentSession) return;
        window.open(`/preview/${currentSession}/`, '_blank', 'noopener');
      }

      // Notify the user that something was updated in another tab.
      function flagTabUpdate(name) {
        if (activeTab === name) return;
        const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
        if (btn) btn.classList.add('has-update');
      }

      // Called from refreshSession() with the latest /api/preview payload (which
      // just exposes the workspace file list now). Triggers a preview reload + a
      // Files-tab refresh whenever the file set changes.
      function onFilesUpdate(data) {
        const files = (data && data.files) || [];
        // Prefer the server-side digest (covers content edits even when the
        // path set is unchanged). Fall back to a path-list hash if an older
        // server build doesn't expose `version`.
        const sig = (data && data.version)
          ? `v:${data.version}`
          : `n:${files.length}|${files.slice(0, 50).join('|')}`;
        if (sig === lastFilesSig) return;
        const wasFirst = !lastFilesSig;
        lastFilesSig = sig;
        // First load just primes the iframe; later changes also flag the tab.
        setPreviewState(files.length ? 'loading' : 'empty',
          files.length ? 'Refreshing preview' : 'No previewable files yet',
          files.length ? 'Applying the latest workspace changes…' : 'Run a task or create an entry file to preview.');
        refreshPreview(true);
        if (!wasFirst) {
          flagTabUpdate('preview');
          flagTabUpdate('files');
        }
        if (activeTab === 'files') loadFilesTree();
        const hint = $('previewHint');
        if (hint) hint.textContent = files.length
          ? `${files.length} file${files.length === 1 ? '' : 's'} in workspace`
          : 'No files yet.';
      }

      window.NX_LOAD_TASKS.push(function previewLifecycleBoot() {
        const ifr = $('previewIframe');
        if (!ifr) return;
        ifr.addEventListener('load', () => {
          const src = ifr.getAttribute('src') || '';
          if (src === 'about:blank') {
            setPreviewState('empty', 'No preview yet', 'Run a task or open a session with previewable files.');
            return;
          }
          setPreviewState('ready');
        });
        ifr.addEventListener('error', () => {
          setPreviewState('error', 'Preview unavailable', 'The preview failed to load. Reload or check the generated app entry files.');
        });
      });

      // ── Files tab — tree + viewer ───────────────────────────────────────────────
      async function loadFilesTree() {
        if (!currentSession) {
          $('fileTree').innerHTML =
            '<div class="ft-warn-banner">' +
            '<b>Select a session to manage files.</b><br>' +
            'Run a task on the left, or pick an existing session from the ' +
            '"Sessions" panel. File create / rename / delete are disabled ' +
            'until a session is active.' +
            '</div>';
          $('filesCount').textContent = '0 files';
          _lastFsFiles = [];
          _lastFsDirs = [];
          _updateFsToolbarState();
          return;
        }
        const r = await api('GET', `/api/files/${currentSession}`);
        if (!r.ok) {
          $('fileTree').innerHTML = '<div class="ft-empty">Failed to load files.</div>';
          _updateFsToolbarState();
          return;
        }
        const files = r.data.files || [];
        const dirs = r.data.dirs || [];
        // Phase 21.1 polish (architect R3) — `files` is an array of objects
        // ({path,size,mtime}); flatten to plain path strings so the duplicate-
        // name check in `_validateFsName` can use Array.prototype.includes.
        _lastFsFiles = files.map(f => (f && f.path) ? f.path : String(f || ''))
          .filter(Boolean);
        _lastFsDirs = (dirs || []).filter(Boolean);
        $('filesCount').textContent = files.length === 1 ? '1 file'
          : `${files.length} files`;
        // Phase 21.1 R2 fix — only short-circuit to "empty" when BOTH files and
        // dirs are empty. Earlier code returned on `!files.length`, which hid
        // empty folders the user had just created via the toolbar.
        if (!files.length && !dirs.length) {
          $('fileTree').innerHTML =
            '<div class="ft-empty">No files yet.<br>Run a task to generate code.</div>';
          return;
        }
        // Phase 21.1 — pass `dirs` (added in R1 fix #2) so empty folders also
        // appear in the tree. Files-only payloads from older clients still work.
        $('fileTree').innerHTML = renderFileTreeHtml(buildTree(files, dirs));
        _initFileTreeDelegation();
        // Phase 21.1 R1 fix #3 — single source of truth for the active row.
        // Prefer the toolbar selection (set by recent UI action); fall back to
        // highlighting just the open file when nothing is explicitly selected.
        // This avoids the prior bug where both selection AND open file could
        // be marked active simultaneously after a tree refresh.
        if (selectedNodePath) {
          const sel = (selectedNodeKind === 'dir')
            ? document.querySelector(`.ft-dir-row[data-dir-row="${cssEscape(selectedNodePath)}"]`)
            : document.querySelector(`.ft-file[data-path="${cssEscape(selectedNodePath)}"]`);
          if (sel) sel.classList.add('active');
          else { selectedNodePath = null; selectedNodeKind = null; }
        } else if (openFilePath) {
          const row = document.querySelector(
            `.ft-file[data-path="${cssEscape(openFilePath)}"]`);
          if (row) row.classList.add('active');
        }
        _updateFsToolbarState();
      }

      function buildTree(files, dirs) {
        const root = { dirs: {}, files: [] };
        // Phase 21.1 R1 fix #2 — materialize empty directories first so they
        // survive in the rendered tree (file-only payloads only create dirs
        // implicitly via parent paths). Safe to call with no `dirs` arg.
        for (const d of (dirs || [])) {
          if (!d) continue;
          let node = root;
          for (const p of d.split('/')) {
            if (!p) continue;
            node.dirs[p] = node.dirs[p] || { dirs: {}, files: [] };
            node = node.dirs[p];
          }
        }
        for (const f of files) {
          const parts = f.path.split('/');
          const name = parts.pop();
          let node = root;
          for (const p of parts) {
            node.dirs[p] = node.dirs[p] || { dirs: {}, files: [] };
            node = node.dirs[p];
          }
          node.files.push({ name, ...f });
        }
        return root;
      }

      function renderFileTreeHtml(node, prefix) {
        prefix = prefix || '';
        let html = '';
        const dirNames = Object.keys(node.dirs).sort();
        for (const dn of dirNames) {
          const path = prefix ? prefix + '/' + dn : dn;
          // Phase 21.1 R1 fix — paths are user-controlled. NEVER interpolate
          // them into inline onclick="..." JS strings: HTML-entity decoding
          // happens BEFORE the JS string is parsed, so a filename containing
          // a single quote breaks out of the JS literal and executes script.
          // We emit data-* attributes (HTML-attribute escaped) and route the
          // click through the delegated handler installed by _initFileTreeDelegation.
          const pAttr = escapeHtml(path);
          html += `
        <div class="ft-dir" data-dir="${pAttr}">
            <div class="ft-row ft-dir-row" data-dir-row="${pAttr}">▾ ${escapeHtml(dn)}</div>
            <div class="ft-children">${renderFileTreeHtml(node.dirs[dn], path)}</div>
        </div>`;
        }
        for (const f of node.files) {
          const path = prefix ? prefix + '/' + f.name : f.name;
          const sizeStr = (typeof f.size === 'number') ? fmtFileSize(f.size) : '';
          html += `<div class="ft-row ft-file" data-path="${escapeHtml(path)}">
            <span>📄 ${escapeHtml(f.name)}</span>
            <span class="ft-size">${sizeStr}</span>
        </div>`;
        }
        return html;
      }

      function ftDirClick(rowEl, path) {
        selectFtNode(path, 'dir');
        toggleFtDir(rowEl);
      }

      // Phase 21.1 R1 fix — single delegated click handler on #fileTree.
      // Reads paths from data-* attributes; nothing user-controlled is ever
      // passed through `eval`, `setTimeout(string)`, or inline onclick strings.
      function _initFileTreeDelegation() {
        const tree = $('fileTree');
        if (!tree || tree._ftDelegated) return;
        tree._ftDelegated = true;
        tree.addEventListener('click', (ev) => {
          const fileRow = ev.target.closest('.ft-file');
          if (fileRow && tree.contains(fileRow)) {
            const p = fileRow.getAttribute('data-path');
            if (p != null) openFileFromTree(p);
            return;
          }
          const dirRow = ev.target.closest('.ft-dir-row');
          if (dirRow && tree.contains(dirRow)) {
            const p = dirRow.getAttribute('data-dir-row');
            if (p != null) ftDirClick(dirRow, p);
          }
        });
      }

      function toggleFtDir(rowEl) {
        const kids = rowEl.parentElement.querySelector('.ft-children');
        if (!kids) return;
        const collapsed = kids.classList.toggle('collapsed');
        rowEl.firstChild.nodeValue = (collapsed ? '▸ ' : '▾ ');
      }

      // ── Phase 21.1 — file-system management UI state + helpers ──────────────────
      let selectedNodePath = null;     // currently selected file or dir
      let selectedNodeKind = null;     // 'file' | 'dir' | null
      // Phase 21.1 polish — last-fetched tree contents for client-side validation.
      let _lastFsFiles = [];
      let _lastFsDirs = [];

      function selectFtNode(path, kind) {
        selectedNodePath = path;
        selectedNodeKind = kind;
        document.querySelectorAll('.ft-row.active').forEach(r => r.classList.remove('active'));
        let sel = null;
        if (kind === 'dir') {
          sel = document.querySelector(`.ft-dir-row[data-dir-row="${cssEscape(path)}"]`);
        } else if (kind === 'file') {
          sel = document.querySelector(`.ft-file[data-path="${cssEscape(path)}"]`);
        }
        if (sel) sel.classList.add('active');
        _updateFsToolbarState();
      }

      // ── Phase 21.1 polish — toolbar enable/disable + filename validation. ──────
      function _updateFsToolbarState() {
        const hasSession = !!currentSession;
        const hasNode = hasSession && !!selectedNodePath;
        const create = $('fsBtnNewFile');
        const folder = $('fsBtnNewFolder');
        const ren = $('fsBtnRename');
        const del = $('fsBtnDelete');
        if (create) create.disabled = !hasSession;
        if (folder) folder.disabled = !hasSession;
        if (ren) ren.disabled = !hasNode;
        if (del) del.disabled = !hasNode;
        // Tooltip nudge so users understand WHY a button is greyed out.
        const sessReason = 'Pick or start a session first';
        const nodeReason = 'Select a file or folder in the tree first';
        if (create) create.title = hasSession ? 'Create new file in selected folder (or root)' : sessReason;
        if (folder) folder.title = hasSession ? 'Create new folder in selected folder (or root)' : sessReason;
        if (ren) ren.title = hasNode ? 'Rename selected file or folder'
          : (hasSession ? nodeReason : sessReason);
        if (del) del.title = hasNode ? 'Delete selected file or folder'
          : (hasSession ? nodeReason : sessReason);
      }

      // Forbidden filename chars — kept in lock-step with backend
      // `_validate_fs_relpath` so the UI never lets a name through that
      // the server would just reject with `bad_path`.  Covers:
      //   - slashes (path separator)
      //   - control chars 0x00-0x1f and 0x7f
      //   - Windows-reserved chars <>:"\\|?* (so the workspace stays
      //     portable when zipped + downloaded).
      const _FS_FORBIDDEN_RE = /[\\/\x00-\x1f\x7f<>:"|?*]/;
      const _FS_NAME_MAX = 80;

      function _validateFsName(name, kind, parent) {
        // Returns {ok:true} on success, or {ok:false, error:'…'} on failure.
        if (name === null || name === undefined) return { ok: false, error: 'Cancelled' };
        const trimmed = String(name).trim();
        if (!trimmed) return { ok: false, error: 'Name cannot be empty' };
        if (trimmed.length > _FS_NAME_MAX) return { ok: false, error: `Name too long (max ${_FS_NAME_MAX})` };
        if (_FS_FORBIDDEN_RE.test(trimmed)) return { ok: false, error: 'Name cannot contain / \\ or control chars' };
        if (trimmed === '.' || trimmed === '..') return { ok: false, error: 'Reserved name' };
        if (trimmed.startsWith('.')) return { ok: false, error: 'Hidden names (starting with ".") are not allowed' };
        if (trimmed.endsWith('.')) return { ok: false, error: 'Name cannot end with "."' };
        // Duplicate check against the last-fetched tree.
        const fullRel = parent ? parent + '/' + trimmed : trimmed;
        const collidesFile = _lastFsFiles.includes(fullRel);
        const collidesDir = _lastFsDirs.includes(fullRel);
        if (collidesFile || collidesDir) {
          return { ok: false, error: `"${fullRel}" already exists` };
        }
        return { ok: true, name: trimmed };
      }

      // ── Browser host allowlist (Settings → Advanced) ───────────────────────────
      async function loadBrowserAllowlist() {
        const box = $('allowlistList');
        if (!box) return;
        const r = await api('GET', '/api/browser-allowlist');
        if (!r.ok) {
          box.innerHTML = '<div class="empty" style="padding:8px 0">Failed to load.</div>';
          return;
        }
        const hosts = r.data.hosts || [];
        const defaults = new Set(r.data.default || []);
        if (!hosts.length) {
          box.innerHTML = '<div class="empty" style="padding:8px 0">No hosts allowed.</div>';
          return;
        }
        box.innerHTML = hosts.map(h => {
          const isDefault = defaults.has(h);
          const safe = escapeHtml(h);
          return `<div class="allowlist-row">
                  <span class="host">${safe}</span>
                  ${isDefault ? '<span class="src">default</span>' : ''}
                  <button class="btn tiny danger"
                          onclick="removeBrowserAllowlistEntry('${safe.replace(/'/g, "\\'")}')">
                    Remove
                  </button>
                </div>`;
        }).join('');
      }

      async function addBrowserAllowlistEntry() {
        const inp = $('allowlistInput');
        if (!inp) return;
        const host = (inp.value || '').trim();
        if (!host) { toast('Enter a host first', 'err'); return; }
        const r = await api('POST', '/api/browser-allowlist', { host });
        if (!r.ok) {
          toast('Add failed: ' + ((r.data && r.data.error) || 'error'), 'err');
          return;
        }
        inp.value = '';
        toast('Added ' + r.data.added, 'ok');
        loadBrowserAllowlist();
      }

      async function removeBrowserAllowlistEntry(host) {
        if (!host) return;
        if (!confirm(`Remove "${host}" from the browser allowlist?`)) return;
        const r = await api('DELETE', '/api/browser-allowlist', { host });
        if (!r.ok) {
          toast('Remove failed: ' + ((r.data && r.data.error) || 'error'), 'err');
          return;
        }
        toast('Removed ' + host, 'ok');
        loadBrowserAllowlist();
      }

      // ── Review policy (Settings → Advanced) ───────────────────────────────────
      // Cached so runAIAction can decide on a stale-but-recent policy without an
      // extra round-trip; the server still re-loads the live policy on every
      // /api/code-action call, so the cache is purely a UX hint.
      let _reviewPolicyCache = null;

      async function loadReviewPolicy() {
        const box = $('reviewPolicyBox');
        if (!box) return;
        const r = await api('GET', '/api/review-policy');
        if (!r.ok) {
          box.innerHTML = '<div class="empty" style="padding:8px 0">Failed to load.</div>';
          return;
        }
        const d = r.data || {};
        _reviewPolicyCache = d.policy || null;
        const pol = d.policy || {};
        const defs = d.defaults || {};
        const modes = d.modes || ['REQUEST_REVIEW', 'ALWAYS_PROCEED', 'AGENT_DECIDES'];
        const valid = d.valid_actions || ['fix', 'optimize', 'refactor', 'explain'];
        const caps = d.caps || { max_lines: 200, max_hunks: 20 };
        const labels = {
          REQUEST_REVIEW: 'Request review (always show diff)',
          ALWAYS_PROCEED: 'Always proceed (auto-apply everything)',
          AGENT_DECIDES: 'Agent decides (hybrid — auto-apply small changes)',
        };
        const radioRows = modes.map(m => {
          const checked = pol.mode === m ? 'checked' : '';
          const safe = escapeHtml(m);
          return `<label class="rp-radio" style="display:flex;gap:8px;align-items:center;margin:4px 0">
                  <input type="radio" name="rpMode" value="${safe}" ${checked}>
                  <span>${escapeHtml(labels[m] || m)}</span>
                </label>`;
        }).join('');
        const actionRows = valid.map(a => {
          const checked = (pol.actions || []).indexOf(a) >= 0 ? 'checked' : '';
          const safe = escapeHtml(a);
          return `<label class="rp-action" style="display:inline-flex;gap:6px;align-items:center;margin-right:12px">
                  <input type="checkbox" name="rpAction" value="${safe}" ${checked}>
                  <span>${safe}</span>
                </label>`;
        }).join('');
        box.innerHTML = `
        <div class="rp-modes">${radioRows}</div>
        <div class="rp-thresholds" style="display:flex;gap:14px;margin-top:8px;flex-wrap:wrap">
            <label style="display:flex;flex-direction:column;font-size:0.78rem">
                <span>Max lines changed (≤ ${caps.max_lines})</span>
                <input type="number" id="rpMaxLines" min="1" max="${caps.max_lines}"
                       value="${Number(pol.max_lines) || defs.max_lines || 10}"
                       style="width:90px">
            </label>
            <label style="display:flex;flex-direction:column;font-size:0.78rem">
                <span>Max hunks (≤ ${caps.max_hunks})</span>
                <input type="number" id="rpMaxHunks" min="1" max="${caps.max_hunks}"
                       value="${Number(pol.max_hunks) || defs.max_hunks || 2}"
                       style="width:90px">
            </label>
        </div>
        <div class="rp-actions-block" style="margin-top:8px;font-size:0.78rem">
            <div style="margin-bottom:4px;color:var(--muted)">
                Auto-apply only these actions in “Agent decides” mode:
            </div>
            ${actionRows}
        </div>
        <div style="display:flex;gap:8px;margin-top:10px">
            <button class="btn" onclick="saveReviewPolicy()">Save policy</button>
            <button class="btn tiny" onclick="loadReviewPolicy()">Refresh</button>
            <span class="meta" style="margin-left:auto;align-self:center">
                Defaults: ${escapeHtml(defs.mode || '?')} ·
                ${defs.max_lines ?? '?'} lines · ${defs.max_hunks ?? '?'} hunks
            </span>
        </div>`;
      }

      async function saveReviewPolicy() {
        const modeEl = document.querySelector('input[name="rpMode"]:checked');
        if (!modeEl) { toast('Pick a mode', 'err'); return; }
        const actions = Array.from(
          document.querySelectorAll('input[name="rpAction"]:checked')
        ).map(el => el.value);
        const max_lines = parseInt(($('rpMaxLines') || {}).value || '10', 10);
        const max_hunks = parseInt(($('rpMaxHunks') || {}).value || '2', 10);
        const payload = {
          mode: modeEl.value,
          max_lines: isFinite(max_lines) ? max_lines : 10,
          max_hunks: isFinite(max_hunks) ? max_hunks : 2,
          actions,
        };
        const r = await api('POST', '/api/review-policy', payload);
        if (!r.ok || (r.data && r.data.ok === false)) {
          toast('Save failed: ' + ((r.data && r.data.error) || 'error'), 'err');
          return;
        }
        _reviewPolicyCache = (r.data && r.data.policy) || payload;
        toast('Review policy saved', 'ok');
        loadReviewPolicy();
      }

      function _basePathForCreate() {
        // Where new files/folders should land when a node is selected.
        if (selectedNodeKind === 'dir') return selectedNodePath || '';
        if (selectedNodeKind === 'file') {
          const p = selectedNodePath || '';
          const i = p.lastIndexOf('/');
          return i >= 0 ? p.slice(0, i) : '';
        }
        return '';
      }

      function _confirmDiscardIfDirty(reason) {
        if (window.CodeEditor && typeof window.CodeEditor.isDirty === 'function'
          && window.CodeEditor.isDirty()) {
          const cur = (window.CodeEditor.getCurrentPath
            && window.CodeEditor.getCurrentPath()) || openFilePath || 'current file';
          return confirm('Discard unsaved changes to ' + cur + '?\n(' + reason + ')');
        }
        return true;
      }

      async function fsCreateFile() {
        if (!currentSession) { toast('No session selected', 'err'); return; }
        const base = _basePathForCreate();
        const raw = prompt('New file name (in ' + (base || 'workspace root') + '):', 'untitled.txt');
        if (raw === null) return;  // user cancelled
        const v = _validateFsName(raw, 'file', base);
        if (!v.ok) { toast(v.error, 'err'); return; }
        const rel = base ? base + '/' + v.name : v.name;
        const r = await api('POST', `/api/create-file/${currentSession}`,
          { path: rel, content: '' });
        if (!r.ok) { toast('Create failed: ' + ((r.data && r.data.error) || 'error'), 'err'); return; }
        toast('Created ' + rel, 'ok');
        await loadFilesTree();
        openFileFromTree(rel);
      }

      async function fsCreateFolder() {
        if (!currentSession) { toast('No session selected', 'err'); return; }
        const base = _basePathForCreate();
        const raw = prompt('New folder name (in ' + (base || 'workspace root') + '):', 'new-folder');
        if (raw === null) return;
        const v = _validateFsName(raw, 'dir', base);
        if (!v.ok) { toast(v.error, 'err'); return; }
        const rel = base ? base + '/' + v.name : v.name;
        const r = await api('POST', `/api/create-folder/${currentSession}`, { path: rel });
        if (!r.ok) { toast('Create folder failed: ' + ((r.data && r.data.error) || 'error'), 'err'); return; }
        toast('Created folder ' + rel, 'ok');
        await loadFilesTree();
        selectFtNode(rel, 'dir');
      }

      async function fsRename() {
        if (!currentSession) { toast('No session selected', 'err'); return; }
        if (!selectedNodePath) { toast('Select a file or folder first', 'err'); return; }
        const oldRel = selectedNodePath;
        const oldName = oldRel.split('/').pop();
        const parent = oldRel.includes('/') ? oldRel.slice(0, oldRel.lastIndexOf('/')) : '';
        const raw = prompt('Rename "' + oldRel + '" to:', oldName);
        if (raw === null) return;                     // user cancelled
        const trimmed = String(raw).trim();
        if (trimmed === oldName) return;              // no-op
        // Allow renaming to a name that already equals the *current* one only
        // when the duplicate-check would catch the new path; bypass collision
        // self-match by stashing+restoring the old entry.
        const wasFile = _lastFsFiles.includes(oldRel);
        const wasDir = _lastFsDirs.includes(oldRel);
        if (wasFile) _lastFsFiles = _lastFsFiles.filter(p => p !== oldRel);
        if (wasDir) _lastFsDirs = _lastFsDirs.filter(p => p !== oldRel);
        const v = _validateFsName(trimmed, selectedNodeKind, parent);
        if (wasFile) _lastFsFiles.push(oldRel);
        if (wasDir) _lastFsDirs.push(oldRel);
        if (!v.ok) { toast(v.error, 'err'); return; }
        const newRel = parent ? parent + '/' + v.name : v.name;

        // If the open file is being renamed (or moved away under a renamed dir),
        // confirm before discarding any unsaved edits.
        const openIsAffected =
          openFilePath === oldRel ||
          (selectedNodeKind === 'dir' && openFilePath
            && openFilePath.startsWith(oldRel + '/'));
        if (openIsAffected && !_confirmDiscardIfDirty('rename will reload the editor')) return;

        const r = await api('POST', `/api/rename-file/${currentSession}`,
          { old_path: oldRel, new_path: newRel });
        if (!r.ok) { toast('Rename failed: ' + ((r.data && r.data.error) || 'error'), 'err'); return; }
        toast('Renamed → ' + newRel, 'ok');

        if (openIsAffected) {
          const wasOpen = openFilePath;
          openFilePath = null;
          if (window.CodeEditor) window.CodeEditor.reset();
          // For a file rename, reopen at the new path. For a dir rename, the
          // open file's path now lives under newRel/* — reopen at the rewritten path.
          if (selectedNodeKind === 'file') {
            await loadFilesTree();
            openFileFromTree(newRel);
            return;
          } else if (wasOpen) {
            const reopened = newRel + wasOpen.slice(oldRel.length);
            await loadFilesTree();
            openFileFromTree(reopened);
            return;
          }
        }
        await loadFilesTree();
        selectFtNode(newRel, selectedNodeKind);
      }

      async function fsDelete() {
        if (!currentSession) { toast('No session selected', 'err'); return; }
        if (!selectedNodePath) { toast('Select a file or folder first', 'err'); return; }
        const target = selectedNodePath;
        const isDir = selectedNodeKind === 'dir';
        const msg = isDir
          ? `Delete folder "${target}" and ALL its contents?\nThis cannot be undone.`
          : `Delete file "${target}"?\nThis cannot be undone.`;
        if (!confirm(msg)) return;

        const openIsAffected =
          openFilePath === target ||
          (isDir && openFilePath && openFilePath.startsWith(target + '/'));
        if (openIsAffected && !_confirmDiscardIfDirty('the open file will be deleted')) return;

        const r = await api('POST', `/api/delete-file/${currentSession}`, { path: target });
        if (!r.ok) { toast('Delete failed: ' + ((r.data && r.data.error) || 'error'), 'err'); return; }
        toast('Deleted ' + target, 'ok');

        if (openIsAffected) {
          openFilePath = null;
          if (window.CodeEditor) window.CodeEditor.reset();
          $('fileViewerPath').textContent = 'Pick a file from the tree';
          $('fileViewerMeta').textContent = '';
          $('fileViewerBody').innerHTML =
            '<div class="empty">Pick a file from the tree to view it here.</div>';
        }
        selectedNodePath = null;
        selectedNodeKind = null;
        await loadFilesTree();
      }

      function fmtFileSize(b) {
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / 1024 / 1024).toFixed(1) + ' MB';
      }

      function cssEscape(s) {
        // Minimal CSS attribute-value escape for the selectors we build.
        return String(s).replace(/(["\\])/g, '\\$1');
      }

      const _IMG_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico']);

      async function openFileFromTree(path) {
        if (!currentSession) return;
        // Phase 21.1 — unsaved-changes guard: don't silently drop edits when
        // the user switches files. Only prompt when the target is different.
        if (path !== openFilePath
          && !_confirmDiscardIfDirty('switching to a different file')) {
          return;
        }
        openFilePath = path;
        // Phase 21.1 — track selection so toolbar Rename/Delete know the target.
        selectedNodePath = path;
        selectedNodeKind = 'file';
        // Phase 21.1 polish (architect R3) — the delegated tree-row click
        // handler reaches openFileFromTree directly (not via selectFtNode),
        // so refresh the toolbar here too or Rename/Delete stay disabled
        // even after the user clicks a file.
        _updateFsToolbarState();
        // Phase 20.6 — capture session+path tokens so a slow response from
        // an earlier file open can't overwrite a later one (architect R2).
        const _openSid = currentSession;
        const _openPath = path;
        document.querySelectorAll('.ft-row.active').forEach(r => r.classList.remove('active'));
        const row = document.querySelector(`.ft-file[data-path="${cssEscape(path)}"]`);
        if (row) row.classList.add('active');

        $('fileViewerPath').textContent = path;
        $('fileViewerMeta').textContent = 'Loading…';
        $('fileViewerBody').innerHTML = '<div class="empty">Loading…</div>';

        const r = await api('GET',
          `/api/file/${currentSession}?path=${encodeURIComponent(path)}`);
        // Drop stale responses (user clicked another file or switched session).
        if (_openSid !== currentSession || _openPath !== openFilePath) return;
        if (!r.ok) {
          // Phase 20.6 — clear any prior editor state so the toolbar
          // can't fire actions against a stale text buffer.
          if (window.CodeEditor) window.CodeEditor.reset();
          const code = (r.data && r.data.error) || 'error';
          if (code === 'too_large') {
            $('fileViewerMeta').textContent =
              `${fmtFileSize(r.data.size || 0)} — too large to preview`;
            $('fileViewerBody').innerHTML =
              `<div class="binary-msg">File is too large to preview inline.<br>
                 <a href="/preview/${currentSession}/${encodeURIComponent(path)}"
                    target="_blank" rel="noopener">Open raw</a></div>`;
          } else {
            $('fileViewerMeta').textContent = '';
            $('fileViewerBody').innerHTML =
              `<div class="binary-msg">Could not open file (${escapeHtml(code)}).</div>`;
          }
          return;
        }
        const d = r.data;
        $('fileViewerMeta').textContent = `${fmtFileSize(d.size)} · ${d.encoding}`;
        if (d.encoding === 'text') {
          // Phase 20.6 — Hand off to the Monaco-backed editor instead of
          // rendering a static <pre>.  Editor falls back to <pre> on its
          // own if Monaco fails to load.
          if (window.CodeEditor) {
            window.CodeEditor.loadFile(path, d.content, d.ext || '');
          } else {
            $('fileViewerBody').innerHTML =
              `<pre>${escapeHtml(d.content)}</pre>`;
          }
        } else if (_IMG_EXTS.has(d.ext)) {
          // Phase 20.6 — non-text branch: reset the editor so the toolbar
          // can't act on a stale buffer from a previously opened file.
          if (window.CodeEditor) window.CodeEditor.reset();
          const mime = d.ext === '.svg' ? 'image/svg+xml'
            : d.ext === '.jpg' || d.ext === '.jpeg' ? 'image/jpeg'
              : `image/${d.ext.slice(1)}`;
          $('fileViewerBody').innerHTML =
            `<img alt="${escapeHtml(path)}" src="data:${mime};base64,${d.content}">`;
        } else {
          // Phase 20.6 — non-text branch: reset the editor here too.
          if (window.CodeEditor) window.CodeEditor.reset();
          $('fileViewerBody').innerHTML =
            `<div class="binary-msg">Binary file (${escapeHtml(d.ext || 'unknown')}).<br>
             <a href="/preview/${currentSession}/${encodeURIComponent(path)}"
                target="_blank" rel="noopener" download>Download</a></div>`;
        }
      }

      // ── Download project zip ────────────────────────────────────────────────────
      async function downloadProject() {
        if (!currentSession) { toast('No session selected', 'err'); return; }
        // HEAD-style probe via /api/files: if empty, give a helpful toast instead
        // of letting the browser download a JSON-error blob.
        const r = await api('GET', `/api/files/${currentSession}`);
        if (!r.ok || !((r.data.files || []).length)) {
          toast('No files to download yet — run a task first.', 'err');
          return;
        }
        window.location.href = `/api/download/${currentSession}`;
      }

      function clearLogView() {
        logBuffer = [];
        $('logArea').innerHTML = '<div class="empty" id="logsEmpty">View cleared. New logs will appear here.</div>';
      }

      async function stopSession() {
        if (!currentSession) return;
        const r = await api('POST', `/api/session/${currentSession}/stop`);
        if (!r.ok) toast(r.data.message || 'Failed to stop', 'err');
        refreshSession(); loadSessions(); loadQueue();
      }
      async function restartSession() {
        if (!currentSession) return;
        const r = await api('POST', `/api/session/${currentSession}/restart`);
        if (!r.ok) { toast(r.data.error || 'Failed to restart', 'err'); return; }
        selectSession(r.data.session_id);
        loadSessions(); loadQueue();
      }
      async function deleteSession() {
        if (!currentSession) return;
        if (!confirm('Delete this session and its logs?')) return;
        const r = await api('DELETE', `/api/session/${currentSession}`);
        if (!r.ok) { toast(r.data.error || 'Failed to delete', 'err'); return; }
        currentSession = null;
        setVal('stSession', null);
        // Phase 21.1 polish (architect R3) — also clear tree selection +
        // hide the project pill + refresh the toolbar so create / rename /
        // delete buttons grey out immediately (instead of staying enabled
        // against a session that no longer exists).
        selectedNodePath = null;
        selectedNodeKind = null;
        _lastFsFiles = [];
        _lastFsDirs = [];
        const projPill = $('projectPill');
        if (projPill) projPill.style.display = 'none';
        _updateFsToolbarState();
        // Phase 21.2 (architect R2) — also tear down the editor so the
        // pendingSuggestions queue / badge / dirty buffer don't survive
        // the session deletion.  reset() clears all of that in one shot.
        if (window.CodeEditor) {
          try { window.CodeEditor.reset(); } catch (_) { }
        }
        // Phase 21.1 polish (architect R3 follow-up) — also redraw the Files
        // tab so it immediately shows the "Select a session" banner instead
        // of leaving the deleted session's tree on screen until the user
        // switches tabs.
        loadFilesTree();
        loadSessions();
      }

      async function loadMemory() {
        const r = await api('GET', '/api/memory');
        if (!r.ok) return;
        const m = r.data;
        const fmt = (cards) => cards.length
          ? cards.map(c => `<div class="mem-card"><span class="cat">${escapeHtml(c.cat)}</span><div class="body">${escapeHtml(c.body)}</div></div>`).join('')
          : '<div class="empty" style="padding:14px 0">None yet.</div>';
        $('memLearnings').innerHTML = fmt((m.learnings || []).map(x => ({
          cat: x.category || 'note',
          body: (x.insight || '').slice(0, 400)
        })));
        $('memTasks').innerHTML = fmt((m.tasks || []).map(x => ({
          cat: x.status || 'task',
          body: `${x.task}\n— ${x.api_used || 'unknown'}`
        })));
        $('memSnippets').innerHTML = fmt((m.snippets || []).map(x => ({
          cat: x.lang || 'code',
          body: `${x.name}  (used ${x.used_count}×)`
        })));
      }

      async function clearAgentMemory() {
        if (!confirm('Wipe agent memory (tasks, snippets, learnings)?')) return;
        const r = await api('POST', '/api/clear-memory');
        toast(r.data.message || 'Done', r.ok ? 'ok' : 'err');
        loadMemory();
      }

      // ─── Polling loops ─────────────────────────────────────────────────────
      // Restore the last-active center tab before any data lands so the user
      // doesn't get a brief flash of the default Logs tab.
      setActiveTab(activeTab);
      loadConfig(true);
      loadSessions();
      loadQueue();
      // FIX: Adaptive poll: fast when running (3s), slow when idle (8s)
      // We restore this but GUARD refreshSession so we don't spam 404s/empty fetches when no session is active.
      // ─── Phase 7.8 Adaptive Polling ───────────────────────────────────────
      // This file is included once via a deferred script tag, so we avoid a
      // top-level `return` here because browsers treat it as a syntax error.
      // The explicit flag still documents intent for future refactors.
      runtimeInitialized = true;

      (function nxAdaptivePoll() {
        function tick() {
          if (document.hidden) { setTimeout(tick, 5000); return; }
          const running = NX.lastStatus === 'running';
          if (typeof currentSession !== 'undefined' && currentSession) {
            refreshSession();
          }
          loadQueue();
          setTimeout(tick, running ? 3000 : 8000);
        }
        setTimeout(tick, 3000);
      })();

      setInterval(() => {
        if (document.hidden) return;
        loadSessions();
      }, 10000);

      setInterval(() => {
        if (document.hidden || settingsModalOpen || userInteracting) return;
        loadConfig(false);
      }, 15000);

      // ─── Keyboard / event wiring ───────────────────────────────────────────
      $('taskInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          queueTask();
        }
      });
      $('quietMode').addEventListener('change', rerenderLogsFromBuffer);

      // ─── Phase 6.9 — Unified layout resize ───────────────────────────────────
      // Persists every adjustable dimension under a single localStorage key so
      // the user's layout (column widths + row heights) survives reloads.
      //
      //   layoutSizes = {
      //     leftW, rightW,           // main grid column widths
      //     centerPreviewH,          // result-preview row inside center column
      //     'rp-status', 'rp-decisions',   // right-pane heights (Phase 6.7 panes)
      //   }
      //
      // Migrates the older "rightPaneSizes" key on first load so users keep
      // their previous layout.
      (function setupLayoutResize() {
        const STORE = 'layoutSizes';
        const LEGACY = 'rightPaneSizes';

        const MIN_COL_LEFT = 200;
        const MIN_COL_RIGHT = 240;
        const MIN_CENTER = 320;   // never let center collapse below this
        const MIN_PANE_V = 80;    // generic vertical pane min height
        const MIN_PREVIEW = 90;    // bottom preview row min
        const MIN_LOG = 140;   // top log pane min

        function readStore() {
          try { return JSON.parse(localStorage.getItem(STORE) || '{}'); }
          catch { return {}; }
        }
        function writeStore(obj) {
          try { localStorage.setItem(STORE, JSON.stringify(obj)); }
          catch { /* ignore quota errors */ }
        }
        function migrate() {
          const cur = readStore();
          if (cur._migrated) return cur;
          try {
            const old = JSON.parse(localStorage.getItem(LEGACY) || '{}');
            for (const k of Object.keys(old)) cur[k] = old[k];
          } catch { /* ignore */ }
          cur._migrated = true;
          writeStore(cur);
          return cur;
        }
        function update(patch) {
          const cur = readStore();
          Object.assign(cur, patch);
          writeStore(cur);
        }

        // ---- restore persisted sizes ----
        function restore() {
          const cur = migrate();
          const root = document.documentElement;
          const vw = window.innerWidth;
          const vh = window.innerHeight;
          const diagPatch = { source: 'persisted', clamped: false, reset: false };
          // Clamp column widths so a previous wide layout doesn't crush the
          // center column on a narrower viewport.
          const maxLeft = Math.max(MIN_COL_LEFT, vw - MIN_CENTER - MIN_COL_RIGHT - 12);
          const maxRight = Math.max(MIN_COL_RIGHT, vw - MIN_CENTER - MIN_COL_LEFT - 12);
          if (Number.isFinite(cur.leftW)) {
            const v = Math.min(maxLeft, Math.max(MIN_COL_LEFT, cur.leftW));
            root.style.setProperty('--leftW', v + 'px');
            if (v !== cur.leftW) diagPatch.clamped = true;
          }
          if (Number.isFinite(cur.rightW)) {
            const v = Math.min(maxRight, Math.max(MIN_COL_RIGHT, cur.rightW));
            root.style.setProperty('--rightW', v + 'px');
            if (v !== cur.rightW) diagPatch.clamped = true;
          }
          if (Number.isFinite(cur.centerPreviewH)) {
            const v = Math.min(Math.max(MIN_PREVIEW, cur.centerPreviewH), vh - MIN_LOG - 80);
            root.style.setProperty('--centerPreviewH', v + 'px');
            if (v !== cur.centerPreviewH) diagPatch.clamped = true;
          }
          // Right-pane heights — clamp each against the live right-column
          // budget so a stored "tall" value from a previous big window
          // doesn't crush its neighbours after a resize. Persist any
          // clamping back to storage so the bad value doesn't recur.
          const right = document.getElementById('rightCol');
          const ids = ['rp-status', 'rp-decisions'];
          if (right) {
            // Reset first so getBoundingClientRect reflects live flex sizing.
            for (const id of ids) {
              const el = document.getElementById(id);
              if (el) el.style.height = '';
            }
            const rightTotal = right.getBoundingClientRect().height;
            const handlesH = Array.from(right.children)
              .filter(el => !el.classList.contains('right-pane'))
              .reduce((s, el) => s + el.getBoundingClientRect().height, 0);
            // Reserve room for the bottom flex pane + any non-stored sibling.
            let remaining = Math.max(0, rightTotal - handlesH);
            const flexPane = right.querySelector('.right-pane.flex');
            const flexMin = flexPane ? 100 : 0;
            const persisted = {};
            for (let i = 0; i < ids.length; i++) {
              const id = ids[i];
              const el = document.getElementById(id);
              if (!el) continue;
              if (!Number.isFinite(cur[id])) continue;
              // Sum of remaining mins for siblings still to come.
              const siblingsLeftMin = (ids.length - 1 - i) * MIN_PANE_V + flexMin;
              const maxH = Math.max(MIN_PANE_V, remaining - siblingsLeftMin);
              const clamped = Math.min(maxH, Math.max(MIN_PANE_V, cur[id]));
              el.style.height = clamped + 'px';
              remaining -= clamped;
              if (clamped !== cur[id]) persisted[id] = Math.round(clamped);
            }
            if (Object.keys(persisted).length) update(persisted);
          }
          if (typeof nxDiag().logLayoutDiagnostic === 'function') {
            nxDiag().logLayoutDiagnostic(diagPatch);
          }
        }

        // ---- generic drag helper ────────────────────────────────────────────
        // axis: 'col' or 'row'
        // initStart() runs ONCE at mousedown; returns a context object that the
        // onMove callback receives every frame along with the cumulative delta
        // from the original mousedown point. This avoids the compounding bug
        // where re-reading "current size" each frame combined with delta-from-
        // mousedown caused the drag to accelerate / overshoot.
        function bindDrag(handle, axis, initStart, onMove) {
          if (!handle) return;
          handle.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            const start = (axis === 'col') ? e.clientX : e.clientY;
            const ctx = initStart() || {};
            handle.classList.add('dragging');
            document.body.classList.add(axis === 'col' ? 'col-resizing' : 'resizing');
            const move = (ev) => {
              const cur = (axis === 'col') ? ev.clientX : ev.clientY;
              onMove(cur - start, ctx, ev);
            };
            const up = () => {
              document.removeEventListener('mousemove', move);
              document.removeEventListener('mouseup', up);
              handle.classList.remove('dragging');
              document.body.classList.remove('col-resizing');
              document.body.classList.remove('resizing');
            };
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
          });
          // Double-click resets that one dimension to default.
          handle.addEventListener('dblclick', (e) => {
            e.preventDefault();
            const root = document.documentElement;
            if (handle.id === 'splitLC') { root.style.removeProperty('--leftW'); update({ leftW: null }); }
            else if (handle.id === 'splitCR') { root.style.removeProperty('--rightW'); update({ rightW: null }); }
            else if (handle.id === 'splitCenter') { root.style.removeProperty('--centerPreviewH'); update({ centerPreviewH: null }); }
            else if (handle.classList.contains('resize-handle')) {
              const t = document.getElementById(handle.dataset.target);
              if (t) { t.style.height = ''; const k = handle.dataset.target; const cur = readStore(); delete cur[k]; writeStore(cur); }
            }
            if (typeof nxDiag().logLayoutDiagnostic === 'function') {
              nxDiag().logLayoutDiagnostic({ source: 'reset-control', target: handle.id || handle.dataset.target || 'unknown', reset: true });
            }
          });
        }

        // ---- LEFT ↔ CENTER ----
        bindDrag(document.getElementById('splitLC'), 'col',
          () => {
            const root = document.documentElement;
            const vw = window.innerWidth;
            const rightW = document.getElementById('rightCol').getBoundingClientRect().width;
            const startW = document.getElementById('leftCol').getBoundingClientRect().width;
            const maxLeft = Math.max(MIN_COL_LEFT, vw - MIN_CENTER - rightW - 12);
            return { startW, maxLeft, root };
          },
          (dx, ctx) => {
            const next = Math.min(ctx.maxLeft, Math.max(MIN_COL_LEFT, ctx.startW + dx));
            ctx.root.style.setProperty('--leftW', next + 'px');
            update({ leftW: Math.round(next) });
          }
        );

        // ---- CENTER ↔ RIGHT ----
        bindDrag(document.getElementById('splitCR'), 'col',
          () => {
            const root = document.documentElement;
            const vw = window.innerWidth;
            const leftW = document.getElementById('leftCol').getBoundingClientRect().width;
            const startW = document.getElementById('rightCol').getBoundingClientRect().width;
            const maxRight = Math.max(MIN_COL_RIGHT, vw - MIN_CENTER - leftW - 12);
            return { startW, maxRight, root };
          },
          (dx, ctx) => {
            // Drag right (+dx) → right column shrinks.
            const next = Math.min(ctx.maxRight, Math.max(MIN_COL_RIGHT, ctx.startW - dx));
            ctx.root.style.setProperty('--rightW', next + 'px');
            update({ rightW: Math.round(next) });
          }
        );

        // ---- LOGS ↕ RESULT PREVIEW (inside center column) ----
        bindDrag(document.getElementById('splitCenter'), 'row',
          () => {
            const root = document.documentElement;
            const center = document.getElementById('centerCol');
            const totalH = center.getBoundingClientRect().height;
            // Read current preview row height from the rendered preview
            // pane (rather than the CSS var) so the very first drag —
            // when the var hasn't been set — still anchors correctly.
            const previewEl = center.querySelector('.preview-pane');
            const startH = previewEl ? previewEl.getBoundingClientRect().height : 240;
            const maxPrev = Math.max(MIN_PREVIEW, totalH - MIN_LOG - 6);
            return { startH, maxPrev, root };
          },
          (dy, ctx) => {
            // Drag DOWN (+dy) → preview shrinks; UP → preview grows.
            const next = Math.min(ctx.maxPrev, Math.max(MIN_PREVIEW, ctx.startH - dy));
            ctx.root.style.setProperty('--centerPreviewH', next + 'px');
            update({ centerPreviewH: Math.round(next) });
          }
        );

        // ---- RIGHT-PANE vertical splits (Phase 6.7 — kept; now persisted in same store) ----
        document.querySelectorAll('#rightCol .resize-handle').forEach(handle => {
          bindDrag(handle, 'row',
            () => {
              const targetId = handle.dataset.target;
              const target = document.getElementById(targetId);
              if (!target) return null;
              const right = document.getElementById('rightCol');
              // Capture both start size AND clamp bounds ONCE, at
              // mousedown. Recomputing them every frame combined with
              // delta-from-mousedown caused acceleration in the
              // previous version.
              const startH = target.getBoundingClientRect().height;
              const kids = Array.from(right.children);
              const hi = kids.indexOf(handle);
              const followingMin = kids.slice(hi + 1)
                .filter(el => el.classList.contains('right-pane'))
                .reduce((s, el) => s + (el.classList.contains('flex') ? 100 : MIN_PANE_V), 0);
              const fixedAbove = kids.slice(0, hi)
                .filter(el => el.classList.contains('right-pane') && el !== target)
                .reduce((s, el) => s + el.getBoundingClientRect().height, 0);
              const handlesH = kids
                .filter(el => !el.classList.contains('right-pane'))
                .reduce((s, el) => s + el.getBoundingClientRect().height, 0);
              const available = right.getBoundingClientRect().height - fixedAbove - handlesH;
              const maxH = Math.max(MIN_PANE_V, available - followingMin);
              return { target, targetId, startH, maxH };
            },
            (dy, ctx) => {
              if (!ctx) return;
              const next = Math.min(ctx.maxH, Math.max(MIN_PANE_V, ctx.startH + dy));
              ctx.target.style.height = next + 'px';
              update({ [ctx.targetId]: Math.round(next) });
            }
          );
        });

        // ---- viewport changes — re-clamp ----
        window.addEventListener('resize', () => {
          // Re-apply restore() to clamp current values against the new viewport.
          restore();
        });

        restore();
      })();

      /* ══════════════════════════════════════════════════════════════════════════
         Phase 20.6 — Integrated Code Editor (Monaco) + AI interaction layer
         ────────────────────────────────────────────────────────────────────────
         Self-contained module exposed as `window.CodeEditor`.  Reuses existing
         `currentSession`, `openFilePath`, `api()`, `toast()`, `escapeHtml()`,
         `fmtFileSize()`, `$()` from the rest of this file.
         ══════════════════════════════════════════════════════════════════════════ */
      (function () {
        'use strict';

        /* ── Monaco loader (lazy, idempotent) ───────────────────────────────── */
        let monacoReadyP = null;
        function ensureMonaco() {
          if (monacoReadyP) return monacoReadyP;
          monacoReadyP = new Promise((resolve, reject) => {
            if (window.monaco) return resolve(window.monaco);
            
            const initMonaco = () => {
              if (!window.require || !window.require.config) {
                return reject(new Error('monaco loader missing'));
              }
              try {
                window.require.config({
                  paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' },
                });
                window.require(['vs/editor/editor.main'], () => {
                  if (window.monaco) resolve(window.monaco);
                  else reject(new Error('monaco failed to load'));
                });
              } catch (e) { reject(e); }
            };

            if (window.require && window.require.config) {
              initMonaco();
            } else {
              const s = document.createElement('script');
              s.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js';
              s.onload = initMonaco;
              s.onerror = () => reject(new Error('Failed to load monaco script'));
              document.head.appendChild(s);
            }
          }).catch(err => {
            console.warn('[editor] monaco load failed:', err);
            monacoReadyP = null;
            throw err;
          });
          return monacoReadyP;
        }

        /* ── State ──────────────────────────────────────────────────────────── */
        const state = {
          editor: null,    // monaco.editor.IStandaloneCodeEditor
          diffEditor: null,    // monaco.editor.IStandaloneDiffEditor
          currentPath: null,
          originalText: '',      // last-saved content (for dirty check)
          currentExt: '',
          diff: null,    // {path, action, original, suggested, explanation}
          savedSession: null,    // sid the editor was last loaded for
          // Monotonic generation tokens — incremented on every loadFile/
          // showDiff/reset.  Async paths capture the token before each
          // await and bail if the token has moved on, so a slow Monaco
          // load can't mount onto a torn-down DOM (architect R2).
          gen: 0,
          diffGen: 0,
          // Phase 21.2 — queue of suggestions the user said "review later"
          // on (or that the policy decided to skip auto-apply for).
          // Each entry: {path, action, original, suggested, explanation,
          //              replaceSelection, ts}.  Acts as a FIFO; the
          //              "Accept all" toolbar button drains it in order.
          pendingSuggestions: [],
        };

        /* ── Language detection ─────────────────────────────────────────────── */
        const LANG_BY_EXT = {
          '.py': 'python', '.js': 'javascript', '.mjs': 'javascript',
          '.cjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript',
          '.jsx': 'javascript', '.json': 'json', '.html': 'html',
          '.htm': 'html', '.css': 'css', '.scss': 'scss', '.md': 'markdown',
          '.sh': 'shell', '.yml': 'yaml', '.yaml': 'yaml', '.xml': 'xml',
          '.sql': 'sql', '.toml': 'ini', '.ini': 'ini', '.rs': 'rust',
          '.go': 'go', '.java': 'java', '.c': 'c', '.h': 'c',
          '.cpp': 'cpp', '.hpp': 'cpp', '.rb': 'ruby', '.php': 'php',
        };
        function langForExt(ext) {
          return LANG_BY_EXT[(ext || '').toLowerCase()] || 'plaintext';
        }

        /* ── DOM helpers ────────────────────────────────────────────────────── */
        function $body() { return document.getElementById('fileViewerBody'); }
        function $tb() { return document.getElementById('editorToolbar'); }
        function $output() { return document.getElementById('editorOutput'); }
        function $obody() { return document.getElementById('editorOutputBody'); }
        function $otitle() { return document.getElementById('editorOutputTitle'); }
        function $ometa() { return document.getElementById('editorOutputMeta'); }
        function $status() { return document.getElementById('saveStatus'); }

        function setStatus(text, cls) {
          const el = $status();
          if (!el) return;
          el.textContent = text || '';
          el.className = 'save-status' + (cls ? ' ' + cls : '');
        }

        /* ── Output panel ───────────────────────────────────────────────────── */
        function showOutput(title, html, meta) {
          $otitle().textContent = title || 'Output';
          $ometa().textContent = meta || '';
          $obody().innerHTML = html || '';
          $output().classList.remove('collapsed');
        }
        window.clearEditorOutput = function () {
          $obody().innerHTML = '';
          $ometa().textContent = '';
        };
        window.hideEditorOutput = function () {
          $output().classList.add('collapsed');
        };

        /* ── Editor lifecycle ───────────────────────────────────────────────── */
        function disposeEditor() {
          if (state.editor) {
            try { state.editor.dispose(); } catch (_) { }
            state.editor = null;
          }
        }

        async function mountEditor(content, ext, gen) {
          const body = $body();
          body.innerHTML = '<div class="editor-mount" id="editorMount"></div>';
          const mount = document.getElementById('editorMount');

          let monaco;
          try {
            monaco = await ensureMonaco();
          } catch (_) {
            // Hard fallback — render <pre>.  Honor the generation token
            // so we don't paint stale content over a newer file.
            if (gen !== state.gen) return null;
            body.innerHTML = `<pre>${escapeHtml(content)}</pre>`;
            $tb().style.display = 'none';
            return null;
          }
          // Stale load — a newer file open or a reset has fired during
          // the await; abort silently so we don't mount on a torn-down
          // body or a body that now belongs to a different file.
          if (gen !== state.gen) return null;

          disposeEditor();
          state.editor = monaco.editor.create(mount, {
            value: content,
            language: langForExt(ext),
            theme: 'vs-dark',
            automaticLayout: true,
            fontSize: 13,
            minimap: { enabled: true, scale: 1 },
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            tabSize: 4,
            renderWhitespace: 'selection',
            fixedOverflowWidgets: true,
          });

          // Ctrl/Cmd+S → save
          state.editor.addCommand(
            monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
            () => window.saveCurrentFile()
          );
          // Track dirty state for status indicator
          state.editor.onDidChangeModelContent(() => {
            const dirty = state.editor.getValue() !== state.originalText;
            setStatus(dirty ? 'Unsaved changes' : '', dirty ? 'busy' : '');
          });
          return state.editor;
        }

        /* ── Public: load file (called from openFileFromTree) ───────────────── */
        async function loadFile(path, content, ext) {
          // Bump generation so any in-flight mountEditor for a previous
          // load is cancelled at its next await boundary.
          const gen = ++state.gen;
          state.currentPath = path;
          state.originalText = content || '';
          state.currentExt = ext || '';
          state.savedSession = currentSession;
          $tb().style.display = '';
          setStatus('');
          await mountEditor(content || '', ext || '', gen);
        }

        function reset() {
          // Invalidate any in-flight loads/diff loads so they can't paint
          // back into a torn-down DOM (architect R2).
          state.gen += 1;
          state.diffGen += 1;
          // Tear down any open diff modal too — session switches must
          // guarantee a fully clean editor surface.
          if (typeof window.rejectDiff === 'function') {
            try { window.rejectDiff(); } catch (_) { }
          }
          disposeEditor();
          state.currentPath = null;
          state.originalText = '';
          state.currentExt = '';
          // Phase 21.2 — drop any queued AI suggestions when the editor
          // resets (session switch / file close).  Suggestions are bound
          // to a specific {sid, path}; once that context is gone the
          // queue would only confuse the user.
          state.pendingSuggestions = [];
          if (typeof window._refreshPendingBadge === 'function') {
            try { window._refreshPendingBadge(); } catch (_) { }
          } else {
            const _btn = document.getElementById('pendingSuggBtn');
            const _cnt = document.getElementById('pendingSuggCount');
            if (_cnt) _cnt.textContent = '0';
            if (_btn) _btn.classList.add('hidden');
          }
          if ($tb()) $tb().style.display = 'none';
          if ($output()) $output().classList.add('collapsed');
          setStatus('');
        }

        /* ── Save ───────────────────────────────────────────────────────────── */
        window.saveCurrentFile = async function () {
          if (!state.editor || !state.currentPath || !currentSession) return;
          const content = state.editor.getValue();
          if (content === state.originalText) {
            setStatus('No changes', '');
            return;
          }
          setStatus('Saving…', 'busy');
          const r = await api('POST', `/api/save-file/${currentSession}`,
            { path: state.currentPath, content });
          if (r.ok && r.data && r.data.ok) {
            state.originalText = content;
            const sz = r.data.size || content.length;
            setStatus(`Saved · ${fmtFileSize(sz)}`, 'ok');
            // Update header meta
            $('fileViewerMeta').textContent = `${fmtFileSize(sz)} · text`;
            toast('Saved ' + state.currentPath, 'ok');
          } else {
            const err = (r.data && (r.data.message || r.data.error)) || 'save failed';
            setStatus('Error: ' + err, 'err');
            toast('Save failed: ' + err, 'err');
          }
        };

        /* ── Run code ───────────────────────────────────────────────────────── */
        function renderRunResult(res) {
          if (!res) return '<span class="err">No result.</span>';
          const lines = [];
          const okCls = res.status === 'success' ? 'ok' : 'err';
          lines.push(`<span class="${okCls}">status: ${escapeHtml(res.status || '?')}</span>` +
            `  <span class="meta">exit=${res.exit_code} · ${(+res.duration_sec || 0).toFixed(2)}s</span>`);
          if (res.output) {
            lines.push('\n<span class="meta">── stdout ──</span>\n' + escapeHtml(res.output));
          }
          if (res.error) {
            lines.push('\n<span class="err">── stderr ──</span>\n' + escapeHtml(res.error));
          }
          if (res.truncated_stdout) lines.push('<span class="meta">[stdout truncated]</span>');
          if (res.truncated_stderr) lines.push('<span class="meta">[stderr truncated]</span>');
          return lines.join('\n');
        }

        window.runCurrentFile = async function () {
          if (!state.editor || !currentSession) return;
          const code = state.editor.getValue();
          showOutput('Running…', '<span class="meta">Executing in sandbox…</span>', '');
          const r = await api('POST', `/api/run-code/${currentSession}`,
            { path: state.currentPath, code });
          if (!r.ok || !r.data || !r.data.ok) {
            const err = (r.data && (r.data.message || r.data.error)) || 'run failed';
            showOutput('Run', `<span class="err">${escapeHtml(err)}</span>`, '');
            return;
          }
          showOutput('Run output', renderRunResult(r.data.result),
            state.currentPath ? state.currentPath : 'inline');
        };

        /* ── Tests ──────────────────────────────────────────────────────────── */
        function renderTestResult(out) {
          if (!out) return '<span class="err">No result.</span>';
          const lines = [];
          const passed = !!out.passed;
          const total = out.total_tests || 0;
          const failed = out.failed_tests || 0;
          const cls = passed ? 'ok' : 'err';
          lines.push(`<span class="${cls}">${passed ? '✓ all passed' : '✗ failures'}</span>` +
            `  <span class="meta">${total - failed}/${total} passed · ${(+out.duration_sec || 0).toFixed(2)}s</span>`);
          const tests = out.generated_tests || [];
          const errors = out.errors || [];
          const errMap = {};
          for (const e of errors) {
            const n = (e && (e.name || e.test)) || '';
            errMap[n] = e;
          }
          for (const t of tests) {
            const name = (t && t.name) || '';
            const isErr = !!errMap[name];
            const tag = isErr ? '✗' : '✓';
            const rowCls = isErr ? 'fail' : 'pass';
            const desc = (t.description || '').toString();
            const call = (t.call || '').toString();
            lines.push(
              `<div class="test-row ${rowCls}">${tag} ` +
              `${escapeHtml(name)}<br>` +
              `<span class="meta">${escapeHtml(desc)}</span><br>` +
              `<span class="meta">→ ${escapeHtml(call)}</span>` +
              (isErr ? `<br><span class="err">${escapeHtml((errMap[name].error || '').slice(0, 400))}</span>` : '') +
              `</div>`
            );
          }
          return lines.join('\n');
        }

        window.testCurrentFile = async function () {
          if (!state.editor || !currentSession) return;
          const code = state.editor.getValue();
          showOutput('Tests', '<span class="meta">Generating + running tests…</span>', '');
          const r = await api('POST', `/api/run-tests/${currentSession}`,
            { path: state.currentPath, code });
          if (!r.ok || !r.data || !r.data.ok) {
            const err = (r.data && (r.data.message || r.data.error)) || 'tests failed';
            showOutput('Tests', `<span class="err">${escapeHtml(err)}</span>`, '');
            return;
          }
          const fn = r.data.function_name || '?';
          showOutput('Tests · ' + fn, renderTestResult(r.data.result), state.currentPath || '');
        };

        /* ── AI actions (fix / optimize / explain / refactor) ──────────────── */
        window.runAIAction = async function (action) {
          if (!state.editor || !currentSession) return;
          const sel = state.editor.getSelection();
          const model = state.editor.getModel();
          let codeForAI = state.editor.getValue();
          let selText = '';
          if (sel && model && !sel.isEmpty()) {
            selText = model.getValueInRange(sel);
            // If user has a selection, prefer it as the focus.
            codeForAI = selText;
          }
          // Capture the request context up front — if the user switches
          // file/session/selection during the await, we must not apply
          // the AI suggestion to the new buffer (architect Phase 21.2 #2).
          const reqCtx = {
            sid: currentSession,
            path: state.currentPath,
            gen: state.gen,
            // Snapshot selection range so the auto-apply path can verify
            // it's still pointed at the same region the user picked.
            selRange: sel && !sel.isEmpty()
              ? {
                startLineNumber: sel.startLineNumber,
                startColumn: sel.startColumn,
                endLineNumber: sel.endLineNumber,
                endColumn: sel.endColumn
              }
              : null,
            selText,
            originalCode: codeForAI,
          };
          showOutput(`AI · ${action}`, '<span class="meta">Asking the model…</span>', state.currentPath || '');
          const payload = {
            path: reqCtx.path, action, code: codeForAI,
          };
          if (selText) payload.selection = selText;

          const r = await api('POST', `/api/code-action/${reqCtx.sid}`, payload);

          // Context check — if the user has navigated away the response
          // is meaningless.  We still log to the output panel of the
          // original target session if it's still the foreground one.
          const ctxStillCurrent = (
            reqCtx.sid === currentSession &&
            reqCtx.path === state.currentPath &&
            reqCtx.gen === state.gen
          );

          if (!r.ok) {
            if (ctxStillCurrent) {
              showOutput(`AI · ${action}`,
                `<span class="err">Request failed.</span>`, '');
            }
            return;
          }
          const d = r.data || {};
          if (d.ok === false) {
            if (ctxStillCurrent) {
              const reason = d.reason || 'unknown';
              const msg = d.message || '';
              showOutput(`AI · ${action}`,
                `<span class="err">No suggestion (${escapeHtml(reason)}).</span>` +
                (msg ? `\n<span class="meta">${escapeHtml(msg)}</span>` : ''), '');
            }
            return;
          }
          // Success
          const explanation = d.explanation || '';
          const suggested = d.suggested_code || '';
          if (action === 'explain' && !suggested) {
            // Explain is prose-only — never auto-applies, always shown
            // in the output panel (no diff).  Only render if the user
            // is still on the same buffer; otherwise stay quiet.
            if (ctxStillCurrent) {
              showOutput('AI · explain',
                `<div>${escapeHtml(explanation || '(empty)')}</div>`,
                state.currentPath || '');
            }
            return;
          }
          const decision = d.policy_decision || {};
          const spec = {
            sid: reqCtx.sid,
            path: reqCtx.path,
            action,
            // `original` snapshots the buffer/selection at request time
            // — used as the diff "original" side and never mutated.
            original: reqCtx.selText || reqCtx.originalCode,
            suggested: suggested || explanation,
            explanation,
            replaceSelection: !!reqCtx.selText,
            selRange: reqCtx.selRange,
          };
          // If we navigated away mid-flight, NEVER mutate the new buffer
          // and NEVER pop a modal that would surprise the user.  Queue
          // the suggestion against its original {sid, path} so the user
          // can apply it later from the toolbar when they're back there.
          if (!ctxStillCurrent) {
            if (spec.suggested) {
              window.queuePendingSuggestion(spec);
              toast(`AI ${action} ready for ${spec.path || 'file'} — queued`, 'ok');
            }
            return;
          }
          // Phase 21.2 — honour the server-side policy decision.  When
          // the policy allows auto-apply we skip the diff modal and edit
          // the buffer directly; otherwise we still show the modal so
          // the user can accept / reject as before.
          if (decision.auto_apply && spec.suggested) {
            const applied = applySuggestionDirect(spec);
            if (applied) {
              const stats = decision.diff_stats || {};
              toast(`Auto-applied ${action} `
                + `(${stats.lines_changed || 0} lines / ${stats.hunks || 0} hunks · ${decision.reason || 'policy'})`,
                'ok');
              setStatus('Unsaved changes', 'busy');
            } else {
              // applySuggestionDirect refused (e.g. selection lost on
              // a selection-scoped edit) — fall back to the modal so
              // the user can decide explicitly.
              toast('Selection moved — review the suggestion in the modal', 'err');
              await showDiff(spec);
            }
            return;
          }
          // No auto-apply — show diff modal as before.
          await showDiff(spec);
        };

        /* ── Direct apply (used by policy auto-apply + Accept All) ──────────── */
        // Returns true on success, false if the apply was refused.  Refusal
        // happens for selection-scoped suggestions when the live selection
        // is missing OR has changed from the snapshot taken at request
        // time.  Refusing instead of falling back to a full-file overwrite
        // avoids turning a small policy-approved selection edit into a
        // destructive whole-file replace, and refusing on selection drift
        // protects against applying changes to the wrong region (architect
        // R1 #2 / R2 #1).
        function _sameRange(a, b) {
          return !!a && !!b
            && a.startLineNumber === b.startLineNumber
            && a.startColumn === b.startColumn
            && a.endLineNumber === b.endLineNumber
            && a.endColumn === b.endColumn;
        }
        function applySuggestionDirect(spec) {
          if (!state.editor || !spec || !spec.suggested) return false;
          if (spec.replaceSelection) {
            const sel = state.editor.getSelection();
            if (!sel || sel.isEmpty()) return false;
            // Strict identity check: the selection at apply time must
            // be the same range the user picked when they clicked the
            // AI action button.  If they changed their mind we route
            // the suggestion through the diff modal instead.
            if (spec.selRange && !_sameRange(spec.selRange, {
              startLineNumber: sel.startLineNumber,
              startColumn: sel.startColumn,
              endLineNumber: sel.endLineNumber,
              endColumn: sel.endColumn,
            })) {
              return false;
            }
            state.editor.executeEdits('ai-action',
              [{ range: sel, text: spec.suggested, forceMoveMarkers: true }]);
          } else {
            state.editor.setValue(spec.suggested);
          }
          return true;
        }

        /* ── Pending-suggestions queue (Phase 21.2) ─────────────────────────── */
        // Each spec carries {sid, path, action, original, suggested, ...} —
        // the queue is filtered by BOTH sid + path on drain so a suggestion
        // can never cross-apply between sessions or files (architect #4).
        function _refreshPendingBadge() {
          const btn = document.getElementById('pendingSuggBtn');
          const cnt = document.getElementById('pendingSuggCount');
          // Show only suggestions matching the current {sid, path} — that
          // is what "Accept all" can actually drain right now.
          const here = state.pendingSuggestions.filter(s =>
            s.sid === currentSession && s.path === state.currentPath);
          const n = here.length;
          if (cnt) cnt.textContent = String(n);
          if (btn) btn.classList.toggle('hidden', n === 0);
        }
        // Make the badge refresher reachable from `reset()` (which lives in
        // the same IIFE but runs before this IIFE-scoped closure was hoisted
        // on first run) — exposing it on `window` keeps the contract simple.
        window._refreshPendingBadge = _refreshPendingBadge;
        window.queuePendingSuggestion = function (spec) {
          if (!spec || !spec.suggested) return;
          // Defensive defaults — older callers may not have set sid yet.
          state.pendingSuggestions.push({
            sid: spec.sid || currentSession,
            ...spec,
            ts: Date.now(),
          });
          _refreshPendingBadge();
        };
        window.acceptAllPending = function () {
          if (!state.editor) return;
          const sid = currentSession;
          const path = state.currentPath;
          const matches = (s) => s.sid === sid && s.path === path;
          const queue = state.pendingSuggestions.filter(matches);
          if (!queue.length) return;
          let applied = 0, refused = 0;
          for (const spec of queue) {
            if (applySuggestionDirect(spec)) applied += 1;
            else refused += 1;
          }
          // Remove only the ones we drained; cross-{sid,path} entries
          // stay queued so they can be applied when the user navigates
          // back.  Refused (selection-lost) entries are also dropped —
          // re-queueing them would just spam the badge.
          state.pendingSuggestions = state.pendingSuggestions.filter(
            s => !matches(s));
          _refreshPendingBadge();
          if (applied) {
            setStatus('Unsaved changes', 'busy');
            const others = state.pendingSuggestions.length;
            toast(`Applied ${applied} pending suggestion(s)`
              + (refused ? `, ${refused} skipped (selection lost)` : '')
              + (others ? `, ${others} queued for other files/sessions` : ''),
              'ok');
          } else if (refused) {
            toast(`${refused} suggestion(s) skipped — selection lost`, 'err');
          }
        };

        /* ── Diff modal ─────────────────────────────────────────────────────── */
        async function showDiff(spec) {
          // Generation token — if the user rejects (or another diff opens)
          // before Monaco resolves, we'll bail out instead of painting an
          // orphan diff editor onto a closed modal (architect R2).
          const gen = ++state.diffGen;
          state.diff = spec;
          document.getElementById('diffModalTitle').textContent =
            `AI ${spec.action} suggestion`;
          document.getElementById('diffModalSubtitle').textContent =
            spec.path || '';
          const expEl = document.getElementById('diffExplain');
          if (spec.explanation) {
            expEl.textContent = spec.explanation;
            expEl.classList.remove('hidden');
          } else {
            expEl.textContent = '';
            expEl.classList.add('hidden');
          }
          document.getElementById('diffModal').classList.add('open');

          // Build/refresh the diff editor.
          let monaco;
          try { monaco = await ensureMonaco(); }
          catch (_) {
            // Without monaco, just show plain accept on the explanation.
            return;
          }
          if (gen !== state.diffGen) return;     // stale → caller closed
          const mount = document.getElementById('diffMount');
          if (state.diffEditor) {
            try { state.diffEditor.dispose(); } catch (_) { }
            state.diffEditor = null;
          }
          // Dispose the previous models before creating new ones so we
          // don't accumulate them inside Monaco's global model registry.
          if (state._diffOriginalModel) {
            try { state._diffOriginalModel.dispose(); } catch (_) { }
            state._diffOriginalModel = null;
          }
          if (state._diffModifiedModel) {
            try { state._diffModifiedModel.dispose(); } catch (_) { }
            state._diffModifiedModel = null;
          }
          mount.innerHTML = '';
          state.diffEditor = monaco.editor.createDiffEditor(mount, {
            theme: 'vs-dark',
            automaticLayout: true,
            renderSideBySide: true,
            readOnly: false,
            originalEditable: false,
            fontSize: 13,
          });
          // Track the models we create so rejectDiff() can dispose them
          // explicitly — Monaco doesn't free them when the diff editor
          // itself is disposed, leading to a slow leak across repeated
          // AI suggestions (architect-flagged).
          state._diffOriginalModel = monaco.editor.createModel(
            spec.original || '', langForExt(state.currentExt));
          state._diffModifiedModel = monaco.editor.createModel(
            spec.suggested || '', langForExt(state.currentExt));
          state.diffEditor.setModel({
            original: state._diffOriginalModel,
            modified: state._diffModifiedModel,
          });
        }

        window.saveDiffForLater = function () {
          // Capture the (possibly user-edited) modified-side text and
          // queue the suggestion, then close the modal without touching
          // the buffer.  Useful when the user wants to chain several
          // small fixes and apply them in one go.
          if (!state.diff) { window.rejectDiff(); return; }
          const spec = { ...state.diff };
          if (state.diffEditor) {
            const mod = state.diffEditor.getModel();
            if (mod && mod.modified) spec.suggested = mod.modified.getValue();
          }
          if (!spec.suggested) {
            toast('Nothing to queue', 'err');
            window.rejectDiff();
            return;
          }
          window.queuePendingSuggestion(spec);
          window.rejectDiff();
          toast('Queued — apply via toolbar ✓ Accept all', 'ok');
        };

        window.acceptDiff = function () {
          if (!state.diff || !state.editor) { window.rejectDiff(); return; }
          const spec = state.diff;
          // Pull edited text from the modified side of the diff editor so
          // the user can tweak the AI output before applying.
          let newText = spec.suggested || '';
          if (state.diffEditor) {
            const mod = state.diffEditor.getModel();
            if (mod && mod.modified) newText = mod.modified.getValue();
          }
          if (spec.replaceSelection) {
            const sel = state.editor.getSelection();
            // Phase 21.2 R3 (architect) — even on manual Accept, refuse
            // to apply a selection-scoped suggestion to a different
            // region than the user originally picked.  Prevents a
            // "modal stays open while user clicks elsewhere" footgun.
            if (!sel || sel.isEmpty()) {
              toast('Selection lost — re-select and try again', 'err');
              return;
            }
            if (spec.selRange && !_sameRange(spec.selRange, {
              startLineNumber: sel.startLineNumber,
              startColumn: sel.startColumn,
              endLineNumber: sel.endLineNumber,
              endColumn: sel.endColumn,
            })) {
              toast('Selection moved — re-select the original region '
                + 'or use Save for later', 'err');
              return;
            }
            state.editor.executeEdits('ai-action',
              [{ range: sel, text: newText, forceMoveMarkers: true }]);
          } else {
            state.editor.setValue(newText);
          }
          window.rejectDiff(); // close modal
          toast('Applied AI ' + spec.action + ' (review then save)', 'ok');
          setStatus('Unsaved changes', 'busy');
        };

        window.rejectDiff = function () {
          // Invalidate any in-flight showDiff() so its post-await
          // continuation doesn't recreate the editor on a closed modal.
          state.diffGen += 1;
          document.getElementById('diffModal').classList.remove('open');
          if (state.diffEditor) {
            try { state.diffEditor.dispose(); } catch (_) { }
            state.diffEditor = null;
          }
          // Dispose the diff models too — disposing the diff editor alone
          // does NOT free the underlying ITextModel instances.
          if (state._diffOriginalModel) {
            try { state._diffOriginalModel.dispose(); } catch (_) { }
            state._diffOriginalModel = null;
          }
          if (state._diffModifiedModel) {
            try { state._diffModifiedModel.dispose(); } catch (_) { }
            state._diffModifiedModel = null;
          }
          const mount = document.getElementById('diffMount');
          if (mount) mount.innerHTML = '';
          state.diff = null;
        };

        /* ── Layout toggles ─────────────────────────────────────────────────── */
        window.toggleEditorFullscreen = function () {
          document.body.classList.toggle('editor-fullscreen');
          if (state.editor) {
            // Force monaco to re-measure
            setTimeout(() => state.editor.layout(), 50);
          }
        };
        window.toggleSessionPanel = function () {
          document.body.classList.toggle('session-panel-hidden');
          if (state.editor) {
            setTimeout(() => state.editor.layout(), 50);
          }
        };

        /* ── Public API ─────────────────────────────────────────────────────── */
        function isDirty() {
          // Phase 21.1 — used by the Files tab to guard destructive UX
          // (file switch, rename, delete) when the buffer has unsaved edits.
          if (!state.editor || !state.currentPath) return false;
          try { return state.editor.getValue() !== state.originalText; }
          catch (_) { return false; }
        }
        function getCurrentPath() { return state.currentPath; }

        window.CodeEditor = {
          loadFile,    // (path, content, ext) → Promise
          reset,       // dispose + hide toolbar
          ensureMonaco,
          isDirty,           // Phase 21.1 — true iff editor buffer differs from disk
          getCurrentPath,    // Phase 21.1 — null when no file is loaded
        };
      })();

      /* ─────────────────────────────────────────────────────────────────────────
         Phase 22 — AI Developer Platform UI
         Terminal · Multi-agent · Timeline · Lessons · Model routing · Predictive
         ───────────────────────────────────────────────────────────────────────── */
      (function () {
        const $$ = (id) => document.getElementById(id);
        const esc = (s) => String(s == null ? '' : s)
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

        /* ── Terminal ─────────────────────────────────────────────────────── */
        let termHistory = [];
        let termHistIdx = -1;

        function termAppend(html) {
          const out = $$('terminalOutput');
          if (!out) return;
          out.insertAdjacentHTML('beforeend', html);
          out.scrollTop = out.scrollHeight;
        }
        window.terminalClear = function () {
          const out = $$('terminalOutput');
          if (out) out.innerHTML = '<span class="muted">cleared.</span>\n';
        };
        window.terminalKeydown = function (e) {
          const inp = $$('terminalInput');
          if (e.key === 'Enter') { e.preventDefault(); window.terminalRun(); return; }
          if (e.key === 'ArrowUp') {
            if (termHistory.length === 0) return;
            e.preventDefault();
            termHistIdx = Math.max(0, (termHistIdx < 0 ? termHistory.length : termHistIdx) - 1);
            inp.value = termHistory[termHistIdx] || '';
          } else if (e.key === 'ArrowDown') {
            if (termHistory.length === 0) return;
            e.preventDefault();
            termHistIdx = Math.min(termHistory.length, termHistIdx + 1);
            inp.value = (termHistIdx >= termHistory.length) ? '' : termHistory[termHistIdx];
          }
        };
        /* ── Phase 24 — terminal mode state + bridge settings ─────────────── */
        // Local cache of the persisted mode config so we don't have to GET on
        // every keystroke. Refreshed by `terminalLoadMode()` whenever the
        // settings panel opens or after a Save.
        let termMode = 'sandbox';
        let termActiveStream = null;     // current EventSource (so we can close)
        function termSetModeBadge(mode) {
          const b = $$('terminalModeBadge');
          if (!b) return;
          const isLocal = mode === 'local';
          b.textContent = 'mode: ' + (isLocal ? 'local bridge' : 'sandbox');
          b.style.background = isLocal ? '#f8514922' : '#1f6feb22';
          b.style.color = isLocal ? '#ffa198' : '#79c0ff';
          b.style.borderColor = isLocal ? '#f8514955' : '#1f6feb55';
        }
        window.terminalToggleSettings = function () {
          const p = $$('terminalSettings');
          if (!p) return;
          const showing = p.style.display !== 'none';
          p.style.display = showing ? 'none' : 'block';
          if (!showing) {
            window.terminalLoadMode();
            // Phase 25 — refresh AI automation toggle state in lockstep.
            if (typeof window.aiLoadSettings === 'function') {
              window.aiLoadSettings();
            }
          }
        };
        window.terminalOnModeChange = function (val) {
          const fields = $$('terminalBridgeFields');
          const warn = $$('terminalLocalWarning');
          const note = $$('terminalSandboxNote');
          const isLocal = val === 'local';
          if (fields) fields.style.display = isLocal ? 'block' : 'none';
          if (warn) warn.style.display = isLocal ? 'block' : 'none';
          if (note) note.style.display = isLocal ? 'none' : 'block';
        };
        window.terminalLoadMode = async function () {
          const r = await api('GET', '/api/terminal/mode');
          if (!r.ok) return;
          const c = (r.data && r.data.config) || {};
          termMode = c.mode || 'sandbox';
          termSetModeBadge(termMode);
          // Reflect into the form
          document.querySelectorAll('input[name="terminalMode"]').forEach(ipt => {
            ipt.checked = (ipt.value === termMode);
          });
          const urlEl = $$('terminalBridgeUrl');
          const tokEl = $$('terminalBridgeToken');
          if (urlEl) urlEl.value = c.local_url || '';
          if (tokEl) tokEl.placeholder = c.token_set
            ? '(token saved — leave blank to keep)'
            : '(optional, recommended)';
          window.terminalOnModeChange(termMode);
        };
        function termSetBridgeStatus(text, color) {
          const el = $$('terminalBridgeStatus');
          if (!el) return;
          el.textContent = text || '';
          el.style.color = color || '';
        }
        window.terminalCheckBridge = async function () {
          const url = ($$('terminalBridgeUrl').value || '').trim();
          const tok = ($$('terminalBridgeToken').value || '').trim();
          if (!url) { termSetBridgeStatus('Enter a bridge URL first.', '#f85149'); return; }
          termSetBridgeStatus('Testing…', '#8b949e');
          const r = await api('POST', '/api/terminal/check-bridge',
            { local_url: url, local_token: tok });
          const d = (r && r.data) || {};
          if (r.ok && d.ok) {
            const h = d.health || {};
            termSetBridgeStatus(
              `OK · ${h.platform || '?'} · ${h.shell || '?'}`, '#3fb950');
          } else {
            const why = d.error || (r.error || 'failed');
            const detail = d.detail ? ` (${d.detail})` : '';
            termSetBridgeStatus('Failed: ' + why + detail, '#f85149');
          }
        };
        window.terminalSaveMode = async function () {
          const mode = (document.querySelector(
            'input[name="terminalMode"]:checked') || {}).value || 'sandbox';
          const url = ($$('terminalBridgeUrl').value || '').trim();
          const tok = ($$('terminalBridgeToken').value || '').trim();
          const payload = { mode, local_url: url };
          // Only send token when the user actually typed one — otherwise the
          // server keeps whatever was previously saved.
          if (tok) payload.local_token = tok;
          const r = await api('POST', '/api/terminal/mode', payload);
          const d = (r && r.data) || {};
          if (!r.ok || !d.ok) {
            termSetBridgeStatus('Save failed: ' + (d.error || r.error || ''), '#f85149');
            return;
          }
          termMode = (d.config && d.config.mode) || 'sandbox';
          termSetModeBadge(termMode);
          termSetBridgeStatus('Saved.', '#3fb950');
          // Clear the typed token from the field so it isn't visible/re-sent.
          const tokEl = $$('terminalBridgeToken');
          if (tokEl) {
            tokEl.value = ''; tokEl.placeholder =
              d.config.token_set ? '(token saved — leave blank to keep)'
                : '(optional, recommended)';
          }
          termAppend(`<div class="muted">[mode set to ${esc(termMode)}]</div>`);
        };

        // Stream a local-mode command via SSE. Falls back to buffered
        // `/api/terminal/run` if EventSource isn't available or the server
        // says streaming is unsupported.
        // ── Phase 25 — AI automation helpers ─────────────────────────────
        window.aiLoadSettings = async function () {
          const r = await api('GET', '/api/ai/terminal-settings');
          const s = (r && r.data && r.data.settings) || {};
          const en = $$('aiTerminalEnabled');
          const ai = $$('aiAllowInstall');
          const ar = $$('aiAllowRun');
          if (en) en.checked = !!s.ai_enabled;
          if (ai) ai.checked = !!s.allow_install_auto;
          if (ar) ar.checked = ('allow_run_auto' in s) ? !!s.allow_run_auto : true;
        };
        window.aiSaveSettings = async function () {
          const body = {
            ai_enabled: !!($$('aiTerminalEnabled') || {}).checked,
            allow_install_auto: !!($$('aiAllowInstall') || {}).checked,
            allow_run_auto: !!($$('aiAllowRun') || {}).checked,
          };
          const status = $$('aiAutomationStatus');
          if (status) status.textContent = 'saving…';
          const r = await api('POST', '/api/ai/terminal-settings', body);
          if (status) {
            const ok = r && r.data && r.data.ok;
            status.textContent = ok ? 'saved' :
              ('error: ' + ((r && r.data && (r.data.errors || []).join(',')) || 'unknown'));
            setTimeout(() => { if (status.textContent === 'saved') status.textContent = ''; }, 1500);
          }
        };
        function _aiSurfaceResult(label, r) {
          const status = $$('aiAutomationStatus');
          if (!status) return;
          const d = (r && r.data) || {};
          if ((r && r.status === 403) || d.error === 'ai_disabled') {
            status.textContent = label + ': enable "Allow AI to run terminal commands" first';
            status.style.color = '#f85149';
            return;
          }
          if ((r && r.status === 409) || d.error === 'confirmation_required') {
            status.textContent = label + ': enable "Auto-approve install" first';
            status.style.color = '#f85149';
            return;
          }
          if (d.ok) {
            status.style.color = '';
            const tail = d.ran === false ? ' (no requirements.txt)' :
              (d.modules ? ` (${d.modules.length} module${d.modules.length === 1 ? '' : 's'})` :
                (typeof d.exit !== 'undefined' ? ` (exit ${d.exit})` : ''));
            status.textContent = label + ': ok' + tail;
          } else {
            status.style.color = '#f85149';
            status.textContent = label + ': ' + (d.error || 'failed');
          }
        }
        window.aiRunSetup = async function () {
          const status = $$('aiAutomationStatus');
          if (status) { status.textContent = 'running setup…'; status.style.color = ''; }
          const r = await api('POST', '/api/ai/run-setup', {});
          _aiSurfaceResult('Setup', r);
        };
        window.aiAutoFixEnv = async function () {
          const status = $$('aiAutomationStatus');
          if (status) { status.textContent = 'scanning recent errors…'; status.style.color = ''; }
          const r = await api('POST', '/api/ai/auto-fix-env', {});
          _aiSurfaceResult('Auto-fix', r);
        };

        function terminalStreamLocal(cmd, sid) {
          if (typeof EventSource === 'undefined') return false;
          try {
            const qs = new URLSearchParams({ cmd: cmd });
            if (sid) qs.set('sid', sid);
            const es = new EventSource('/api/terminal/stream?' + qs.toString());
            termActiveStream = es;
            const startedAt = Date.now();
            const safeJson = (s) => {
              try { return JSON.parse(s); }
              catch (e) { return s; }
            };
            es.addEventListener('stdout', (ev) => {
              const line = safeJson(ev.data);
              termAppend(`<div>${esc(typeof line === 'string' ? line : ev.data)}</div>`);
            });
            es.addEventListener('stderr', (ev) => {
              const line = safeJson(ev.data);
              termAppend(`<div style="color:#ffa657">${esc(typeof line === 'string' ? line : ev.data)}</div>`);
            });
            es.addEventListener('done', (ev) => {
              const d = safeJson(ev.data) || {};
              const ms = Math.round(((d.duration_sec) || ((Date.now() - startedAt) / 1000)) * 1000);
              const meta = `[exit ${d.exit_code != null ? d.exit_code : '?'} · ${ms}ms${d.timed_out ? ' · TIMEOUT' : ''}]`;
              termAppend(`<div class="muted" style="font-size:0.74rem">${esc(meta)}</div>`);
              es.close(); termActiveStream = null;
            });
            es.addEventListener('error', (ev) => {
              // EventSource fires 'error' both for app-level errors
              // (with .data) and connection drops (no .data). Try to
              // surface the app error; otherwise show a generic note.
              let msg = 'stream closed';
              if (ev && ev.data) {
                const d = safeJson(ev.data);
                if (d && d.error) msg = d.error +
                  (d.detail ? ': ' + d.detail : '');
                else if (typeof d === 'string') msg = d;
              }
              termAppend(`<div style="color:#f85149">[stream] ${esc(msg)}</div>`);
              try { es.close(); } catch (e) { }
              termActiveStream = null;
            });
            return true;
          } catch (e) {
            return false;
          }
        }

        window.terminalRun = async function () {
          const inp = $$('terminalInput');
          const cmd = (inp.value || '').trim();
          if (!cmd) return;
          termHistory.push(cmd);
          termHistIdx = termHistory.length;
          inp.value = '';
          // If a previous stream is still open (e.g. user spammed Enter),
          // close it so output doesn't interleave.
          if (termActiveStream) { try { termActiveStream.close(); } catch (e) { } termActiveStream = null; }
          termAppend(`<div><span style="color:#7ee787">$</span> ${esc(cmd)}</div>`);
          const sid = (typeof currentSession !== 'undefined') ? currentSession : null;

          // Local mode: try SSE streaming first.
          if (termMode === 'local') {
            if (terminalStreamLocal(cmd, sid)) return;
            // Fall through to buffered POST if EventSource unavailable.
          }

          const r = await api('POST', '/api/terminal/run', { cmd, sid, mode: termMode });
          // Even a 400 (blocked) returns JSON in r.data with .error/.reason
          const d = (r && r.data) || {};
          if (d.error === 'command_blocked') {
            termAppend(`<div style="color:#f85149">[blocked] ${esc(d.reason || 'unsafe token')}</div>`);
            return;
          }
          if (d.error === 'bridge_not_configured' || d.error === 'bridge_unreachable'
            || d.error === 'unauthorized' || d.error === 'local_mode_disabled') {
            termAppend(`<div style="color:#f85149">[bridge] ${esc(d.error)}${d.detail ? ' — ' + esc(d.detail) : ''}</div>`);
            return;
          }
          if (!r.ok && !d.stdout && !d.stderr) {
            termAppend(`<div style="color:#f85149">[network] ${esc(r.error || 'request failed')}</div>`);
            return;
          }
          if (d.stdout) termAppend(`<div>${esc(d.stdout)}</div>`);
          if (d.stderr) termAppend(`<div style="color:#ffa657">${esc(d.stderr)}</div>`);
          const ms = Math.round((d.duration_sec || 0) * 1000);
          const meta = `[exit ${d.exit != null ? d.exit : '?'} · ${ms}ms${d.truncated ? ' · TRUNCATED' : ''}${d.timed_out ? ' · TIMEOUT' : ''}${d.mode ? ' · ' + d.mode : ''}]`;
          termAppend(`<div class="muted" style="font-size:0.74rem">${esc(meta)}</div>`);
          const cwdEl = $$('terminalCwd');
          if (cwdEl && d.cwd) cwdEl.textContent = 'cwd: ' + d.cwd;
        };
        window.terminalLoadHistory = async function () {
          const r = await api('GET', '/api/terminal/history');
          if (!r.ok) return;
          const items = (r.data && r.data.history) || [];
          if (!items.length) { termAppend('<div class="muted">no history.</div>'); return; }
          termAppend('<div class="muted">── history ──</div>');
          items.slice(-20).forEach(h => {
            termAppend(`<div class="muted">$ ${esc(h.cmd || '')} <span style="font-size:0.74rem">[${h.exit != null ? h.exit : '?'}]</span></div>`);
          });
        };

        /* ── Multi-agent state ────────────────────────────────────────────── */
        window.loadAgentsState = async function () {
          const root = $$('agentsPanel');
          if (!root) return;
          const r = await api('GET', '/api/agents/state');
          if (!r.ok) { root.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
          const agents = (r.data && r.data.agents) || [];
          const sid = (r.data && r.data.session) || '';
          if (!agents.length) { root.innerHTML = '<div class="empty">No agents reporting yet.</div>'; return; }
          root.innerHTML = agents.map(a => {
            const stCol = a.status === 'active' ? '#3fb950'
              : a.status === 'completed' ? '#79c0ff'
                : a.status === 'error' ? '#f85149'
                  : '#8b949e';
            const last = a.last_activity
              ? new Date(a.last_activity * 1000).toLocaleTimeString()
              : '—';
            return `
            <div style="border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--card)">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <span style="width:10px;height:10px;border-radius:50%;background:${stCol};display:inline-block"></span>
                    <strong>${esc(a.role)}</strong>
                    <span class="muted" style="margin-left:auto;font-size:0.75rem">${esc(a.status)}</span>
                </div>
                <div class="muted" style="font-size:0.78rem;margin-bottom:4px">events: ${a.events || 0} · last: ${esc(last)}</div>
                <div style="font-size:0.82rem;word-break:break-word">${esc(a.current_task || 'idle')}</div>
            </div>`;
          }).join('') + (sid
            ? `<div class="muted" style="grid-column:1/-1;font-size:0.74rem">Session: ${esc(sid.slice(0, 8))}…</div>`
            : '');
        };

        /* ── Timeline ─────────────────────────────────────────────────────── */
        window.loadTimeline = async function () {
          const root = $$('timelinePanel');
          if (!root) return;
          const r = await api('GET', '/api/timeline?limit=50');
          if (!r.ok) { root.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
          const items = (r.data && r.data.timeline) || [];
          if (!items.length) { root.innerHTML = '<div class="empty">No past tasks yet.</div>'; return; }
          root.innerHTML = `
            <div style="border-left:2px solid var(--border);padding-left:14px">
              ${items.map(t => {
            const when = t.created_at ? new Date(t.created_at * 1000).toLocaleString() : '—';
            const stCol = t.status === 'done' ? '#3fb950'
              : t.status === 'failed' ? '#f85149'
                : t.status === 'running' ? '#f0883e'
                  : t.status === 'stopped' ? '#d29922'
                    : '#8b949e';
            return `
                <div style="margin-bottom:14px;position:relative">
                    <span style="position:absolute;left:-19px;top:6px;width:10px;height:10px;border-radius:50%;background:${stCol}"></span>
                    <div style="font-size:0.75rem" class="muted">${esc(when)} · ${esc(t.status || '—')}${t.model ? ' · ' + esc(t.model) : ''}</div>
                    <div style="font-weight:600;margin:2px 0">${esc((t.task || '(no task)').slice(0, 140))}</div>
                    <div class="muted" style="font-size:0.78rem">decisions: ${t.decisions || 0} · logs: ${t.logs || 0} · sid: ${esc((t.sid || '').slice(0, 8))}</div>
                </div>`;
          }).join('')}
            </div>`;
        };

        /* ── Lessons (searchable) ─────────────────────────────────────────── */
        let lessonsTimer = null;
        window.loadLessons = function () {
          clearTimeout(lessonsTimer);
          lessonsTimer = setTimeout(_loadLessonsNow, 200);
        };
        async function _loadLessonsNow() {
          const root = $$('lessonsList');
          if (!root) return;
          const q = ($$('lessonsSearch') || {}).value || '';
          const r = await api('GET', `/api/lessons?q=${encodeURIComponent(q)}&limit=30`);
          if (!r.ok) { root.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
          const items = (r.data && r.data.lessons) || [];
          if (!items.length) { root.innerHTML = '<div class="empty" style="padding:14px 0">No lessons match.</div>'; return; }
          root.innerHTML = items.map(L => {
            const when = L.created_at ? new Date(L.created_at * 1000).toLocaleString() : '';
            const okCol = L.success ? '#3fb950' : '#f85149';
            return `
            <div style="border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:8px;background:var(--card)">
                <div style="font-size:0.75rem" class="muted">
                    <span style="color:${okCol}">●</span>
                    ${esc(L.error_type || '—')} · ${esc(when)}
                </div>
                <div style="margin:4px 0;color:#f85149;font-family:ui-monospace,monospace;font-size:0.82rem;white-space:pre-wrap">${esc((L.error_message || '').slice(0, 280))}</div>
                <div style="font-family:ui-monospace,monospace;font-size:0.82rem;white-space:pre-wrap">${esc((L.fix_summary || '').slice(0, 360))}</div>
            </div>`;
          }).join('');
        }

        /* ── Model routing ────────────────────────────────────────────────── */
        let routingState = null;
        let selectedRoutingProvider = null;

        window.loadModelRouting = async function () {
          const box = $$('modelRoutingBox');
          if (!box) return;
          const r = await api('GET', '/api/model-routing');
          if (!r.ok) { box.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
          routingState = r.data || {};
          if (!selectedRoutingProvider) {
            selectedRoutingProvider = (routingState.routing && routingState.routing.provider) || 'auto';
          }
          renderModelRouting();
        };

        window.selectRoutingProvider = function (p) {
          if ($$('routing_planner') && selectedRoutingProvider !== 'auto' && routingState.routing) {
            if (!routingState.routing.providers) routingState.routing.providers = {};
            if (!routingState.routing.providers[selectedRoutingProvider]) routingState.routing.providers[selectedRoutingProvider] = {};

            routingState.routing.providers[selectedRoutingProvider].planner_model = $$('routing_planner').value;
            routingState.routing.providers[selectedRoutingProvider].coding_model = $$('routing_coding').value;
            routingState.routing.providers[selectedRoutingProvider].reasoning_model = $$('routing_reasoning').value;
          }
          selectedRoutingProvider = p;
          renderModelRouting();
        };

        window.renderModelRouting = function () {
          const box = $$('modelRoutingBox');
          if (!box || !routingState) return;
          const providers = routingState.providers || [];
          const cur = routingState.routing || {};
          const def = routingState.defaults || {};
          const all_provs = cur.providers || {};

          let curProvData = {};
          if (selectedRoutingProvider !== "auto") {
            curProvData = all_provs[selectedRoutingProvider] || {};
          }

          const provRadios = providers.map(p => `
            <label style="margin-right:14px">
                <input type="radio" name="routingProvider" value="${esc(p)}"
                       ${selectedRoutingProvider === p ? 'checked' : ''}
                       onchange="selectRoutingProvider('${esc(p)}')">
                ${esc(p)}
            </label>`).join('');

          const roleRow = (role, label) => `
            <label style="display:flex;flex-direction:column">
                <span class="muted" style="font-size:0.75rem">${esc(label)}</span>
                <input type="text" id="routing_${role}"
                       value="${esc(curProvData[role + '_model'] || '')}"
                       placeholder="${esc((def.providers && def.providers[selectedRoutingProvider] && def.providers[selectedRoutingProvider][role + '_model']) || '(default)')}"
                       style="background:#0d1117;border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-family:ui-monospace,monospace;font-size:0.82rem"
                       ${selectedRoutingProvider === 'auto' ? 'disabled' : ''}>
            </label>`;

          box.innerHTML = `
            <div style="margin-bottom:10px">
                <div class="muted" style="font-size:0.78rem;margin-bottom:4px">Provider</div>
                <div>${provRadios}</div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
                ${roleRow('planner', 'Planner model')}
                ${roleRow('coding', 'Coding model')}
                ${roleRow('reasoning', 'Reasoning model')}
            </div>
            <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
                <button class="btn primary tiny" onclick="saveModelRouting()">Save</button>
                <button class="btn tiny" onclick="suggestOllamaModels()">Pick from Ollama…</button>
                <span id="routingStatus" class="muted" style="font-size:0.78rem"></span>
            </div>
            <div id="ollamaModelsHint" class="muted" style="font-size:0.74rem;margin-top:6px"></div>`;
        };

        window.saveModelRouting = async function () {
          if ($$('routing_planner') && selectedRoutingProvider !== 'auto' && routingState.routing) {
            if (!routingState.routing.providers) routingState.routing.providers = {};
            if (!routingState.routing.providers[selectedRoutingProvider]) routingState.routing.providers[selectedRoutingProvider] = {};

            routingState.routing.providers[selectedRoutingProvider].planner_model = $$('routing_planner').value;
            routingState.routing.providers[selectedRoutingProvider].coding_model = $$('routing_coding').value;
            routingState.routing.providers[selectedRoutingProvider].reasoning_model = $$('routing_reasoning').value;
          }

          const payload = {
            provider: selectedRoutingProvider,
            providers: routingState.routing.providers || {}
          };
          const status = $$('routingStatus');
          if (status) status.textContent = 'Saving…';
          const r = await api('POST', '/api/model-routing', payload);
          if (!r.ok) { if (status) status.textContent = 'Save failed: ' + (r.error || ''); return; }
          if (status) status.textContent = '✓ Saved';
          if (r.data && r.data.routing) routingState.routing = r.data.routing;
          setTimeout(() => { if (status) status.textContent = ''; }, 2200);
        };
        window.suggestOllamaModels = async function () {
          const hint = $$('ollamaModelsHint');
          if (hint) hint.textContent = 'Querying Ollama…';
          const r = await api('GET', '/api/ollama-models');
          if (!r.ok) { if (hint) hint.textContent = 'Ollama unreachable: ' + (r.error || ''); return; }
          const models = (r.data && r.data.models) || [];
          if (!models.length) { if (hint) hint.textContent = 'No local Ollama models.'; return; }
          if (hint) hint.innerHTML = 'Local models: '
            + models.map(m => `<a href="#" onclick="event.preventDefault();fillRoutingFromOllama('${esc(m)}')" style="color:#79c0ff;margin-right:6px">${esc(m)}</a>`).join('');
        };
        window.fillRoutingFromOllama = function (m) {
          if (selectedRoutingProvider === 'auto') {
            selectRoutingProvider('ollama');
          }
          ['routing_planner', 'routing_coding', 'routing_reasoning'].forEach(id => {
            const el = $$(id);
            if (el && !el.value) el.value = m;
          });
        };

        /* ── Smart Action Bar — predictive next step ──────────────────────── */
        let lastPrediction = null;
        window.refreshPrediction = async function () {
          const chip = $$('predictiveChip');
          if (!chip) return;
          const sid = (typeof currentSession !== 'undefined') ? currentSession : null;
          if (!sid) { chip.classList.add('hidden'); return; }
          const r = await api('GET', `/api/predict/${encodeURIComponent(sid)}`);
          if (!r.ok || !r.data || !r.data.suggestion) { chip.classList.add('hidden'); return; }
          const sug = r.data.suggestion;
          if (!sug || !sug.action || sug.action === 'wait') { chip.classList.add('hidden'); return; }
          lastPrediction = sug;
          const label = sug.label || sug.action;
          chip.textContent = '💡 ' + label;
          chip.title = `Suggested next step (priority: ${sug.priority || 'med'})`;
          chip.classList.remove('hidden');
        };
        window.acceptPrediction = function () {
          if (!lastPrediction) return;
          const a = lastPrediction.action;
          const chip = $$('predictiveChip');
          if (chip) chip.classList.add('hidden');
          if (a === 'fix' || a === 'optimize' || a === 'explain' || a === 'refactor') {
            if (typeof runAIAction === 'function') runAIAction(a);
          } else if (a === 'preview') {
            setActiveTab('preview');
          } else if (a === 'review') {
            setActiveTab('reasoning');
          } else if (a === 'run') {
            // Just focus the task input so user can type a new task
            const inp = document.getElementById('taskInput');
            if (inp) inp.focus();
          }
        };
        // Hook into existing session refresh — refresh prediction every poll.
        if (typeof window.addEventListener === 'function') {
          window.addEventListener('phase22:session-update', window.refreshPrediction);
          // Periodic safety net (cheap call, server-cached).
          setInterval(() => {
            if (document.hidden) return;
            window.refreshPrediction();
          }, 8000);
        }
      // ─── Export Runtime Interactions ───────────────────────────────────────
      window.api = api;
      window.$ = $;
      window.$$ = $;
      window.toast = toast;
      window.queueTask = queueTask;
      window.selectSession = selectSession;
      window.nxSelectSession = selectSession; // alias
      window.loadSessions = loadSessions;
      window.loadQueue = loadQueue;
      window.setTask = setTask;
      window.markInteracting = markInteracting;
      window.setActiveTab = setActiveTab;
      window.openSettings = openSettings;
      window.closeSettings = closeSettings;
      window.switchSettingsTab = switchSettingsTab;
      window.refreshPreview = refreshPreview;
      window.reloadPreview = reloadPreview;
      window.openPreviewWindow = openPreviewWindow;
      window.loadFilesTree = loadFilesTree;
      Object.defineProperty(window, 'currentSession', {
        configurable: true,
        get: () => currentSession,
        set: (value) => { currentSession = value; },
      });
      Object.defineProperty(window, 'planMode', {
        configurable: true,
        get: () => (window.NX && window.NX.planMode) || 'elite',
        set: (value) => {
          if (window.NX) window.NX.planMode = value;
        },
      });
      })();
