/* ════════════════════════════════════════════════════════════════════════
   nx-z52.js — Phase Z52: Operational Product Experience + UI Maturity
   Auth identity, toast governance, workspace presence, product maturity.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (sel, r) => (r || document).querySelector(sel);
  const qsa = (sel, r) => Array.from((r || document).querySelectorAll(sel));

  /* ═══════════════════════════════════════════════════════════════════
     Z52A-INJECT: Inject authoritative auth styles via JS-created <style>
     This bypasses any CSS cascade ambiguity — runs last, wins always.
     ═══════════════════════════════════════════════════════════════════ */
  function _injectAuthStyleTag() {
    if ($('z52-auth-styles')) return;
    const s = document.createElement('style');
    s.id = 'z52-auth-styles';
    s.textContent = `
      /* Z52A Authoritative Auth Overrides */
      #nx-auth-gate {
        background: #060810 !important;
        background-image: radial-gradient(ellipse 90% 55% at 50% -5%, rgba(88,130,255,0.06), transparent 65%) !important;
      }
      .nx-auth-card {
        background: #0c1018 !important;
        border: 1px solid rgba(255,255,255,0.07) !important;
        border-radius: 14px !important;
        padding: 40px 40px 30px !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.6), 0 24px 64px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.04) !important;
      }
      .nx-auth-tabs {
        background: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        margin-bottom: 24px !important;
        border-bottom: 1px solid rgba(255,255,255,0.07) !important;
        display: flex !important;
        gap: 0 !important;
      }
      .nx-auth-tab {
        flex: 1 !important;
        padding: 10px 0 !important;
        border-radius: 0 !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        color: rgba(139,148,158,0.6) !important;
        background: none !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        margin-bottom: -1px !important;
        cursor: pointer !important;
        transition: color 0.15s, border-color 0.15s !important;
      }
      .nx-auth-tab:hover {
        color: rgba(209,217,224,0.8) !important;
      }
      .nx-auth-tab.active {
        color: #d1d9e0 !important;
        background: none !important;
        border-bottom-color: #58a6ff !important;
        font-weight: 600 !important;
      }
      .nx-auth-field label {
        font-size: 11.5px !important;
        font-weight: 500 !important;
        color: rgba(139,148,158,0.8) !important;
        text-transform: none !important;
        letter-spacing: 0 !important;
      }
      .nx-auth-field input {
        background: rgba(255,255,255,0.025) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 8px !important;
        color: #d1d9e0 !important;
        padding: 11px 13px !important;
        font-size: 0.88rem !important;
      }
      .nx-auth-field input:focus {
        border-color: rgba(88,166,255,0.45) !important;
        box-shadow: 0 0 0 3px rgba(88,166,255,0.09) !important;
        outline: none !important;
      }
      .nx-auth-btn-primary {
        background: #d1d9e0 !important;
        color: #0a0d12 !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 11px !important;
        font-size: 0.87rem !important;
        transition: background 0.15s !important;
        cursor: pointer !important;
      }
      .nx-auth-btn-primary:hover:not(:disabled) {
        background: #e6edf3 !important;
      }
      .nx-auth-btn-primary:disabled {
        background: rgba(209,217,224,0.25) !important;
        color: rgba(10,13,18,0.5) !important;
        cursor: not-allowed !important;
      }
      .nx-auth-divider {
        color: rgba(139,148,158,0.35) !important;
        font-size: 0.72rem !important;
        margin: 20px 0 !important;
      }
      .nx-auth-divider::before, .nx-auth-divider::after {
        background: rgba(255,255,255,0.05) !important;
      }
      .nx-auth-oauth-btn {
        background: transparent !important;
        border: 1px solid rgba(255,255,255,0.07) !important;
        color: rgba(209,217,224,0.7) !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        border-radius: 8px !important;
      }
      .nx-auth-oauth-btn:hover {
        border-color: rgba(255,255,255,0.14) !important;
        background: rgba(255,255,255,0.03) !important;
        color: #d1d9e0 !important;
      }
      .nx-auth-footer {
        font-size: 11px !important;
        color: rgba(139,148,158,0.35) !important;
        text-align: center !important;
        margin-top: 20px !important;
      }
      .nx-auth-footer a { color: rgba(88,166,255,0.5) !important; }
    `;
    document.head.appendChild(s);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52A: AUTH IDENTITY — inject tagline, refine label text
     ═══════════════════════════════════════════════════════════════════ */
  function z52Auth() {
    /* Inject authoritative styles via <style> tag — guarantees override */
    _injectAuthStyleTag();

    const logo = qs('.nx-auth-logo');
    if (!logo || $('z52AuthTagline')) return;

    /* Inject product tagline below logo */
    const tagline = document.createElement('div');
    tagline.id = 'z52AuthTagline';
    tagline.className = 'z52-auth-tagline';
    tagline.textContent = 'Autonomous AI development workspace';
    logo.parentNode.insertBefore(tagline, logo.nextSibling);

    /* Normalise label text — convert ALL-CAPS labels to sentence case */
    _patchAuthLabels();
  }

  function _patchAuthLabels() {
    /* Only patch labels that are pure uppercase (created by the legacy CSS) */
    qsa('.nx-auth-field label').forEach(lbl => {
      const txt = lbl.firstChild?.textContent || '';
      if (txt === txt.toUpperCase() && txt.length > 1) {
        /* Sentence-case: first char upper, rest lower */
        lbl.firstChild.textContent = txt.charAt(0) + txt.slice(1).toLowerCase();
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52B / Z52F: TOAST GOVERNANCE — rich, deduplicated toast stack
     ═══════════════════════════════════════════════════════════════════ */
  const ToastGov = (function () {
    /* Priority levels: error(3) > warn(2) > ok(1) > info(0) */
    const PRIO = { err: 3, error: 3, warn: 2, warning: 2, ok: 1, info: 0, restore: 0 };
    const ICONS = { err: '✕', error: '✕', warn: '⚠', warning: '⚠', ok: '✓', info: '·', restore: '↩' };
    const MAX_VISIBLE = 3;
    const DEDUP_WINDOW_MS = 3500;
    const RESTORE_COLLECT_MS = 600;

    let _stack = [];            // { id, el, timer }
    let _seen = {};             // msg hash → timestamp
    let _restoreBuffer = [];    // restore messages collected within window
    let _restoreTimer = null;
    let _container = null;

    function _getContainer() {
      if (_container) return _container;
      _container = $('z52ToastStack');
      if (_container) return _container;
      _container = document.createElement('div');
      _container.id = 'z52ToastStack';
      document.body.appendChild(_container);
      return _container;
    }

    function _hash(msg) {
      /* Simple string hash for deduplication */
      const s = String(msg || '').toLowerCase().replace(/[^a-z0-9 ]/g, '').trim();
      let h = 0;
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
      return h;
    }

    function _isRestoreMsg(msg) {
      const m = String(msg).toLowerCase();
      return m.includes('restor') || m.includes('reconnect') || m.includes('session restored') ||
             m.includes('workspace restored') || m.includes('continuing from') ||
             m.includes('↩');
    }

    function _collectRestore(msg) {
      _restoreBuffer.push(msg);
      if (_restoreTimer) clearTimeout(_restoreTimer);
      _restoreTimer = setTimeout(() => {
        _restoreTimer = null;
        const items = _restoreBuffer.slice();
        _restoreBuffer = [];
        if (!items.length) return;
        /* Show one consolidated restore toast */
        _showRichToast(
          'Workspace restored',
          items.length > 1 ? items.length + ' systems synced' : null,
          'restore',
          items.length > 1 ? items : null,
          4000
        );
      }, RESTORE_COLLECT_MS);
    }

    function _deduplicate(msg, kind) {
      const h = _hash(msg);
      const now = Date.now();
      const last = _seen[h];
      if (last && now - last < DEDUP_WINDOW_MS) return true; /* suppress */
      _seen[h] = now;
      /* Clean old entries */
      const cutoff = now - DEDUP_WINDOW_MS * 2;
      Object.keys(_seen).forEach(k => { if (_seen[k] < cutoff) delete _seen[k]; });
      return false;
    }

    function _showRichToast(msg, detail, kind, detailList, duration) {
      const dur = duration || _getDuration(kind);
      const container = _getContainer();
      if (!container) return;

      /* Enforce max visible */
      while (_stack.length >= MAX_VISIBLE) {
        const oldest = _stack.shift();
        if (oldest) _dismiss(oldest.id);
      }

      const id = 'z52t-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
      const toast = document.createElement('div');
      toast.className = 'z52-toast ' + (kind || 'ok');
      toast.id = id;
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', kind === 'err' ? 'assertive' : 'polite');

      const icon = ICONS[kind] || '·';
      const hasDetail = detail || (detailList && detailList.length > 1);

      toast.innerHTML = `
        <div class="z52-toast-icon">${icon}</div>
        <div class="z52-toast-body">
          <div class="z52-toast-msg">${_esc(String(msg))}</div>
          ${detail ? `<div class="z52-toast-detail">${_esc(String(detail))}</div>` : ''}
          ${hasDetail && detailList ? `
            <button class="z52-toast-expand" onclick="this.nextElementSibling.classList.toggle('visible');this.textContent=this.nextElementSibling.classList.contains('visible')?'Show less ▲':'Show details ▾'">Show details ▾</button>
            <div class="z52-toast-detail-list">${detailList.map(d => _esc(String(d))).join('<br>')}</div>
          ` : ''}
        </div>
        <button class="z52-toast-dismiss" onclick="window._z52ToastGov.dismiss('${id}')" aria-label="Dismiss">✕</button>`;

      container.appendChild(toast);

      const timer = setTimeout(() => _dismiss(id), dur);
      _stack.push({ id, el: toast, timer });
    }

    function _dismiss(id) {
      const entry = _stack.find(s => s.id === id);
      if (entry) {
        clearTimeout(entry.timer);
        entry.el.classList.add('z52-toast-exit');
        setTimeout(() => { entry.el.remove(); }, 200);
        _stack = _stack.filter(s => s.id !== id);
      }
    }

    function _getDuration(kind) {
      if (kind === 'err' || kind === 'error') return 6000;
      if (kind === 'warn') return 4500;
      if (kind === 'restore') return 4000;
      return 2800;
    }

    function _esc(s) {
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    return {
      show: function (msg, kind, detail, detailList, duration) {
        if (!msg) return;
        /* Route restore messages through collector */
        if (_isRestoreMsg(msg) && kind !== 'err') {
          _collectRestore(msg);
          return;
        }
        /* Deduplicate */
        if (_deduplicate(msg, kind)) return;
        _showRichToast(msg, detail, kind, detailList, duration);
      },
      dismiss: _dismiss,
      info: function (msg, detail) { this.show(msg, 'info', detail); },
      silent: function () { /* intentional no-op for low-priority events */ },
    };
  })();

  window._z52ToastGov = ToastGov;

  /* Install toast intercept — wraps global toast/nxToast once they're available */
  function _installToastIntercept() {
    /* runtime.js defines toast() at a high scope — by the time Z52 runs (deferred last)
       it's available via closure in the same page. We intercept window.toast if set,
       and also define a global wrapper. */
    const origToast = typeof window.toast === 'function' ? window.toast : null;
    const origNxToast = typeof window.nxToast === 'function' ? window.nxToast : null;

    window.toast = function (msg, kind) {
      /* Show in Z52 governor */
      ToastGov.show(msg, kind || 'ok');
      /* Also fire original for legacy #toast element consumers */
      if (origToast) origToast(msg, kind);
    };

    window.nxToast = function (msg, dur) {
      ToastGov.show(msg, 'ok');
      if (origNxToast) origNxToast(msg, dur);
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52C: WORKSPACE PRESENCE — readiness + smart empty state
     ═══════════════════════════════════════════════════════════════════ */
  function z52WorkspacePresence() {
    _injectReadinessBanner();
    _upgradeEmptyState();
    _wireIdleHeroStats();
  }

  function _injectReadinessBanner() {
    const hero = $('nxIdleHero');
    if (!hero || $('z52ReadyBanner')) return;
    const banner = document.createElement('div');
    banner.id = 'z52ReadyBanner';
    banner.className = 'z52-workspace-ready';
    banner.innerHTML = `
      <span class="z52-ready-dot"></span>
      <span class="z52-ready-msg" id="z52ReadyMsg">Nexora ready · all systems operational</span>
      <span class="z52-ready-time" id="z52ReadyTime">${_fmtTime()}</span>`;
    /* Insert before the first child of hero */
    hero.insertBefore(banner, hero.firstChild);
    /* Update time every minute */
    setInterval(() => {
      const el = $('z52ReadyTime');
      if (el) el.textContent = _fmtTime();
    }, 60000);
  }

  function _fmtTime() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function _upgradeEmptyState() {
    const emptyEl = qs('.nx-iw-recent-empty', $('nxIdleRecent') || document);
    if (!emptyEl || emptyEl.dataset.z52) return;
    emptyEl.dataset.z52 = '1';
    /* Replace bare "No recent executions" with structured quick-start flows */
    emptyEl.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'z52-empty-state';
    wrap.innerHTML = `
      <div class="z52-empty-label">Start a mission</div>
      ${_missionCard('🏗', 'Build a feature', 'Describe a feature and Nexora will plan and implement it', 'Build a new feature: ')}
      ${_missionCard('🐛', 'Fix a bug', 'Paste the error — Nexora diagnoses and patches the code', 'Debug and fix this error: ')}
      ${_missionCard('🔍', 'Audit the codebase', 'Review files for bugs, security issues, and improvements', 'Audit workspace for errors and security issues')}
    `;
    emptyEl.appendChild(wrap);
  }

  function _missionCard(icon, title, sub, task) {
    const escaped = task.replace(/'/g, "\\'");
    return `<button class="z52-mission-card" onclick="nxSetTask('${escaped}');document.getElementById('nxComposerInput')?.focus()">
      <div class="z52-mission-icon">${icon}</div>
      <div class="z52-mission-body">
        <div class="z52-mission-title">${title}</div>
        <div class="z52-mission-sub">${sub}</div>
      </div>
      <span class="z52-mission-arrow">›</span>
    </button>`;
  }

  function _wireIdleHeroStats() {
    /* Poll model/confidence from existing nxIdleModel etc. — already done by Z50.
       Z52 adds context-aware hints: show a hint if model is unset or confidence is low */
    setInterval(() => {
      const modelEl = $('nxIdleModel');
      const confEl  = $('nxIdleConf');
      if (!modelEl || !confEl) return;

      const model = (modelEl.textContent || '').trim();
      const conf  = (confEl.textContent  || '').trim();

      /* Clear previous hints */
      qsa('.z52-context-hint', $('nxIdleHero') || document).forEach(el => el.remove());
      const hero = $('nxIdleHero');
      if (!hero || document.body.classList.contains('nx-running')) return;

      /* If model is unset, suggest configuring keys */
      if (!model || model === '—') {
        const hint = document.createElement('div');
        hint.className = 'z52-context-hint';
        hint.innerHTML = `<span>⚙</span> <span>No AI provider configured — <strong>open Settings → Providers</strong> to add your API keys</span>`;
        const section = qs('.nx-iw-section', hero) || hero;
        section.appendChild(hint);
      }

      /* If confidence is low, hint at context compression */
      if (conf && parseFloat(conf) < 30 && parseFloat(conf) > 0) {
        const hint = document.createElement('div');
        hint.className = 'z52-context-hint';
        hint.innerHTML = `<span>⚠</span> <span>Session confidence is low — consider <strong>starting a new session</strong> for best results</span>`;
        const section = qs('.nx-iw-section', hero) || hero;
        section.appendChild(hint);
      }
    }, 12000);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52G: PRODUCT IDENTITY — terminology & label cleanup
     ═══════════════════════════════════════════════════════════════════ */

  /* Map of generic strings → operational replacements */
  const IDENTITY_MAP = [
    /* Idle hero title — "Ready for execution" → "Nexora ready" */
    ['#nxIdleHero .nx-iw-header .nx-iw-title',       'Ready for execution',  'Nexora ready'],
    /* "Recent executions" → "Mission history" */
    ['#nxIdleHero .nx-iw-section-label',             'Recent executions',    'Mission history'],
    /* "Agent running" in HITL status */
    ['#hitlStatusText, #hitlStatusText-legacy',       'Agent running',        'Executing'],
    /* "Initializing..." in terminal header */
    ['#xtermStatus',                                  'Initializing...',      'Terminal ready'],
    /* Settings tab labels — keep "Providers & Models" but clean up "Sessions" → "Sessions" (already good) */
  ];

  function z52ApplyIdentity() {
    IDENTITY_MAP.forEach(([sel, old, replacement]) => {
      qsa(sel).forEach(el => {
        if (el.textContent?.trim() === old) {
          el.textContent = replacement;
          el.dataset.z52patched = '1';
        }
      });
    });

    /* Patch log area placeholder */
    const logArea = $('logArea');
    if (logArea && !logArea.textContent?.trim() && !logArea.dataset.z52) {
      logArea.dataset.z52 = '1';
      /* Add a subtle "Awaiting execution output" watermark via inner span */
      const span = document.createElement('div');
      span.style.cssText = 'padding:16px;font-size:11.5px;color:rgba(72,79,88,0.6);';
      span.textContent = 'Awaiting execution output…';
      span.id = 'z52LogPlaceholder';
      logArea.appendChild(span);
      /* Remove once real content appears */
      const obs = new MutationObserver(() => {
        if (logArea.children.length > 1 || (logArea.textContent.trim() && logArea.textContent.trim() !== span.textContent)) {
          span.remove();
          obs.disconnect();
        }
      });
      obs.observe(logArea, { childList: true, subtree: false });
    }

    /* Patch composer placeholder */
    const composer = $('nxComposerInput') || $('taskInput');
    if (composer && !composer.dataset.z52) {
      composer.dataset.z52 = '1';
      if (!composer.getAttribute('placeholder') || composer.getAttribute('placeholder') === 'Describe what to build or fix…') {
        /* Leave existing placeholder — it's already good */
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52B: RESTORE NOTIFICATION CONSOLIDATION
     Listens for nx:session:restored and routes to governor
     ═══════════════════════════════════════════════════════════════════ */
  function z52WireRestoreConsolidation() {
    if (!window.NxBus) {
      document.addEventListener('nx:bus:ready', z52WireRestoreConsolidation, { once: true });
      return;
    }

    /* Listen for session restored — governor deduplicates within RESTORE_COLLECT_MS window */
    window.NxBus.on('nx:session:restored', (data) => {
      const sid = data?.sid || '';
      ToastGov.show(
        'Session restored — workspace synced',
        sid ? 'Session ' + sid.slice(-8) : null,
        'restore'
      );
    }, { owner: 'nx-z52-restore-gov' });

    /* Listen for NxBus events that existing handlers catch and re-route their toasts
       The governor dedup logic in window.toast handles the rest */
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z52D: VISUAL HIERARCHY CLEANUP — runtime label fixes
     ═══════════════════════════════════════════════════════════════════ */
  function z52VisualHierarchy() {
    /* Ensure nxIdleHero stats strip data is readable vs muted */
    _syncHeroStats();

    /* Reduce prominence of offline/unavailable badges */
    qsa('[data-status="offline"], .nx-provider-offline').forEach(el => {
      el.style.opacity = '0.45';
    });
  }

  function _syncHeroStats() {
    /* Hook into the existing p9 routing update to keep hero stats fresh */
    const orig = window.p9UpdateRouting;
    if (typeof orig !== 'function' || window._z52HeroHooked) return;
    window._z52HeroHooked = true;
    window.p9UpdateRouting = function (data) {
      orig.call(this, data);
      /* Sync model to hero strip */
      const model = data?.coding?.model || data?.planning?.model || '';
      const modelEl = $('nxIdleModel');
      if (modelEl && model) modelEl.textContent = _trimModel(model);
    };
  }

  function _trimModel(model) {
    /* Shorten long model names for the stat strip */
    return model.replace('claude-', '').replace('gpt-', '').replace('-preview', '').replace('-latest', '').slice(0, 18);
  }

  /* ═══════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════ */
  function z52Boot() {
    /* Auth identity */
    z52Auth();

    /* Install toast intercept early */
    _installToastIntercept();

    /* Restore consolidation */
    z52WireRestoreConsolidation();

    /* Workspace presence & empty state */
    z52WorkspacePresence();

    /* Visual hierarchy + identity */
    z52VisualHierarchy();
    z52ApplyIdentity();

    /* Re-apply identity patches after tab switches (lazy-rendered content) */
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-tab], .nx-tab-btn, .nx-tab')) {
        setTimeout(z52ApplyIdentity, 150);
      }
    });

    console.log('[Phase Z52] Operational Product Experience active. Identity, toast governance, workspace presence.');
  }

  /* Defer 150ms to allow Z51 and all other deferred scripts to settle */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(z52Boot, 150));
  } else {
    setTimeout(z52Boot, 150);
  }

  /* Public API */
  window._z52 = {
    toast:          ToastGov,
    applyIdentity:  z52ApplyIdentity,
    auth:           z52Auth,
    presence:       z52WorkspacePresence,
  };

})();
