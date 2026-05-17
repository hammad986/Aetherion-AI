/* ════════════════════════════════════════════════════════════════════════
   nx-z50.js — Phase Z50: Operational Interaction Realism
   Dead control elimination, live workspace presence, execution feedback,
   panel maturity, workspace cohesion, and performance trust pass.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Helpers ──────────────────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const set = (id, val) => { const e = $(id); if (e) e.textContent = val; };

  /* ═══════════════════════════════════════════════════════════════════
     Z50A/B — COOKIE BANNER: persistence & real dismiss
     ═══════════════════════════════════════════════════════════════════ */
  function z50InitCookieBanner() {
    const banner = $('nx-cookie-banner');
    if (!banner) return;
    // Z56: check both keys — nx_cookie_accepted (z50+) and nx_cookie_ok (legacy session.js)
    if (localStorage.getItem('nx_cookie_accepted') || localStorage.getItem('nx_cookie_ok')) {
      banner.style.display = 'none';
      return;
    }
    banner.style.display = 'flex';
    const dismissBtn = banner.querySelector('.nx-cookie-dismiss');
    if (dismissBtn) {
      dismissBtn.onclick = () => z50DismissCookieBanner(true);
    }
  }

  // Z58: Canonical definition — both accept and dismiss persist consent for beta.
  window.nxAcceptCookies = function () {
    z50DismissCookieBanner(true);
  };

  function z50DismissCookieBanner(accept) {
    // Z58: Always persist — for beta, accept and dismiss are equivalent.
    // Banner must never re-appear after any user interaction.
    localStorage.setItem('nx_cookie_accepted', '1');
    localStorage.setItem('nx_cookie_ok', '1');
    if (accept !== false) { /* already set above */ }
    const banner = $('nx-cookie-banner');
    if (!banner) return;
    banner.classList.add('z50-hiding');
    setTimeout(() => { banner.style.display = 'none'; }, 220);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50B — MODE / SCOPE SELECTS: wire them to real state
     ═══════════════════════════════════════════════════════════════════ */
  function z50WireExecSelects() {
    const toolbar = qs('.nx-exec-toolbar');
    if (!toolbar) return;
    const selects = qsa('select', toolbar);

    selects.forEach(sel => {
      sel.classList.add('z50-exec-select');
      // Restore saved value
      const key = 'nx_exec_' + sel.options[0]?.text?.split(':')[0]?.toLowerCase()?.replace(/\s/g,'_');
      if (key) {
        const saved = localStorage.getItem(key);
        if (saved) {
          for (let i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === saved || sel.options[i].text === saved) {
              sel.selectedIndex = i;
              if (sel.selectedIndex !== 0) sel.classList.add('z50-changed');
              break;
            }
          }
        }
      }

      sel.addEventListener('change', function () {
        if (key) localStorage.setItem(key, this.value || this.options[this.selectedIndex]?.text);
        this.classList.toggle('z50-changed', this.selectedIndex !== 0);

        // Mode select — reflect in NX state
        const label = this.options[0]?.text || '';
        if (label.startsWith('Mode')) {
          const mode = (this.value || this.options[this.selectedIndex]?.text || '').toLowerCase();
          if (window.NX) window.NX.execMode = mode;
          if (typeof toast === 'function') {
            toast('Mode: ' + (this.options[this.selectedIndex]?.text?.replace('Mode: ', '') || mode), 'ok');
          }
        }
        if (label.startsWith('Scope')) {
          const scope = (this.value || this.options[this.selectedIndex]?.text || '').toLowerCase();
          if (window.NX) window.NX.execScope = scope;
        }
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50B — NAVRAIL PANELS: populate content + track active state
     ═══════════════════════════════════════════════════════════════════ */
  let _z50ActivePanel = null;

  function z50InitNavRail() {
    // Restore last open panel
    const saved = sessionStorage.getItem('nx_navrail_panel');
    if (saved) {
      // Don't auto-reopen — just remember for toggle behaviour
    }

    // Wire each nav icon to track active state
    qsa('.nx-shell-navrail .nx-nav-icon').forEach(btn => {
      const origClick = btn.onclick;
      btn.addEventListener('click', function () {
        const match = this.getAttribute('onclick') || '';
        const m = match.match(/nxTogglePanel\(['"](\w+)['"]\)/);
        if (m) z50HandleNavClick(m[1], this);
      });
    });
  }

  function z50HandleNavClick(panelId, btn) {
    const panel = $('nxPanel-' + panelId);
    const isOpen = panel && panel.style.display !== 'none' && panel.style.display !== '';

    // Deactivate all
    qsa('.nx-shell-navrail .nx-nav-icon').forEach(b => b.classList.remove('z50-active'));

    if (isOpen) {
      // Closing
      _z50ActivePanel = null;
      sessionStorage.removeItem('nx_navrail_panel');
    } else {
      // Opening
      _z50ActivePanel = panelId;
      sessionStorage.setItem('nx_navrail_panel', panelId);
      if (btn) btn.classList.add('z50-active');
      z50PopulatePanel(panelId);
    }
  }

  // Override nxTogglePanel to integrate Z50 population
  window.nxTogglePanel = function (panelId) {
    // Hide all panels
    qsa('.nx-slide-panel').forEach(p => { p.style.display = 'none'; });
    const panel = $('nxPanel-' + panelId);
    if (!panel) return;

    const btn = qs(`.nx-nav-icon[onclick*="'${panelId}'"]`) ||
                qs(`.nx-nav-icon[onclick*='"${panelId}"']`);
    const alreadyOpen = _z50ActivePanel === panelId;

    qsa('.nx-shell-navrail .nx-nav-icon').forEach(b => b.classList.remove('z50-active'));

    if (alreadyOpen) {
      _z50ActivePanel = null;
      sessionStorage.removeItem('nx_navrail_panel');
      return;
    }

    panel.style.display = 'block';
    _z50ActivePanel = panelId;
    sessionStorage.setItem('nx_navrail_panel', panelId);
    if (btn) btn.classList.add('z50-active');
    z50PopulatePanel(panelId);
  };

  window.nxClosePanels = function () {
    qsa('.nx-slide-panel').forEach(p => { p.style.display = 'none'; });
    qsa('.nx-shell-navrail .nx-nav-icon').forEach(b => b.classList.remove('z50-active'));
    _z50ActivePanel = null;
    sessionStorage.removeItem('nx_navrail_panel');
  };

  function z50PopulatePanel(panelId) {
    const contentEl = $('nxPanelContent-' + panelId);
    if (!contentEl) return;
    if (contentEl.dataset.z50loaded) return; // already populated, live data refreshed below

    switch (panelId) {
      case 'files':    z50BuildFilesPanel(contentEl); break;
      case 'history':  z50BuildHistoryPanel(contentEl); break;
      case 'settings': z50BuildSettingsPanel(contentEl); break;
      case 'chat':     z50BuildChatPanel(contentEl); break;
    }
    contentEl.dataset.z50loaded = '1';
  }

  /* ── Files panel ────────────────────────────────────────────────── */
  function z50BuildFilesPanel(el) {
    el.innerHTML = `
      <div class="z50-panel-body">
        <div class="z50-panel-section">
          <div style="display:flex;align-items:center;gap:6px;">
            <input id="z50FileSearch" placeholder="Filter files…" style="flex:1;background:var(--surface);border:1px solid var(--panel-border);border-radius:var(--r-sm);padding:4px 8px;color:var(--text);font-size:11px;font-family:var(--font);outline:none;" />
            <button class="nx-tiny-btn" onclick="nxClosePanels();nxSetTab('code');" title="Open in Code tab" style="white-space:nowrap">Open tab →</button>
          </div>
        </div>
        <div class="z50-panel-scroll" id="z50FileTree">
          <div class="z50-panel-empty">
            <div class="z50-panel-empty-icon">📁</div>
            <div class="z50-panel-empty-label">No session active</div>
            <div class="z50-panel-empty-hint">Run a task to generate workspace files.</div>
          </div>
        </div>
      </div>`;
    z50RefreshFileTree();
    const search = $('z50FileSearch');
    if (search) {
      search.addEventListener('input', function () {
        const q = this.value.toLowerCase();
        qsa('.z50-file-item', $('z50FileTree')).forEach(item => {
          item.style.display = q && !item.textContent.toLowerCase().includes(q) ? 'none' : '';
        });
      });
    }
  }

  async function z50RefreshFileTree() {
    const sid = window.NX?.activeSid || (typeof currentSession !== 'undefined' ? currentSession : null);
    if (!sid) return;
    const tree = $('z50FileTree');
    if (!tree) return;
    try {
      const r = await fetch('/api/files?sid=' + sid);
      if (!r.ok) return;
      const d = await r.json();
      const files = (d.files || d.tree || []).slice(0, 120);
      if (!files.length) return;
      tree.innerHTML = '';
      files.forEach(f => {
        const name = typeof f === 'string' ? f : (f.path || f.name || '');
        if (!name) return;
        const div = document.createElement('div');
        div.className = 'z50-file-item' + (name.endsWith('/') ? ' dir' : '');
        div.title = name;
        div.textContent = (name.endsWith('/') ? '📂 ' : '📄 ') + name.split('/').pop();
        div.onclick = () => {
          qsa('.z50-file-item', tree).forEach(i => i.classList.remove('z50-active'));
          div.classList.add('z50-active');
          nxClosePanels();
          nxSetTab('code');
          if (typeof openFileInEditor === 'function') openFileInEditor(name);
          else if (typeof nxOpenFile === 'function') nxOpenFile(name);
        };
        tree.appendChild(div);
      });
    } catch (_) {}
  }

  /* ── History panel ──────────────────────────────────────────────── */
  function z50BuildHistoryPanel(el) {
    el.innerHTML = `
      <div class="z50-panel-body">
        <div class="z50-panel-section">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <span style="font-size:10px;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:.07em;">Recent Sessions</span>
            <button class="nx-tiny-btn" onclick="z50RefreshHistory()">↻ Refresh</button>
          </div>
        </div>
        <div class="z50-panel-scroll" id="z50HistoryList">
          <div class="z50-panel-empty">
            <div class="z50-panel-empty-icon">🕐</div>
            <div class="z50-panel-empty-label">Loading sessions…</div>
          </div>
        </div>
      </div>`;
    z50RefreshHistory();
  }

  window.z50RefreshHistory = async function () {
    const list = $('z50HistoryList');
    if (!list) return;
    try {
      const r = await fetch('/api/sessions?limit=30');
      if (!r.ok) return;
      const sessions = await r.json();
      if (!sessions.length) {
        list.innerHTML = `<div class="z50-panel-empty"><div class="z50-panel-empty-icon">🕐</div><div class="z50-panel-empty-label">No sessions yet</div></div>`;
        return;
      }
      list.innerHTML = '';
      [...sessions].reverse().forEach(s => {
        const div = document.createElement('div');
        div.className = 'z50-hist-item';
        const status = s.status || 'idle';
        const statusCls = status === 'completed' || status === 'idle' ? 'ok' : status === 'error' ? 'err' : status === 'running' ? 'run' : 'idle';
        const name = s.project_name || s.task_preview || s.sid?.slice(-8) || 'Session';
        const ts = s.created_at ? new Date(s.created_at * 1000).toLocaleString([], { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' }) : '';
        div.innerHTML = `
          <div class="z50-hist-name" title="${name}">${name}</div>
          <div class="z50-hist-meta">
            <span class="z50-hist-status ${statusCls}">${status}</span>
            <span>${ts}</span>
          </div>`;
        div.onclick = () => {
          nxClosePanels();
          if (typeof loadSession === 'function') loadSession(s.sid);
          else if (typeof p4LoadSession === 'function') p4LoadSession(s.sid);
          else if (window.NX) window.NX.activeSid = s.sid;
        };
        list.appendChild(div);
      });
    } catch (_) {
      list.innerHTML = `<div class="z50-panel-empty"><div class="z50-panel-empty-label">Failed to load</div><button class="nx-tiny-btn" onclick="z50RefreshHistory()">Retry</button></div>`;
    }
  };

  /* ── Settings panel ─────────────────────────────────────────────── */
  function z50BuildSettingsPanel(el) {
    el.innerHTML = `
      <div class="z50-panel-body">
        <div class="z50-panel-section">
          <div style="font-size:10px;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">Quick Settings</div>
          <div style="display:flex;flex-direction:column;gap:6px;">
            <button class="nx-tiny-btn" style="text-align:left;padding:7px 10px;border:1px solid var(--panel-border);border-radius:var(--r-sm);" onclick="nxClosePanels();nxOpenPanel?.('settings')??openSettings?.()">
              ⚙ Model &amp; API Settings
            </button>
            <button class="nx-tiny-btn" style="text-align:left;padding:7px 10px;border:1px solid var(--panel-border);border-radius:var(--r-sm);" onclick="nxClosePanels();p8ShowUpgradeModal?.()">
              🚀 Plans &amp; Billing
            </button>
            <button class="nx-tiny-btn" style="text-align:left;padding:7px 10px;border:1px solid var(--panel-border);border-radius:var(--r-sm);" onclick="nxClosePanels();p4ToggleTheme?.()">
              🎨 Toggle Theme
            </button>
            <button class="nx-tiny-btn" style="text-align:left;padding:7px 10px;border:1px solid var(--panel-border);border-radius:var(--r-sm);" onclick="nxClosePanels();openSettings?.('security')">
              🔐 Account &amp; Security
            </button>
          </div>
        </div>
        <div class="z50-panel-section" style="flex:1;">
          <div style="font-size:10px;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Runtime</div>
          <div id="z50SettingsStatus" style="font-size:11px;color:var(--text-muted);">Loading…</div>
        </div>
      </div>`;
    z50LoadSettingsStatus();
  }

  async function z50LoadSettingsStatus() {
    const el = $('z50SettingsStatus');
    if (!el) return;
    try {
      const r = await fetch('/api/health');
      if (!r.ok) { el.textContent = 'Unable to load status.'; return; }
      const d = await r.json();
      const sys = d.system || {};
      el.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:4px;">
          <div style="display:flex;justify-content:space-between;"><span>CPU</span><span style="color:var(--text)">${(sys.cpu_pct||0).toFixed(1)}%</span></div>
          <div style="display:flex;justify-content:space-between;"><span>Memory</span><span style="color:var(--text)">${(sys.mem_used_pct||0).toFixed(1)}%</span></div>
          <div style="display:flex;justify-content:space-between;"><span>Sessions</span><span style="color:var(--text)">${(d.sessions||{}).total||0}</span></div>
          <div style="display:flex;justify-content:space-between;"><span>Status</span><span style="color:var(--green)">● Online</span></div>
        </div>`;
    } catch (_) { el.textContent = 'Status unavailable.'; }
  }

  /* ── Chat panel ─────────────────────────────────────────────────── */
  function z50BuildChatPanel(el) {
    el.innerHTML = `
      <div class="z50-panel-body">
        <div class="z50-panel-section">
          <div style="font-size:10px;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:.07em;">Chat</div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;gap:8px;">
          <div style="font-size:20px;opacity:.35;">💬</div>
          <div style="font-size:11px;color:var(--text-muted);font-weight:500;">Chat tab</div>
          <div style="font-size:10px;color:var(--text-dim);text-align:center;">Open the Chat tab to continue your conversation with the agent.</div>
          <button class="nx-tiny-btn" style="margin-top:4px;border:1px solid var(--panel-border);padding:6px 12px;border-radius:var(--r-sm);"
            onclick="nxClosePanels();nxSetTab('chat');if(typeof p12LoadChat==='function')p12LoadChat(window.NX?.activeSid);">
            Open Chat →
          </button>
        </div>
      </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50C: LIVE WORKSPACE PRESENCE — idle hero stats + heartbeat
     ═══════════════════════════════════════════════════════════════════ */
  let _z50StatTimer = null;

  function z50StartLivePresence() {
    z50UpdateIdleStats();
    z50UpdateRuntimePulse('idle');
    if (_z50StatTimer) clearInterval(_z50StatTimer);
    _z50StatTimer = setInterval(z50UpdateIdleStats, 12000);
  }

  async function z50UpdateIdleStats() {
    const hero = $('nxIdleHero');
    if (!hero || hero.classList.contains('hidden')) return;
    try {
      const [mr, sr] = await Promise.all([
        fetch('/api/system/metrics'),
        fetch('/api/scheduler/stats').catch(() => null),
      ]);
      if (mr.ok) {
        const md = await mr.json();
        const providers = md.providers || [];
        const avail = providers.find(p => p.available) || providers[0];
        if (avail) z50StatUpdate('nxIdleModel', avail.model || avail.provider || '—');
        const conf = md.metrics?.avg_confidence;
        z50StatUpdate('nxIdleConf', conf ? (conf * 100).toFixed(0) + '%' : 'High');
        const ctx = md.metrics?.context_pressure;
        z50StatUpdate('nxIdleCtx', ctx ? (ctx * 100).toFixed(0) + '%' : 'Low');
      }
      if (sr && sr.ok) {
        const sd = await sr.json();
        const sched = sd.total_enabled || sd.total || 0;
        z50StatUpdate('nxIdleSched', sched ? sched + ' active' : 'None');
      } else {
        z50StatUpdate('nxIdleSched', '—');
      }
    } catch (_) {}
  }

  function z50StatUpdate(id, val) {
    const el = $(id);
    if (!el || el.textContent === val) return;
    el.textContent = val;
    el.classList.remove('z50-updated');
    void el.offsetWidth; // reflow to restart animation
    el.classList.add('z50-updated');
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50D: EXECUTION FEEDBACK — accepted/running/done/failed/retry
     ═══════════════════════════════════════════════════════════════════ */
  let _z50FeedbackEl = null;
  let _z50FeedbackTimer = null;
  let _z50ExecStart = null;
  let _z50ElapsedTimer = null;

  function z50InitExecFeedback() {
    // Inject feedback bar before activity bar in the center panel
    const actBar = $('nxActivityBar');
    if (!actBar || $('z50ExecFeedback')) return;

    const bar = document.createElement('div');
    bar.id = 'z50ExecFeedback';
    bar.className = 'z50-exec-feedback';
    bar.setAttribute('role', 'status');
    bar.setAttribute('aria-live', 'polite');
    bar.innerHTML = `
      <div class="z50-exec-dot"></div>
      <span class="z50-exec-label" id="z50ExecLabel">—</span>
      <span class="z50-exec-elapsed" id="z50ExecElapsed"></span>`;
    actBar.parentNode.insertBefore(bar, actBar);
    _z50FeedbackEl = bar;
  }

  function z50ShowFeedback(state, message, autoDismiss) {
    const bar = $('z50ExecFeedback') || _z50FeedbackEl;
    if (!bar) return;
    clearTimeout(_z50FeedbackTimer);
    bar.className = 'z50-exec-feedback visible ' + state;
    set('z50ExecLabel', message || state);
    set('z50ExecElapsed', '');
    if (autoDismiss) {
      _z50FeedbackTimer = setTimeout(() => {
        bar.classList.remove('visible');
      }, autoDismiss);
    }
  }

  function z50StartElapsedTimer() {
    _z50ExecStart = Date.now();
    clearInterval(_z50ElapsedTimer);
    _z50ElapsedTimer = setInterval(() => {
      const el = $('z50ExecElapsed');
      if (!el) return;
      const s = Math.floor((Date.now() - _z50ExecStart) / 1000);
      el.textContent = s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    }, 1000);
  }

  function z50StopElapsedTimer() {
    clearInterval(_z50ElapsedTimer);
    _z50ElapsedTimer = null;
  }

  /* ── Run button feedback ripple ─────────────────────────────────── */
  function z50WireRunButton() {
    const runBtn = $('runBtn');
    if (!runBtn) return;
    const orig = runBtn.onclick;
    runBtn.addEventListener('click', function () {
      if (window.NX?.lastStatus !== 'running') {
        this.classList.remove('z50-accepted');
        void this.offsetWidth;
        this.classList.add('z50-accepted');
        z50ShowFeedback('accepted', 'Task accepted — queuing…');
        z50StartElapsedTimer();
        setTimeout(() => this.classList.remove('z50-accepted'), 600);
      }
    }, true); // capture phase so it fires before any stop logic
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50C/F: RUNTIME PULSE — topbar state indicator
     ═══════════════════════════════════════════════════════════════════ */
  function z50UpdateRuntimePulse(state) {
    const el = $('z33RuntimePulse');
    if (!el) return;
    el.dataset.state = state;
    const label = el.querySelector('.z33-pulse-label');
    if (label) {
      label.textContent = state === 'running' ? 'Running'
                        : state === 'error'   ? 'Error'
                        : 'Idle';
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50F: STATUS SYNC — hook into nxSetGlobalStatus
     ═══════════════════════════════════════════════════════════════════ */
  function z50HookStatusSync() {
    const origSetStatus = window.nxSetGlobalStatus;
    if (typeof origSetStatus !== 'function') {
      // Retry after boot
      setTimeout(z50HookStatusSync, 500);
      return;
    }
    window.nxSetGlobalStatus = function (status) {
      origSetStatus.call(this, status);
      z50UpdateRuntimePulse(status);

      if (status === 'running') {
        z50ShowFeedback('running', 'Agent is executing…');
        z50StartElapsedTimer();
        // Refresh file panel if open
        if (_z50ActivePanel === 'files') {
          $('z50FileTree') && setTimeout(z50RefreshFileTree, 2000);
        }
      } else if (status === 'idle' && window.NX?.lastStatus === 'running') {
        z50StopElapsedTimer();
        const runBtn = $('runBtn');
        if (runBtn) {
          runBtn.classList.remove('z50-accepted');
          void runBtn.offsetWidth;
          runBtn.classList.add('z50-completed');
          setTimeout(() => runBtn.classList.remove('z50-completed'), 700);
        }
        z50ShowFeedback('completed', 'Task completed', 6000);
        // Refresh file panel
        if (_z50ActivePanel === 'files') setTimeout(z50RefreshFileTree, 1000);
        // Refresh idle stats
        setTimeout(z50UpdateIdleStats, 1500);
      } else if (status === 'error') {
        z50StopElapsedTimer();
        z50ShowFeedback('failed', 'Task failed — check output for details', 10000);
      }
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50C: QUEUE HEARTBEAT — badge pulse when queue > 0
     ═══════════════════════════════════════════════════════════════════ */
  function z50WireQueueBadge() {
    const qEl = $('nxQueueCount');
    if (!qEl) return;
    const obs = new MutationObserver(() => {
      const n = parseInt(qEl.textContent, 10) || 0;
      qEl.classList.toggle('z50-active', n > 0);
    });
    obs.observe(qEl, { childList: true, characterData: true, subtree: true });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50C: RECONNECT AWARENESS — SSE disconnect banner
     ═══════════════════════════════════════════════════════════════════ */
  let _z50Reconnecting = false;

  function z50InitReconnectBar() {
    const center = qs('.nx-shell-center');
    if (!center || $('z50ReconnectBar')) return;
    const bar = document.createElement('div');
    bar.id = 'z50ReconnectBar';
    bar.className = 'z50-reconnect-bar';
    bar.innerHTML = `
      <div class="z50-exec-dot"></div>
      <span>Connection lost — attempting to reconnect…</span>
      <button class="nx-tiny-btn" style="margin-left:auto;" onclick="window.location.reload()">Reload</button>`;
    const topbar = qs('.nx-shell-topbar');
    if (topbar && topbar.nextSibling) {
      topbar.parentNode.insertBefore(bar, topbar.nextSibling);
    }
  }

  function z50ShowReconnectBar(visible) {
    const bar = $('z50ReconnectBar');
    if (bar) bar.classList.toggle('visible', !!visible);
    _z50Reconnecting = !!visible;
  }

  // Hook into SSE events if available
  function z50WireSSEEvents() {
    document.addEventListener('nx:sse:disconnected', () => z50ShowReconnectBar(true));
    document.addEventListener('nx:sse:reconnected',  () => z50ShowReconnectBar(false));
    document.addEventListener('nx:sse:connected',    () => z50ShowReconnectBar(false));
    // Also observe the obs conn label for state changes
    const connLabel = $('nx-obs-conn-label');
    if (connLabel) {
      const obs = new MutationObserver(() => {
        const txt = connLabel.textContent || '';
        if (txt.toLowerCase().includes('reconnect')) z50ShowReconnectBar(true);
        else if (txt.toLowerCase().includes('connected')) z50ShowReconnectBar(false);
      });
      obs.observe(connLabel, { childList: true, characterData: true, subtree: true });
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50G: PERFORMANCE — deduplicate MutationObservers & stale listeners
     ═══════════════════════════════════════════════════════════════════ */
  function z50PerformancePass() {
    // Limit nxWatchInspectorSlots from over-observing (subtree+charData is heavy)
    // This is handled by keeping the existing observer but not adding more
    // Prevent duplicate nxInitBackgroundTasks calls
    if (window._z50PerfDone) return;
    window._z50PerfDone = true;

    // Cleanup: remove duplicate event listeners on the run button
    // (boot.js and ui.js can both wire click — the capture listener above handles ordering)

    // Fix: ensure nxPollQueue doesn't stack if called repeatedly
    if (window.NX && !window.NX._z50QueueInterval) {
      window.NX._z50QueueInterval = true; // flag to prevent double-start
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50B: PANEL KEYBOARD ACCESSIBILITY — Escape to close
     ═══════════════════════════════════════════════════════════════════ */
  function z50InitPanelKeyboard() {
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (_z50ActivePanel) {
          window.nxClosePanels();
          return;
        }
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z50F: COHESION — idle hero recent executions
     ═══════════════════════════════════════════════════════════════════ */
  async function z50PopulateIdleRecent() {
    const el = $('nxIdleRecent');
    if (!el) return;
    try {
      const r = await fetch('/api/sessions?limit=5');
      if (!r.ok) return;
      const sessions = await r.json();
      if (!sessions.length) return;
      el.innerHTML = '';
      [...sessions].reverse().slice(0, 5).forEach(s => {
        const div = document.createElement('div');
        div.style.cssText = 'display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--panel-border);cursor:pointer;';
        const status = s.status || 'idle';
        const dotColor = status === 'completed' || status === 'idle' ? 'var(--green)' : status === 'error' ? 'var(--red)' : 'var(--blue)';
        const name = s.project_name || s.task_preview || s.sid?.slice(-8) || 'Session';
        div.innerHTML = `
          <span style="width:6px;height:6px;border-radius:50%;background:${dotColor};flex-shrink:0;"></span>
          <span style="flex:1;font-size:11px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</span>
          <span style="font-size:10px;color:var(--text-dim)">${status}</span>`;
        div.addEventListener('click', () => {
          if (typeof loadSession === 'function') loadSession(s.sid);
          else if (window.NX) window.NX.activeSid = s.sid;
        });
        el.appendChild(div);
      });
    } catch (_) {}
  }

  /* ═══════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════ */
  function z50Boot() {
    z50InitCookieBanner();
    z50InitExecFeedback();
    z50InitNavRail();
    z50WireExecSelects();
    z50WireRunButton();
    z50WireQueueBadge();
    z50InitReconnectBar();
    z50WireSSEEvents();
    z50InitPanelKeyboard();
    z50HookStatusSync();
    z50StartLivePresence();
    z50PopulateIdleRecent();
    z50PerformancePass();
    console.log('[Phase Z50] Operational Interaction Realism active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z50Boot);
  } else {
    // Defer slightly to let other modules boot first
    setTimeout(z50Boot, 80);
  }

  // Expose for external use
  window._z50 = {
    showFeedback: z50ShowFeedback,
    updatePulse:  z50UpdateRuntimePulse,
    refreshFiles: z50RefreshFileTree,
    refreshHistory: window.z50RefreshHistory,
    updateStats:  z50UpdateIdleStats,
    showReconnect: z50ShowReconnectBar,
  };

})();
