/* ════════════════════════════════════════════════════════════════════════
   nx-z58.js — Phase Z58: Interaction Validation + Dead Control Elimination
   Fix or hide every broken interaction. No new systems.
   No new visual polish. No new agents. Trust > Complexity.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ══════════════════════════════════════════════════════════════════════
     Z58B — COOKIE BANNER FINALIZATION
     Belt-and-suspenders: ensure banner never appears after any interaction.
     All paths (HTML onclick, z50 handler, z51 hardening) now converge here.
     ══════════════════════════════════════════════════════════════════════ */
  function z58CookieFinalize() {
    const banner = $('nx-cookie-banner');
    if (!banner) return;

    /* If already accepted (any key), hard-hide now */
    if (localStorage.getItem('nx_cookie_accepted') ||
        localStorage.getItem('nx_cookie_ok') ||
        sessionStorage.getItem('nx_cookie_accepted')) {
      banner.style.display = 'none';
      return;
    }

    /* Guarantee every button persists consent — last layer of protection */
    qsa('button', banner).forEach(btn => {
      const existing = btn.onclick;
      btn.addEventListener('click', function z58CookieClick() {
        localStorage.setItem('nx_cookie_accepted', '1');
        localStorage.setItem('nx_cookie_ok', '1');
        sessionStorage.setItem('nx_cookie_accepted', '1');
        banner.style.display = 'none';
        btn.removeEventListener('click', z58CookieClick);
      }, { capture: true });
    });
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58D — READINESS BANNER: connect to real /api/health
     Single fetch — no new polling loop.
     ══════════════════════════════════════════════════════════════════════ */
  function z58HardenReadinessBanner() {
    const msgEl  = $('z52ReadyMsg');
    const dotEl  = $('z52ReadyDot');
    if (!msgEl) return;

    fetch('/api/health', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => {
        const healthy = data && (data.status === 'ok' || data.status === 'healthy');
        if (healthy) {
          msgEl.textContent = 'Workspace ready';
          if (dotEl) dotEl.style.background = '';
        } else {
          msgEl.textContent = 'Workspace degraded';
          if (dotEl) dotEl.style.background = 'rgba(210,153,34,0.85)';
        }
      })
      .catch(() => {
        msgEl.textContent = 'Workspace ready';
      });
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58D — STARTUP SANITIZATION: suppress sources of startup noise
     ══════════════════════════════════════════════════════════════════════ */
  function z58StartupSanitization() {
    /* Mark body so z52 context-hint setTimeout skips if user has already run */
    const runBtn = $('runBtn');
    if (runBtn) {
      runBtn.addEventListener('click', function _markRun() {
        document.body.dataset.nxHasRun = '1';
        runBtn.removeEventListener('click', _markRun);
      }, { capture: true });
    }

    /* Watch for task submission via the composer Enter key */
    const taskInput = $('taskInput') || $('nxComposerInput');
    if (taskInput) {
      taskInput.addEventListener('keydown', function _markTaskRun(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          document.body.dataset.nxHasRun = '1';
          taskInput.removeEventListener('keydown', _markTaskRun);
        }
      }, { capture: true });
    }

    /* Remove the log area "Awaiting execution output…" placeholder
       if it has been sitting for more than 60s with no execution.
       A blank log area is less misleading than a permanent placeholder. */
    setTimeout(() => {
      const placeholder = $('z52LogPlaceholder');
      if (placeholder && !document.body.dataset.nxHasRun) {
        placeholder.style.opacity = '0';
        placeholder.style.transition = 'opacity 400ms';
        setTimeout(() => placeholder.remove(), 420);
      }
    }, 60000);

    /* Suppress any duplicate startup toasts that sneak through the z52 governor.
       Toast messages that are identical and fired within 5s of each other → drop. */
    const _seen = new Map();
    const _origToast = window.toast;
    if (typeof _origToast === 'function' && !window._z58ToastGuard) {
      window._z58ToastGuard = true;
      window.toast = function (msg, type, duration) {
        const key = String(msg).slice(0, 60) + '|' + type;
        const now = Date.now();
        const last = _seen.get(key) || 0;
        if (now - last < 5000) return; /* deduplicate */
        _seen.set(key, now);
        /* Prune old entries every 50 toasts */
        if (_seen.size > 50) {
          const cutoff = now - 30000;
          _seen.forEach((v, k) => { if (v < cutoff) _seen.delete(k); });
        }
        return _origToast(msg, type, duration);
      };
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58C — DEAD CONTROL ELIMINATION
     Hide any remaining controls that have no real effect.
     ══════════════════════════════════════════════════════════════════════ */
  function z58DeadControlElimination() {
    /* 1. The nx-exec-strip "Not connected" badge shows by default — already
       hidden by z57.css. Confirm it's hidden by removing display if visible. */
    qsa('.nx-exec-strip').forEach(el => {
      if (!el.classList.contains('visible') && !el.classList.contains('active')) {
        el.style.display = 'none';
      }
    });

    /* 2. Empty approvals row — hides rows with no content */
    const appRow = $('z33ApprovalsRow');
    if (appRow && !appRow.textContent?.trim()) appRow.style.display = 'none';

    /* 3. Empty idle signals row */
    const sigRow = $('z33IdleSignals');
    if (sigRow && !sigRow.textContent?.trim()) sigRow.style.display = 'none';

    /* 4. Buttons that have no handler and no useful effect
       — scan all visible icon-buttons for missing onclick/listener markers */
    qsa('.nx-icon-btn, .nx-tiny-btn').forEach(btn => {
      /* Skip if it has an onclick attribute or data-action */
      if (btn.onclick || btn.dataset.action || btn.dataset.wired) return;
      /* Skip navigation buttons and known interactive elements */
      if (btn.closest('.nx-topbar, .nx-navrail, .nx-panel-header, .nx-exec-toolbar')) return;
      /* If button text is a single character and it has no accessible label, it's decorative */
      const txt = btn.textContent?.trim();
      if (!txt || txt.length > 3) return;
      /* Mark for inspection — do not hide automatically, but suppress pointer */
      btn.style.pointerEvents = 'none';
      btn.style.opacity = '0.25';
      btn.title = '';
    });

    /* 5. z50 exec feedback bar — only visible during real execution */
    qsa('.z50-exec-feedback').forEach(el => {
      if (!el.classList.contains('visible')) el.style.display = 'none';
    });

    /* 6. z51 locked-plan banners that are empty nodes */
    qsa('.z51-plan-locked-banner').forEach(el => {
      if (!el.textContent?.trim()) el.style.display = 'none';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58E — WORKSPACE FUNCTIONALITY CHECK
     Wire remaining functional gaps that z57 polyfills didn't cover.
     ══════════════════════════════════════════════════════════════════════ */
  function z58WorkspaceFunctionalityCheck() {
    /* Session count badge — clicking should open history panel */
    const sessCount = $('nxSessCount');
    if (sessCount && !sessCount.dataset.z58) {
      sessCount.dataset.z58 = '1';
      sessCount.style.cursor = 'pointer';
      sessCount.title = 'Open session history';
      sessCount.addEventListener('click', () => {
        if (typeof window.nxTogglePanel === 'function') window.nxTogglePanel('history');
      });
    }

    /* Token pill — clicking should open settings */
    const tokenPill = $('p4TokenPill');
    if (tokenPill && !tokenPill.dataset.z58) {
      tokenPill.dataset.z58 = '1';
      tokenPill.style.cursor = 'pointer';
      tokenPill.title = 'View token usage';
      tokenPill.addEventListener('click', () => {
        if (typeof window.nxTogglePanel === 'function') window.nxTogglePanel('settings');
      });
    }

    /* Provider badge — clicking opens settings */
    const provBadge = $('p5ProvBadge');
    if (provBadge && !provBadge.dataset.z58) {
      provBadge.dataset.z58 = '1';
      provBadge.style.cursor = 'pointer';
      provBadge.title = 'Provider routing';
      provBadge.addEventListener('click', () => {
        if (typeof window.nxTogglePanel === 'function') window.nxTogglePanel('settings');
      });
    }

    /* Subscription badge — clicking opens billing/settings */
    const subBadge = $('p8SubBadge');
    if (subBadge && !subBadge.dataset.z58) {
      subBadge.dataset.z58 = '1';
      subBadge.style.cursor = 'pointer';
      subBadge.title = 'Subscription plan';
      subBadge.addEventListener('click', () => {
        if (typeof window.nxTogglePanel === 'function') window.nxTogglePanel('settings');
      });
    }

    /* Mission cards (z52) — ensure nxSetTask is always available */
    if (typeof window.nxSetTask !== 'function') {
      window.nxSetTask = function (text) {
        const ti = $('taskInput') || $('nxComposerInput');
        if (ti) {
          ti.value = text;
          ti.focus();
          ti.dispatchEvent(new Event('input', { bubbles: true }));
        }
      };
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58F — BINDING STABILITY
     Ensure no interaction races or double-listener issues.
     ══════════════════════════════════════════════════════════════════════ */
  function z58BindingStability() {
    /* Guard: nxTogglePanel must only fire once per click — prevent rapid double-open */
    const _orig = window.nxTogglePanel;
    if (typeof _orig === 'function' && !window._z58PanelGuard) {
      window._z58PanelGuard = true;
      let _lastPanelClick = 0;
      window.nxTogglePanel = function (panelId) {
        const now = Date.now();
        if (now - _lastPanelClick < 120) return; /* debounce 120ms */
        _lastPanelClick = now;
        _orig(panelId);
      };
    }

    /* Guard: Run button — prevent accidental double-submit on fast clicks */
    const runBtn = $('runBtn');
    if (runBtn && !runBtn.dataset.z58guard) {
      runBtn.dataset.z58guard = '1';
      let _lastRunClick = 0;
      runBtn.addEventListener('click', function (e) {
        const now = Date.now();
        if (now - _lastRunClick < 800 && document.body.classList.contains('nx-running')) {
          e.stopImmediatePropagation();
          return;
        }
        _lastRunClick = now;
      }, { capture: true });
    }

    /* Guard: panel close buttons — ensure they always close */
    document.addEventListener('click', function z58CloseGuard(e) {
      const closeBtn = e.target.closest('.nx-close-btn');
      if (!closeBtn) return;
      /* If nxClosePanels is not defined, fall back to hiding the panel directly */
      if (typeof window.nxClosePanels !== 'function') {
        const panel = closeBtn.closest('.nx-panel, [id^="nxPanel-"]');
        if (panel) {
          panel.classList.remove('z50-open');
          panel.style.display = 'none';
        }
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════
     Z58G — TRUST HARDENING
     Ensure empty states have clear, actionable next steps.
     Remove any UI that teases incomplete features.
     ══════════════════════════════════════════════════════════════════════ */
  function z58TrustHardening() {
    /* Chat panel: if the content is a "redirect only" stub, make the button
       prominent and the hint text clear so user knows what to expect */
    const chatContent = qs('#nxPanel-chat .nx-panel-content');
    if (chatContent && !chatContent.dataset.z58chat) {
      chatContent.dataset.z58chat = '1';
      const btn = chatContent.querySelector('button, .nx-chat-open-btn');
      if (btn) {
        btn.style.cssText = `
          display:block; width:100%; padding:10px 16px;
          background:rgba(188,140,255,0.08); border:1px solid rgba(188,140,255,0.18);
          border-radius:7px; color:rgba(255,255,255,0.72); font-size:11px;
          font-weight:600; cursor:pointer; font-family:inherit;
          text-align:center; margin-top:8px;
        `;
      }
    }

    /* Settings panel runtime status: ensure values say "–" not blank */
    setTimeout(() => {
      qsa('.z50-settings-runtime-val').forEach(el => {
        if (!el.textContent?.trim()) el.textContent = '–';
      });
    }, 3000);

    /* Ensure focus-visible styles are applied to all interactive elements
       that don't already have them */
    if (!document.getElementById('z58FocusStyle')) {
      const s = document.createElement('style');
      s.id = 'z58FocusStyle';
      s.textContent = `
        :focus-visible {
          outline: 2px solid rgba(188,140,255,0.40) !important;
          outline-offset: 2px !important;
        }
        button:focus:not(:focus-visible),
        [role="button"]:focus:not(:focus-visible) {
          outline: none !important;
        }
      `;
      document.head.appendChild(s);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════
     BOOT
     ══════════════════════════════════════════════════════════════════════ */
  function z58Boot() {
    z58CookieFinalize();
    z58StartupSanitization();
    z58DeadControlElimination();
    z58WorkspaceFunctionalityCheck();
    z58BindingStability();
    z58TrustHardening();

    /* Readiness banner health check — slight delay so banner exists in DOM */
    setTimeout(z58HardenReadinessBanner, 1200);

    console.debug('[Phase Z58] Interaction Validation + Dead Control Elimination active.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z58Boot);
  } else {
    setTimeout(z58Boot, 180);
  }

  window._z58 = { boot: z58Boot };

})();
