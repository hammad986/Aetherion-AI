/* ═══════════════════════════════════════════════════════════════════
     NEXORA — Enterprise Authentication Engine
     Handles: token storage, auto-refresh, login/signup, OAuth callback,
              session management, user badge
     ═══════════════════════════════════════════════════════════════════ */
  (function () {
    'use strict';

    const NX_TOKEN_KEY = 'nx_access_token';
    const NX_USER_KEY = 'nx_user_info';

    // ── Token storage helpers ──────────────────────────────────────────
    // Refresh token is now HttpOnly cookie — never touches localStorage
    function nxGetToken() { return localStorage.getItem(NX_TOKEN_KEY) || ''; }
    function nxGetUser() { try { return JSON.parse(localStorage.getItem(NX_USER_KEY) || '{}'); } catch { return {}; } }

    function nxStoreTokens(data, userName) {
      if (data.access_token) localStorage.setItem(NX_TOKEN_KEY, data.access_token);
      // refresh_token intentionally NOT stored in localStorage — handled by HttpOnly cookie
      if (userName) localStorage.setItem(NX_USER_KEY, JSON.stringify({ name: userName }));
    }

    function nxClearTokens() {
      localStorage.removeItem(NX_TOKEN_KEY);
      localStorage.removeItem(NX_USER_KEY);
    }

    window.nxLogout = function() {
      nxClearTokens();
      // Use a POST to the logout API if it exists, otherwise just reload
      _origFetch('/api/auth/logout', { method: 'POST' }).catch(() => {}).finally(() => {
        location.reload();
      });
    };

    // ── JWT decode (client-side, no verify — server validates) ─────────
    function nxDecodeJWT(token) {
      try {
        const parts = token.split('.');
        if (parts.length !== 3) return null;
        const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
        return payload;
      } catch { return null; }
    }

    function nxIsTokenExpired(token) {
      const p = nxDecodeJWT(token);
      if (!p || !p.exp) return true;
      return Date.now() / 1000 > p.exp - 30; // 30s buffer
    }

    // ── Auto-refresh loop ──────────────────────────────────────────────
    let _nxRefreshTimer = null;
    let _refreshPromise = null;

    function nxScheduleRefresh(expiresIn) {
      clearTimeout(_nxRefreshTimer);
      const ms = Math.max(((expiresIn || 900) - 60) * 1000, 30000);
      _nxRefreshTimer = setTimeout(nxRefreshNow, ms);
    }

    async function nxRefreshNow() {
      if (_refreshPromise) return _refreshPromise;
      
      _refreshPromise = (async () => {
        try {
          const r = await _origFetch('/api/auth/refresh', {
            method: 'POST',
            credentials: 'include',
          });
          const d = await r.json();
          if (d.ok) {
            nxStoreTokens(d);
            nxScheduleRefresh(d.expires_in || 900);
            return true;
          } else {
            nxClearTokens();
            // If we are on a protected page/state, show gate
            if (typeof nxShowAuthGate === 'function') nxShowAuthGate();
            return false;
          }
        } catch {
          nxScheduleRefresh(60); // retry in 1 min on network error
          return false;
        } finally {
          _refreshPromise = null;
        }
      })();

      return _refreshPromise;
    }

    // ── Fetch wrapper that auto-refreshes on 401 ───────────────────────
    const _origFetch = window.fetch.bind(window);
    window.fetch = async function (url, opts = {}) {
      const token = nxGetToken();
      if (token && typeof url === 'string' && url.startsWith('/api/')) {
        opts = { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } };
      }
      let resp = await _origFetch(url, opts);
      if (resp.status === 401 && typeof url === 'string' && url.startsWith('/api/')) {
        const body = await resp.clone().json().catch(() => ({}));
        if (body.code === 'TOKEN_EXPIRED') {
          const ok = await nxRefreshNow();
          const newToken = nxGetToken();
          if (newToken) {
            opts.headers = { ...(opts.headers || {}), Authorization: `Bearer ${newToken}` };
            return _origFetch(url, opts);
          }
        }
      }
      return resp;
    };

    // ── Account Recovery: Forgot Password ─────────────────────────────
    window.nxShowForgotPw = function (show = true) {
      const loginForm = document.getElementById('nx-form-login');
      const forgotForm = document.getElementById('nx-form-forgot');
      const tabs = document.querySelector('.nx-auth-tabs');
      const oauthDiv = document.querySelector('.nx-auth-divider');
      const oauthBtns = document.querySelector('.nx-auth-oauth');
      const errEl = document.getElementById('nx-auth-err');
      const okEl = document.getElementById('nx-auth-ok');
      if (!loginForm || !forgotForm) return;

      if (show) {
        loginForm.classList.add('nx-hidden');
        forgotForm.classList.remove('nx-hidden');
        if (tabs) tabs.classList.add('nx-hidden');
        if (oauthDiv) oauthDiv.classList.add('nx-hidden');
        if (oauthBtns) oauthBtns.classList.add('nx-hidden');
        if (errEl) errEl.classList.remove('show');
        if (okEl) okEl.classList.remove('show');
        setTimeout(() => {
          const em = document.getElementById('nx-forgot-email');
          if (em) em.focus();
        }, 50);
      } else {
        forgotForm.classList.add('nx-hidden');
        loginForm.classList.remove('nx-hidden');
        if (tabs) tabs.classList.remove('nx-hidden');
        if (oauthDiv) oauthDiv.classList.remove('nx-hidden');
        if (oauthBtns) oauthBtns.classList.remove('nx-hidden');
        if (errEl) errEl.classList.remove('show');
        if (okEl) okEl.classList.remove('show');
      }
    };

    window.nxDoForgotPw = async function () {
      const email = (document.getElementById('nx-forgot-email')?.value || '').trim();
      const btn = document.getElementById('nx-btn-forgot');
      const errEl = document.getElementById('nx-auth-err');
      const okEl = document.getElementById('nx-auth-ok');

      if (!email || !email.includes('@')) {
        if (errEl) { errEl.textContent = 'Enter a valid email address.'; errEl.classList.add('show'); }
        return;
      }
      if (errEl) errEl.classList.remove('show');
      if (okEl) okEl.classList.remove('show');

      if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
      try {
        const r = await _origFetch('/api/auth/forgot-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok && d.error) {
          if (errEl) { errEl.textContent = d.error; errEl.classList.add('show'); }
          return;
        }
        // Always show success message — prevents enumeration
        if (okEl) {
          okEl.textContent = '✓ If that email is registered, a reset link has been sent. Check your inbox (and spam folder).';
          okEl.classList.add('show');
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Send Reset Link'; }
        const inp = document.getElementById('nx-forgot-email');
        if (inp) inp.value = '';
      } catch (e) {
        if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.classList.add('show'); }
        if (btn) { btn.disabled = false; btn.textContent = 'Send Reset Link'; }
      }
    };

    // ── Cross-Tab Synchronization ───────────────────────────────────────
    window.addEventListener('storage', (e) => {
      if (e.key === NX_TOKEN_KEY) {
        if (!e.newValue) {
          console.log('[Auth] Token cleared remotely, logging out...');
          nxClearTokens();
          location.reload();
        } else {
          console.log('[Auth] Token updated remotely, syncing...');
          nxRenderUserBadge();
          if (typeof nxHideAuthGate === 'function') nxHideAuthGate();
        }
      }
    });

    // ── Email Verification ──────────────────────────────────────────────
    window.nxCheckVerification = async function () {
      const token = localStorage.getItem('nx_access_token');
      if (!token) return;
      try {
        const r = await fetch('/api/auth/verification-status', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const d = await r.json();
        if (d.ok && !d.verified) {
          const banner = document.getElementById('nx-verify-banner');
          if (banner) banner.classList.add('show');
        }
      } catch (e) { }
    };

    window.nxSendVerificationEmail = async function () {
      const btn = document.getElementById('nx-verify-send-btn');
      const msgEl = document.getElementById('nx-verify-banner-msg');
      const token = localStorage.getItem('nx_access_token');
      if (!token || !btn) return;

      btn.disabled = true;
      btn.textContent = 'Sending…';
      try {
        const r = await fetch('/api/auth/send-verification', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }
        });
        const d = await r.json();
        if (d.ok) {
          if (msgEl) msgEl.textContent = d.already_verified
            ? 'Your email is already verified!'
            : 'Verification email sent! Check your inbox.';
          btn.textContent = 'Sent ✓';
          if (d.already_verified) {
            setTimeout(() => {
              const banner = document.getElementById('nx-verify-banner');
              if (banner) banner.classList.remove('show');
            }, 3000);
          }
        } else {
          if (msgEl) msgEl.textContent = d.error || 'Failed to send. Try again.';
          btn.disabled = false;
          btn.textContent = 'Resend';
        }
      } catch (e) {
        if (msgEl) msgEl.textContent = 'Network error. Try again.';
        btn.disabled = false;
        btn.textContent = 'Retry';
      }
    };

    // Handle URL params from OAuth callbacks / verification redirects
    (function nxHandleUrlParams() {
      const params = new URLSearchParams(window.location.search);

      // Email verified successfully
      if (params.get('verified') === '1') {
        // Show a toast/notification
        setTimeout(() => {
          const banner = document.getElementById('nx-verify-banner');
          if (banner) banner.classList.remove('show');
          // Show toast
          const toast = document.createElement('div');
          toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#161b22;border:1px solid #3fb950;border-radius:10px;padding:12px 20px;color:#3fb950;font-size:0.85rem;z-index:99999;font-family:Inter,sans-serif;font-weight:600;box-shadow:0 8px 32px rgba(0,0,0,0.5)';
          toast.textContent = '✓ Email verified successfully!';
          document.body.appendChild(toast);
          setTimeout(() => toast.remove(), 5000);
        }, 1000);
        // Clean URL
        const clean = new URL(window.location.href);
        clean.searchParams.delete('verified');
        window.history.replaceState({}, '', clean.toString());
      }

      // Verification error
      const verifyErr = params.get('verify_error');
      if (verifyErr) {
        setTimeout(() => {
          const toast = document.createElement('div');
          toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#161b22;border:1px solid #f85149;border-radius:10px;padding:12px 20px;color:#f85149;font-size:0.85rem;z-index:99999;font-family:Inter,sans-serif;box-shadow:0 8px 32px rgba(0,0,0,0.5)';
          toast.textContent = '✕ ' + decodeURIComponent(verifyErr);
          document.body.appendChild(toast);
          setTimeout(() => toast.remove(), 6000);
        }, 800);
        const clean = new URL(window.location.href);
        clean.searchParams.delete('verify_error');
        window.history.replaceState({}, '', clean.toString());
      }
    })();

    // ── Auth gate UI ───────────────────────────────────────────────────
    function nxShowAuthGate() {
      const gate = document.getElementById('nx-auth-gate');
      if (!gate) return;
      gate.classList.remove('nx-auth-hidden', 'nx-auth-exiting');
      // Reset card loading state on show
      nxAuthCardLoading(false);
    }

    function nxHideAuthGate() {
      const gate = document.getElementById('nx-auth-gate');
      if (!gate) return;
      gate.classList.add('nx-auth-exiting');
      setTimeout(() => {
        gate.classList.add('nx-auth-hidden');
        gate.classList.remove('nx-auth-exiting');
        nxAuthCardLoading(false);
      }, 340);
    }

    function nxAuthCardLoading(on) {
      const card = document.getElementById('nx-auth-card');
      if (!card) return;
      if (on) {
        card.setAttribute('data-loading', 'true');
        card.querySelectorAll('input, button').forEach(el => { el.disabled = true; });
      } else {
        card.removeAttribute('data-loading');
        card.querySelectorAll('input, button').forEach(el => { el.disabled = false; });
      }
    }

    function nxAuthErr(msg) {
      const el = document.getElementById('nx-auth-err');
      if (!el) return;
      el.textContent = msg;
      el.classList.add('show');
      setTimeout(() => el.classList.remove('show'), 6000);
    }

    window.nxAuthTab = function (tab) {
      document.getElementById('nx-form-login').classList.toggle('nx-hidden', tab !== 'login');
      document.getElementById('nx-form-signup').classList.toggle('nx-hidden', tab !== 'signup');
      const loginTab = document.getElementById('nx-tab-login');
      const signupTab = document.getElementById('nx-tab-signup');
      if (loginTab) { loginTab.classList.toggle('active', tab === 'login'); loginTab.setAttribute('aria-selected', tab === 'login'); }
      if (signupTab) { signupTab.classList.toggle('active', tab === 'signup'); signupTab.setAttribute('aria-selected', tab === 'signup'); }
      document.getElementById('nx-auth-err').classList.remove('show');
    };

    // ── Login ──────────────────────────────────────────────────────────
    window.nxDoLogin = async function () {
      const identifier = (document.getElementById('nx-login-id')?.value || '').trim();
      const password = document.getElementById('nx-login-pw')?.value || '';
      const btn = document.getElementById('nx-btn-login');

      if (!identifier || !password) { nxAuthErr('Please fill in all fields'); return; }

      nxAuthCardLoading(true);
      if (btn) btn.textContent = 'Signing in…';
      try {
        const r = await _origFetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: identifier, username: identifier, password }),
        });
        const d = await r.json();
        if (d.ok) {
          const p = nxDecodeJWT(d.access_token);
          nxStoreTokens(d, p?.name || p?.email || identifier);
          nxScheduleRefresh(d.expires_in || 900);
          nxHideAuthGate();
          nxRenderUserBadge();
          setTimeout(nxCheckVerification, 2000);
        } else {
          nxAuthCardLoading(false);
          if (btn) btn.textContent = 'Sign In';
          nxAuthErr(d.error || 'Sign in failed. Check your details and try again.');
        }
      } catch (e) {
        nxAuthCardLoading(false);
        if (btn) btn.textContent = 'Sign In';
        nxAuthErr('Network error. Please check your connection and try again.');
      }
    };

    // ── Signup ─────────────────────────────────────────────────────────
    window.nxDoSignup = async function () {
      const name = (document.getElementById('nx-signup-name')?.value || '').trim();
      const email = (document.getElementById('nx-signup-email')?.value || '').trim();
      const password = document.getElementById('nx-signup-pw')?.value || '';
      const btn = document.getElementById('nx-btn-signup');

      if (!email || !password) { nxAuthErr('Email and password are required'); return; }
      if (password.length < 8) { nxAuthErr('Password must be at least 8 characters'); return; }
      if (!email.includes('@')) { nxAuthErr('Enter a valid email address'); return; }
      if (!document.getElementById('nx-signup-agree')?.checked) {
        nxAuthErr('Please agree to the Terms of Service and Privacy Policy to continue.');
        return;
      }

      nxAuthCardLoading(true);
      if (btn) btn.textContent = 'Creating account…';
      try {
        const r = await _origFetch('/api/auth/signup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, name, password }),
        });
        const d = await r.json();
        if (d.ok) {
          const p = nxDecodeJWT(d.access_token);
          nxStoreTokens(d, name || p?.email || email);
          nxScheduleRefresh(d.expires_in || 900);
          nxHideAuthGate();
          nxRenderUserBadge();
          setTimeout(nxCheckVerification, 2000);
        } else {
          nxAuthCardLoading(false);
          if (btn) btn.textContent = 'Create Account';
          nxAuthErr(d.error || 'Account creation failed. Please try again.');
        }
      } catch (e) {
        nxAuthCardLoading(false);
        if (btn) btn.textContent = 'Create Account';
        nxAuthErr('Network error. Please check your connection and try again.');
      }
    };

    // ── Change Password ─────────────────────────────────────────────────
    window.nxChangePassword = async function () {
      const oldPw = (document.getElementById('sec-old-pw')?.value || '').trim();
      const newPw = (document.getElementById('sec-new-pw')?.value || '');
      const confirmPw = (document.getElementById('sec-confirm-pw')?.value || '');
      const msgEl = document.getElementById('sec-pw-msg');
      const btn = document.getElementById('sec-pw-btn');

      const setMsg = (text, color) => {
        if (msgEl) { msgEl.textContent = text; msgEl.style.color = color; }
      };

      if (!oldPw || !newPw || !confirmPw) { setMsg('All fields are required.', 'var(--red,#f85149)'); return; }
      if (newPw.length < 8) { setMsg('New password must be at least 8 characters.', 'var(--red,#f85149)'); return; }
      if (newPw !== confirmPw) { setMsg('New passwords do not match.', 'var(--red,#f85149)'); return; }

      if (btn) { btn.disabled = true; btn.textContent = 'Updating…'; }
      setMsg('', '');
      try {
        const r = await fetch('/api/auth/change-password', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
        });
        const d = await r.json();
        if (d.ok) {
          setMsg('✓ ' + d.message, 'var(--green,#3fb950)');
          ['sec-old-pw', 'sec-new-pw', 'sec-confirm-pw'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
          });
        } else {
          setMsg(d.error || 'Password change failed.', 'var(--red,#f85149)');
        }
      } catch {
        setMsg('Network error. Please try again.', 'var(--red,#f85149)');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Update Password'; }
      }
    };

    // ── Delete Account Modal ─────────────────────────────────────────────
    window.nxShowDeleteModal = function () {
      const existing = document.getElementById('nx-delete-modal');
      if (existing) { existing.remove(); return; }

      const token = nxDecodeJWT(nxGetToken());
      const provider = token?.provider || 'local';
      const isOAuth = provider !== 'local';

      const modal = document.createElement('div');
      modal.id = 'nx-delete-modal';
      Object.assign(modal.style, {
        position: 'fixed', inset: '0', zIndex: '999999',
        background: 'rgba(13,17,23,0.92)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        fontFamily: "'Inter',sans-serif",
      });
      modal.innerHTML = `
        <div style="background:#1c0d0d;border:2px solid #f85149;border-radius:14px;padding:28px 28px 22px;max-width:400px;width:90%;box-shadow:0 24px 64px rgba(0,0,0,0.7)">
            <div style="font-size:1.4rem;font-weight:800;color:#f85149;margin-bottom:6px">⚠ Delete Account</div>
            <div style="font-size:0.87rem;color:#c9d1d9;margin-bottom:18px;line-height:1.6">
                This will <strong>permanently delete</strong> your account, all sessions, chat history, and data.<br>
                <strong>This action cannot be undone.</strong>
            </div>
            <div style="margin-bottom:14px">
                <label style="font-size:0.78rem;color:#8b949e;display:block;margin-bottom:5px">
                    ${isOAuth ? 'Type <strong style="color:#f85149">DELETE</strong> to confirm' : 'Enter your password to confirm'}
                </label>
                <input type="${isOAuth ? 'text' : 'password'}" id="nx-del-confirm"
                       placeholder="${isOAuth ? 'DELETE' : '••••••••'}"
                       style="width:100%;background:#0d1117;border:1px solid #f85149;border-radius:6px;padding:9px 12px;color:#e6edf3;font-size:0.87rem">
            </div>
            <div id="nx-del-msg" style="font-size:0.82rem;color:#f85149;min-height:18px;margin-bottom:12px"></div>
            <div style="display:flex;gap:10px">
                <button onclick="document.getElementById('nx-delete-modal').remove()"
                        style="flex:1;padding:9px;border-radius:6px;border:1px solid #30363d;background:#21262d;color:#8b949e;font-size:0.87rem;cursor:pointer;font-family:inherit">
                    Cancel
                </button>
                <button id="nx-del-btn" onclick="nxConfirmDeleteAccount()"
                        style="flex:1;padding:9px;border-radius:6px;border:none;background:#f85149;color:#fff;font-size:0.87rem;font-weight:700;cursor:pointer;font-family:inherit">
                    Delete Forever
                </button>
            </div>
        </div>
    `;
      document.body.appendChild(modal);
      setTimeout(() => document.getElementById('nx-del-confirm')?.focus(), 50);
    };

    window.nxConfirmDeleteAccount = async function () {
      const confirm = (document.getElementById('nx-del-confirm')?.value || '').trim();
      const msgEl = document.getElementById('nx-del-msg');
      const btn = document.getElementById('nx-del-btn');

      if (!confirm) {
        if (msgEl) msgEl.textContent = 'This field is required.';
        return;
      }

      if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
      try {
        const r = await fetch('/api/auth/delete-account', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: confirm }),
        });
        const d = await r.json();
        if (d.ok) {
          nxClearTokens();
          clearTimeout(_nxRefreshTimer);
          document.getElementById('nx-delete-modal')?.remove();
          nxRemoveUserBadge();
          nxShowAuthGate();
          setTimeout(() => nxAuthErr('Your account has been permanently deleted.'), 200);
        } else {
          if (msgEl) msgEl.textContent = d.error || 'Deletion failed. Please try again.';
          if (btn) { btn.disabled = false; btn.textContent = 'Delete Forever'; }
        }
      } catch {
        if (msgEl) msgEl.textContent = 'Network error. Please try again.';
        if (btn) { btn.disabled = false; btn.textContent = 'Delete Forever'; }
      }
    };

    // ── Cookie Consent ─────────────────────────────────────────────────
    // Z56: nxAcceptCookies is now owned by nx-z50.js (z50DismissCookieBanner).
    // Banner init is also handled there. Removed duplicate definition here.

    // ── Data Request / Account Deletion ────────────────────────────────
    window.nxRequestData = function () {
      const email = nxGetProfile()?.email || 'your registered email';
      alert(`Data export request received.\n\nWe will send a copy of all your personal data to ${email} within 30 days, in accordance with our Privacy Policy.\n\nFor urgent requests contact: support@nexora.ai`);
    };
    window.nxRequestAccountDeletion = function () {
      const confirmed = confirm(
        'Are you sure you want to permanently delete your account?\n\n' +
        'This will erase all your data, sessions, and subscription history.\n\n' +
        'This action CANNOT be undone.\n\nClick OK to confirm and email support to proceed.'
      );
      if (confirmed) {
        const email = nxGetProfile()?.email || '';
        window.location.href = `mailto:support@nexora.ai?subject=Account%20Deletion%20Request&body=Please%20permanently%20delete%20my%20Nexora%20AI%20account%20and%20all%20associated%20data.%0A%0ARegistered%20email%3A%20${encodeURIComponent(email)}`;
      }
    };
    function nxGetProfile() {
      try { return JSON.parse(localStorage.getItem('nx_profile') || '{}'); } catch { return {}; }
    }

    // ── Logout ─────────────────────────────────────────────────────────
    window.nxLogout = async function () {
      // Cookie is sent automatically; no need to pass refresh_token in body
      try {
        await _origFetch('/api/auth/logout', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        });
      } catch { }
      nxClearTokens();
      clearTimeout(_nxRefreshTimer);
      nxRemoveUserBadge();
      nxShowAuthGate();
    };

    window.nxLogoutAll = async function () {
      try {
        await fetch('/api/auth/logout-all', { method: 'POST' });
      } catch { }
      nxClearTokens();
      clearTimeout(_nxRefreshTimer);
      nxRemoveUserBadge();
      nxShowAuthGate();
    };

    // ── User badge ─────────────────────────────────────────────────────
    function nxRenderUserBadge() {
      nxRemoveUserBadge();
      const user = nxGetUser();
      const token = nxDecodeJWT(nxGetToken());
      const name = user.name || token?.name || token?.email || 'You';
      const initials = name.slice(0, 2).toUpperCase();

      const badge = document.createElement('div');
      badge.id = 'nx-user-badge';
      badge.className = 'nx-auth-user-badge';
      badge.title = 'Account & Sessions';
      badge.innerHTML = `
        <div class="nx-auth-avatar">${initials}</div>
        <span>${name.split(' ')[0].substring(0, 14)}</span>
        <span style="color:var(--muted);font-size:10px;">▾</span>
    `;
      badge.onclick = nxShowAccountPanel;
      document.body.appendChild(badge);
    }

    function nxRemoveUserBadge() {
      const el = document.getElementById('nx-user-badge');
      if (el) el.remove();
    }

    // ── Account panel (sessions) ───────────────────────────────────────
    function nxShowAccountPanel() {
      const existing = document.getElementById('nx-account-panel');
      if (existing) { existing.remove(); return; }

      const panel = document.createElement('div');
      panel.id = 'nx-account-panel';
      Object.assign(panel.style, {
        position: 'fixed', top: '42px', right: '56px', zIndex: '99998',
        background: 'var(--panel,#161b22)', border: '1px solid var(--border,#30363d)',
        borderRadius: '12px', padding: '16px', width: '320px',
        boxShadow: '0 16px 48px rgba(0,0,0,0.5)', fontFamily: "'Inter', sans-serif",
      });

      const user = nxGetUser();
      const token = nxDecodeJWT(nxGetToken());
      const name = user.name || token?.name || 'User';
      const email = token?.email || '';

      panel.innerHTML = `
        <div style="font-size:0.85rem;font-weight:700;color:var(--text);margin-bottom:4px">${name}</div>
        ${email ? `<div style="font-size:0.75rem;color:var(--muted);margin-bottom:12px">${email}</div>` : ''}
        <div style="display:flex;gap:8px;margin-bottom:14px">
            <button onclick="nxLoadSessions()" style="flex:1;padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel2);color:var(--text);font-size:0.78rem;cursor:pointer">📱 Sessions</button>
            <button onclick="nxLogout()" style="flex:1;padding:6px;border-radius:6px;border:none;background:var(--red,#f85149);color:#fff;font-size:0.78rem;cursor:pointer">Sign Out</button>
        </div>
        <button onclick="nxLogoutAll()" style="width:100%;padding:6px;border-radius:6px;border:1px solid var(--border);background:none;color:var(--muted);font-size:0.75rem;cursor:pointer">⚠ Sign out from all devices</button>
        <div id="nx-sessions-list" style="margin-top:12px"></div>
    `;

      document.body.appendChild(panel);
      document.addEventListener('click', function _dismiss(e) {
        if (!panel.contains(e.target) && e.target.id !== 'nx-user-badge') {
          panel.remove();
          document.removeEventListener('click', _dismiss);
        }
      }, true);
    }

    window.nxLoadSessions = async function () {
      const el = document.getElementById('nx-sessions-list');
      if (!el) return;
      el.innerHTML = '<div style="color:var(--muted);font-size:0.75rem">Loading sessions…</div>';
      try {
        const r = await fetch('/api/auth/sessions');
        const d = await r.json();
        if (!d.ok) { el.innerHTML = '<div style="color:var(--red);font-size:0.75rem">Failed to load sessions</div>'; return; }
        const rows = (d.sessions || []).map(s => {
          const da = s.created_at ? new Date(s.created_at).toLocaleDateString() : '';
          const dev = (s.device_info || 'Unknown').substring(0, 40);
          return `<div class="nx-session-row">
                <div class="nx-session-info">
                    <div class="nx-session-device" title="${s.device_info || ''}">${dev}</div>
                    <div class="nx-session-meta">${s.ip_address || ''} · ${da}</div>
                </div>
                <button class="nx-session-revoke" onclick="nxRevokeSession('${s.id}', this)">Revoke</button>
            </div>`;
        }).join('');
        el.innerHTML = `<div class="nx-sessions-panel">${rows || '<div style="color:var(--muted);font-size:0.75rem">No active sessions</div>'}</div>`;
      } catch {
        el.innerHTML = '<div style="color:var(--red);font-size:0.75rem">Error loading sessions</div>';
      }
    };

    window.nxRevokeSession = async function (sessionId, btn) {
      if (btn) { btn.disabled = true; btn.textContent = '…'; }
      try {
        const r = await fetch(`/api/auth/sessions/${sessionId}`, { method: 'DELETE' });
        const d = await r.json();
        if (d.ok) {
          const row = btn?.closest('.nx-session-row');
          if (row) row.remove();
        }
      } catch { }
    };

    // ── Handle OAuth callback tokens in URL ────────────────────────────
    function nxHandleOAuthCallback() {
      const params = new URLSearchParams(window.location.search);
      const token = params.get('nx_token');
      const name = params.get('nx_name');
      const error = params.get('auth_error');

      if (error) {
        const msgs = {
          invalid_state: 'OAuth security check failed. Please try again.',
          no_email: 'Could not retrieve email from OAuth provider.',
          google_failed: 'Google sign-in failed. Please try again.',
          github_failed: 'GitHub sign-in failed. Please try again.',
        };
        history.replaceState({}, '', window.location.pathname);
        nxShowAuthGate();
        setTimeout(() => nxAuthErr(msgs[error] || 'Sign-in failed'), 200);
        return true; // handled (error case)
      }

      if (token) {
        // Refresh cookie is already set by the server-side redirect
        nxStoreTokens({ access_token: token }, decodeURIComponent(name || ''));
        history.replaceState({}, '', window.location.pathname);
        nxScheduleRefresh(900);
        nxHideAuthGate();
        nxRenderUserBadge();
        setTimeout(nxCheckVerification, 2000);
        console.log('[Auth] OAuth sign-in successful');
        return true; // handled
      }

      return false; // nothing to handle
    }

    // ── Init: check auth state on page load — no flash, cookie-first ───
    async function nxAuthInit() {
      // ── STARTUP GLITCH FIX ──────────────────────────────────────────
      // Apply a brief opacity:0 guard on document.body so the browser
      // does not flash a broken/unresolved render state while the async
      // /api/auth/refresh network call is in-flight.
      // The auth gate is position:fixed + z-index:99999, so it renders
      // on top of the faded body correctly in all cases.
      // We use opacity (not display/visibility) so layout is fully
      // computed and no reflow happens on reveal.
      if (!document.body.style.opacity) {
        document.body.style.opacity = '0';
        document.body.style.transition = 'opacity 0.18s ease';
      }

      function _revealBody() {
        document.body.style.opacity = '1';
        // Clean up the inline transition after it completes
        setTimeout(() => { document.body.style.transition = ''; }, 250);
      }

      // Handle OAuth callback first — if it handles it, we're done
      if (nxHandleOAuthCallback()) { _revealBody(); return; }

      // If we have a non-expired access token in memory, use it immediately
      const token = nxGetToken();
      if (token && !nxIsTokenExpired(token)) {
        nxHideAuthGate();
        nxRenderUserBadge();
        nxScheduleRefresh(900);
        _revealBody();
        return;
      }

      // Always attempt silent refresh via HttpOnly cookie —
      // this covers: expired access token, fresh page load, cross-tab restore
      const ok = await nxRefreshNow();
      if (ok) {
        nxHideAuthGate();
        nxRenderUserBadge();
      } else {
        nxClearTokens();
        nxShowAuthGate();
      }
      _revealBody();
    }

    window.NX_BOOT_TASKS.push(nxAuthInit);

    window.nxGetToken = nxGetToken;
    window.nxGetUser = nxGetUser;
    window.nxStoreTokens = nxStoreTokens;
    window.nxClearTokens = nxClearTokens;
    window.nxDecodeJWT = nxDecodeJWT;
    window.nxIsTokenExpired = nxIsTokenExpired;
    window.nxScheduleRefresh = nxScheduleRefresh;
    window.nxRefreshNow = nxRefreshNow;
    window.nxShowAuthGate = nxShowAuthGate;
    window.nxHideAuthGate = nxHideAuthGate;

    console.debug('[Auth] active');
  })();
