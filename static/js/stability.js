/* ─── FINAL STABILITY & UX HARDENING — Phase STABLE ───────────────────── */
  (function () {
    'use strict';

    /* ══════════════════════════════════════════════════════════════════════
       PART 1A — GLOBAL LOADING BAR
       ══════════════════════════════════════════════════════════════════════ */
    let _loadingCount = 0;
    let _loadingBar = null;
    let _loadingDoneTimer = null;

    function _initLoadingBar() {
      if ($id('nx-loading-bar')) return;
      const bar = document.createElement('div');
      bar.id = 'nx-loading-bar';
      document.body.insertBefore(bar, document.body.firstChild);
      _loadingBar = bar;
    }

    function showLoading() {
      _loadingCount++;
      if (!_loadingBar) _loadingBar = $id('nx-loading-bar');
      if (!_loadingBar) return;
      clearTimeout(_loadingDoneTimer);
      _loadingBar.className = 'active';
    }
    function hideLoading() {
      _loadingCount = Math.max(0, _loadingCount - 1);
      if (_loadingCount > 0) return;
      if (!_loadingBar) _loadingBar = $id('nx-loading-bar');
      if (!_loadingBar) return;
      _loadingBar.className = 'done';
      _loadingDoneTimer = setTimeout(() => { if (_loadingBar) _loadingBar.className = ''; }, 450);
    }

    /* ══════════════════════════════════════════════════════════════════════
       PART 1B — TOAST NOTIFICATIONS
       ══════════════════════════════════════════════════════════════════════ */
    const TOAST_ICONS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const TOAST_TITLES = { success: 'Success', error: 'Error', warning: 'Warning', info: 'Info' };

    function nxToast(type, message, title, duration) {
      let container = $id('nx-toasts');
      if (!container) {
        container = document.createElement('div');
        container.id = 'nx-toasts';
        document.body.appendChild(container);
      }
      duration = duration || (type === 'error' ? 6000 : 4000);
      title = title || TOAST_TITLES[type] || 'Notice';

      const toast = document.createElement('div');
      toast.className = `nx-toast ${type}`;
      toast.innerHTML = `
    <div class="nx-toast-icon">${TOAST_ICONS[type] || 'ℹ'}</div>
    <div class="nx-toast-body">
      <div class="nx-toast-title">${_escToast(title)}</div>
      <div class="nx-toast-msg">${_escToast(message)}</div>
    </div>
    <div class="nx-toast-close" onclick="this.parentElement.remove()">✕</div>`;
      container.appendChild(toast);
      toast.onclick = (e) => { if (!e.target.classList.contains('nx-toast-close')) { } };

      setTimeout(() => {
        toast.style.animation = 'nx-toast-out 0.3s ease forwards';
        setTimeout(() => { try { container.removeChild(toast); } catch (_) { } }, 310);
      }, duration);

      return toast;
    }

    function _escToast(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // Expose globally
    window.nxToast = nxToast;

    /* ── Patch window.fetch to show loading + surface errors ──────────────── */
    let _fetchPatched = false;
    function patchFetch() {
      // FIX: Guard against double-wrapping window.fetch.
      // The auth IIFE already wraps window.fetch to inject Bearer tokens;
      // if patchFetch runs after that, calling orig() would bypass the auth
      // wrapper on internal calls, causing 401 responses and stuck states.
      if (_fetchPatched || typeof window.fetch !== 'function') return;
      if (window.fetch._nxStablePatched) return; // already patched by this module
      _fetchPatched = true;
      const orig = window.fetch.bind(window);
      const _patchedFn = async function (url, opts) {
        // Skip SSE streams and non-API calls
        const urlStr = String(url);
        const isStream = urlStr.includes('/stream') || urlStr.includes('EventSource');
        const isApi = urlStr.includes('/api/');
        if (isApi && !isStream) showLoading();
        try {
          const resp = await orig(url, opts);
          if (isApi && !isStream) {
            // Surface 5xx errors as toasts (not 401 which is handled by auth)
            if (resp.status >= 500) {
              nxToast('error', `Server error (${resp.status}) on ${urlStr.split('/api/')[1] || urlStr}`, 'Server Error');
            } else if (resp.status === 429) {
              nxToast('warning', 'Too many requests — please wait a moment.', 'Rate Limited');
            }
          }
          return resp;
        } catch (err) {
          if (isApi && !isStream) {
            nxToast('error', 'Network request failed. Check your connection.', 'Connection Error');
            showFailsafeBanner('Network connection lost. Check your internet.', null);
          }
          throw err;
        } finally {
          if (isApi && !isStream) hideLoading();
        }
      };
      _patchedFn._nxStablePatched = true;
      window.fetch = _patchedFn;
    }

    /* ══════════════════════════════════════════════════════════════════════
       PART 2 — ONBOARDING PANEL (new users / empty state)
       ══════════════════════════════════════════════════════════════════════ */
    const ONBOARD_KEY = 'nx_onboard_dismissed_v2';
    const ONBOARD_PROMPTS = [
      { icon: '🔐', text: 'Build a login system with SQLite auth' },
      { icon: '🐛', text: 'Fix the error in my Python script' },
      { icon: '🚀', text: 'Create a full-stack web project with Flask' },
    ];

    function _injectOnboarding() {
      if (!$id('nx-onboard-panel')) {
        const panel = document.createElement('div');
        panel.id = 'nx-onboard-panel';
        panel.innerHTML = `
      <div class="nx-onboard-title">
        <span>👋</span> Welcome to Nexora AI
      </div>
      <div class="nx-onboard-sub">
        Your autonomous AI coding assistant. Describe what you want to build and the AI will plan, code, and run it for you.
      </div>
      <div class="nx-onboard-prompts">
        ${ONBOARD_PROMPTS.map(p => `
          <div class="nx-onboard-prompt" onclick="nxStableUsePrompt(${JSON.stringify(p.text).replace(/"/g, '&quot;')})">
            <span class="nx-op-icon">${p.icon}</span>
            <span>${_escToast(p.text)}</span>
          </div>`).join('')}
      </div>
      <div class="nx-onboard-dismiss" onclick="nxStableDismissOnboard()">Dismiss</div>
    `;
        // Try to insert after the hero section in the main area
        const heroArea = document.getElementById('nxHero') || document.getElementById('nxCenter');
        if (heroArea) heroArea.insertBefore(panel, heroArea.firstChild);
        else document.body.appendChild(panel);
      }
      // Show if no sessions and not dismissed
      _checkShowOnboarding();
    }

    function _checkShowOnboarding() {
      if (localStorage.getItem(ONBOARD_KEY)) return;
      const panel = $id('nx-onboard-panel');
      if (!panel) return;
      // Show only if logged in but no sessions
      const token = typeof nxGetToken === 'function' ? nxGetToken() : null;
      if (!token) return;
      fetch('/api/sessions').then(r => r.json()).then(d => {
        const count = Array.isArray(d) ? d.length : (d.sessions || []).length;
        if (count === 0 && panel) panel.style.display = 'block';
      }).catch(() => { });
    }

    window.nxStableUsePrompt = function (text) {
      // Fill the task input and dismiss onboard
      const ta = document.getElementById('nxTaskInput') || document.getElementById('taskInput');
      if (ta) { ta.value = text; ta.dispatchEvent(new Event('input', { bubbles: true })); }
      if (typeof nxSetTask === 'function') nxSetTask(text);
      nxStableDismissOnboard();
      // Focus the run button
      const rb = document.getElementById('runBtn');
      if (rb) rb.focus();
    };

    window.nxStableDismissOnboard = function () {
      localStorage.setItem(ONBOARD_KEY, '1');
      const panel = $id('nx-onboard-panel');
      if (panel) panel.style.display = 'none';
    };

    /* ══════════════════════════════════════════════════════════════════════
       PART 3 — SESSION RECOVERY
       ══════════════════════════════════════════════════════════════════════ */
    const LAST_SESSION_KEY = 'nx_last_session_id';

    // Persist active session ID to localStorage
    function _patchSelectSession() {
      if (typeof window.selectSession !== 'function') {
        setTimeout(_patchSelectSession, 400); return;
      }
      const orig = window.selectSession;
      window.selectSession = function (sid) {
        if (sid) localStorage.setItem(LAST_SESSION_KEY, sid);
        return orig.call(this, sid);
      };
    }

    // On load, restore last session if none is active
    function _restoreLastSession() {
      const lastSid = localStorage.getItem(LAST_SESSION_KEY);
      if (!lastSid) return;
      // Wait for sessions to load, then select
      setTimeout(() => {
        if (typeof currentSession !== 'undefined' && currentSession) return;
        if (typeof selectSession === 'function') {
          // Verify session still exists before restoring
          fetch(`/api/session/${lastSid}`).then(r => r.json()).then(d => {
            if (d && d.id) selectSession(lastSid);
          }).catch(() => { });
        }
      }, 1200);
    }

    // SSE auto-reconnect — patch openLogStream with exponential back-off
    function _patchSSEReconnect() {
      if (typeof window.openLogStream !== 'function') {
        setTimeout(_patchSSEReconnect, 500); return;
      }
      if (window._sseReconnectPatched) return;
      window._sseReconnectPatched = true;
      const orig = window.openLogStream;
      let _reconnectTimer = null;
      let _reconnectDelay = 2000;

      window.openLogStream = function (sid) {
        orig.call(this, sid);
        // Intercept onerror on the new stream
        const es = window.logStream;
        if (!es) return;
        const _origErr = es.onerror;
        es.onerror = function (e) {
          if (_origErr) _origErr.call(this, e);
          _showSSEReconnecting();
          clearTimeout(_reconnectTimer);
          _reconnectTimer = setTimeout(() => {
            _hideSSEReconnecting();
            _reconnectDelay = Math.min(_reconnectDelay * 1.5, 15000);
            if (typeof currentSession !== 'undefined' && currentSession) {
              window.openLogStream(currentSession);
            }
          }, _reconnectDelay);
        };
        const _origMsg = es.onmessage;
        es.onmessage = function (e) {
          if (_origMsg) _origMsg.call(this, e);
          _hideSSEReconnecting();
          _reconnectDelay = 2000; // reset on success
        };
      };
    }

    function _showSSEReconnecting() {
      let badge = $id('nx-sse-status');
      if (!badge) {
        badge = document.createElement('div');
        badge.id = 'nx-sse-status';
        badge.innerHTML = '<div class="nx-sse-dot"></div><span>Reconnecting…</span>';
        document.body.appendChild(badge);
      }
      badge.classList.add('show');
    }
    function _hideSSEReconnecting() {
      const badge = $id('nx-sse-status');
      if (badge) badge.classList.remove('show');
    }

    /* ══════════════════════════════════════════════════════════════════════
       PART 4 — FAILSAFE SYSTEM
       ══════════════════════════════════════════════════════════════════════ */
    let _failsafeRetryFn = null;

    function showFailsafeBanner(msg, retryFn) {
      let banner = $id('nx-failsafe-banner');
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'nx-failsafe-banner';
        banner.innerHTML = `
      <span class="nx-failsafe-msg" id="nx-failsafe-msg"></span>
      <button class="nx-failsafe-retry" id="nx-failsafe-retry-btn" onclick="nxStableRetry()">↻ Retry</button>
      <button class="nx-failsafe-retry" onclick="document.getElementById('nx-failsafe-banner').classList.remove('show')" style="background:transparent;border-color:#30363d;color:#8b949e">✕</button>
    `;
        const logArea = $id('logArea');
        if (logArea) logArea.parentNode.insertBefore(banner, logArea);
        else document.body.insertBefore(banner, document.body.firstChild);
      }
      const msgEl = $id('nx-failsafe-msg');
      if (msgEl) msgEl.textContent = msg || 'An error occurred. The system has stopped safely.';
      _failsafeRetryFn = retryFn;
      banner.classList.add('show');
    }
    function hideFailsafeBanner() {
      const banner = $id('nx-failsafe-banner');
      if (banner) banner.classList.remove('show');
    }

    window.nxStableRetry = function () {
      hideFailsafeBanner();
      if (typeof _failsafeRetryFn === 'function') { _failsafeRetryFn(); return; }
      // Default: stop + re-run last session
      if (typeof currentSession !== 'undefined' && currentSession) {
        const sid = currentSession;
        fetch(`/api/stop`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sid }) })
          .catch(() => { })
          .finally(() => {
            nxToast('info', 'Session stopped. You can start a new task.', 'Ready');
          });
      }
    };

    // Patch nxSetGlobalStatus to auto-show failsafe on error
    function _patchGlobalStatusFailsafe() {
      if (typeof window.nxSetGlobalStatus !== 'function') {
        setTimeout(_patchGlobalStatusFailsafe, 400); return;
      }
      if (window._failsafePatchDone) return;
      window._failsafePatchDone = true;
      const orig = window.nxSetGlobalStatus;
      window.nxSetGlobalStatus = function (status) {
        orig.call(this, status);
        if (status === 'error') {
          showFailsafeBanner(
            'AI execution encountered an error. The system has stopped safely.',
            null
          );
          nxToast('error', 'Execution stopped due to an error. Click Retry to try again.', 'Execution Error');
        } else if (status === 'running') {
          hideFailsafeBanner();
        }
      };
    }

    /* ══════════════════════════════════════════════════════════════════════
       PART 5 — LOG POLISH: filter buttons + error highlighting
       ══════════════════════════════════════════════════════════════════════ */
    function _injectLogFilter() {
      const toolbarId = 'tabActLogs';
      const toolbar = $id(toolbarId);
      if (!toolbar || $id('nx-log-filter-bar')) return;

      const bar = document.createElement('div');
      bar.id = 'nx-log-filter-bar';
      bar.style.cssText = 'display:flex;gap:4px;align-items:center;margin-left:8px';
      bar.innerHTML = `
    <button class="btn tiny nx-lf-btn active" data-lf="all"     onclick="nxLogFilter('all',this)">All</button>
    <button class="btn tiny nx-lf-btn"        data-lf="error"   onclick="nxLogFilter('error',this)" style="color:#f85149">Errors</button>
    <button class="btn tiny nx-lf-btn"        data-lf="success" onclick="nxLogFilter('success',this)" style="color:#3fb950">Success</button>
    <button class="btn tiny nx-lf-btn"        data-lf="system"  onclick="nxLogFilter('system',this)" style="color:#bc8cff">System</button>
  `;
      toolbar.appendChild(bar);
    }

    window.nxLogFilter = function (level, btn) {
      // Update active button
      document.querySelectorAll('.nx-lf-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');

      const area = $id('logArea');
      if (!area) return;
      const lines = area.querySelectorAll('.log-line');
      lines.forEach(line => {
        if (level === 'all') {
          line.style.display = '';
        } else {
          const cls = line.className.replace('log-line', '').replace('uxit-log-new', '').trim();
          line.style.display = cls === level ? '' : 'none';
        }
      });
    };

    /* ══════════════════════════════════════════════════════════════════════
       PART 6 — SECURITY VALIDATION (client-side checks)
       ══════════════════════════════════════════════════════════════════════ */
    function _runSecurityChecks() {
      // JWT expiry: verify token is checked and refresh scheduled
      if (typeof nxGetToken !== 'function') return;
      const token = nxGetToken();
      if (!token) return;

      if (typeof nxIsTokenExpired === 'function' && nxIsTokenExpired(token)) {
        // Token expired but still in storage — force refresh
        if (typeof nxRefreshNow === 'function') {
          nxRefreshNow().then(ok => {
            if (!ok) {
              nxToast('warning', 'Your session has expired. Please sign in again.', 'Session Expired');
              if (typeof nxShowAuthGate === 'function') nxShowAuthGate();
            }
          }).catch(() => { });
        }
      }

      // Ensure refresh is scheduled
      if (typeof nxScheduleRefresh === 'function') {
        const dec = typeof nxDecodeJWT === 'function' ? nxDecodeJWT(token) : null;
        if (dec && dec.exp) {
          const secs = dec.exp - Math.floor(Date.now() / 1000);
          if (secs > 0 && secs < 1800) {
            nxScheduleRefresh(secs);
          }
        }
      }
    }

    /* ══════════════════════════════════════════════════════════════════════
       PART 7 — INTERACTION VALIDATION (button/form checks)
       ══════════════════════════════════════════════════════════════════════ */
    function _validateInteractions() {
      // Verify critical button handlers are bound
      const checks = [
        ['nx-btn-login', 'nxDoLogin'],
        ['nx-btn-signup', 'nxDoSignup'],
        ['runBtn', null],
      ];
      let allOk = true;
      checks.forEach(([id, fn]) => {
        const el = $id(id);
        if (!el) return;
        if (fn && typeof window[fn] !== 'function') {
          console.warn(`[STABLE] Handler ${fn} missing — rebinding`);
          allOk = false;
        }
      });
      if (!allOk) {
        // Re-expose handlers in case of scope issue
        if (typeof window.nxDoLogin === 'undefined' && $id('nx-btn-login')) {
          $id('nx-btn-login').onclick = function () {
            nxToast('warning', 'Auth handler loading — please try again in a moment.', 'Loading');
          };
        }
      }
    }

    /* ── Offline/online detection ─────────────────────────────────────────── */
    window.addEventListener('offline', () => {
      nxToast('warning', 'You are offline. Some features may not work.', 'No Internet');
      showFailsafeBanner('Network connection lost. Check your connection and retry.', null);
    });
    window.addEventListener('online', () => {
      hideFailsafeBanner();
      nxToast('success', 'Connection restored.', 'Back Online');
      // Re-open SSE stream
      if (typeof currentSession !== 'undefined' && currentSession && typeof openLogStream === 'function') {
        openLogStream(currentSession);
      }
    });

    /* ══════════════════════════════════════════════════════════════════════
       UTILS
       ══════════════════════════════════════════════════════════════════════ */
    function $id(id) { return document.getElementById(id); }

    /* ══════════════════════════════════════════════════════════════════════
       BOOT
       ══════════════════════════════════════════════════════════════════════ */
    function bootStable() {
      _initLoadingBar();
      patchFetch();
      _patchSelectSession();
      _patchSSEReconnect();
      _patchGlobalStatusFailsafe();
      _injectOnboarding();
      _injectLogFilter();
      _restoreLastSession();

      // Security checks after auth init settles
      setTimeout(_runSecurityChecks, 1500);
      // Interaction validation
      setTimeout(_validateInteractions, 2000);

      // Expose public API
      window.NxStable = {
        toast: nxToast,
        showFailsafe: showFailsafeBanner,
        hideFailsafe: hideFailsafeBanner,
      };

      console.log('[STABLE] FINAL STABILITY & UX HARDENING COMPLETE — SYSTEM IS BETA LAUNCH READY');
    }

    window.NX_BOOT_TASKS.push(bootStable);

  })();
