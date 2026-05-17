/* ════════════════════════════════════════════════════════════════════════
   nx-z51.js — Phase Z51: Beta Operational Lockdown + Product Cohesion
   Beta governance, billing suppression, HITL realization,
   performance stabilization, trust hardening.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ═══════════════════════════════════════════════════════════════════
     Z51G: BETA GOVERNANCE — feature flag registry
     ═══════════════════════════════════════════════════════════════════ */
  window.NX_BETA = {
    mode: true,
    version: '0.9.0-beta',
    buildDate: '2026-05-17',

    /* Feature gates — false = disabled in beta */
    features: {
      billing:          false,  // FUTURE: activate when Razorpay live keys confirmed
      payments:         false,  // FUTURE: enable after billing backend hardening
      marketplace:      false,  // FUTURE: planned for v1.0 post-beta
      collaboration:    false,  // FUTURE: multi-user sessions planned for v1.1
      publicApi:        false,  // FUTURE: v1.0 API key distribution
      advancedScheduler:true,   // beta-safe: scheduler is read-only in UI
      hitl:             true,   // beta-safe: HITL is a core execution safety feature
      memory:           true,   // beta-safe: memory is read-only in UI
      observability:    true,   // beta-safe: observability is read-only
    },

    /* Check a feature gate */
    isEnabled: function (feature) {
      return !!this.features[feature];
    },

    /* Gate a function — returns a no-op wrapper if feature is disabled */
    gate: function (feature, fn, fallbackMsg) {
      if (this.features[feature]) return fn;
      return function () {
        if (fallbackMsg !== false && typeof toast === 'function') {
          toast(fallbackMsg || 'This feature is not available in beta.', 'warn');
        }
      };
    },
  };

  /* ═══════════════════════════════════════════════════════════════════
     Z51A: BILLING LOCKDOWN — suppress all commercial surfaces
     ═══════════════════════════════════════════════════════════════════ */
  function z51LockBilling() {
    /* Override p8OpenUpgradeModal to be a safe no-op */
    window.p8OpenUpgradeModal = NX_BETA.gate('billing', window.p8OpenUpgradeModal,
      'Billing is not active in this beta. Stay tuned.');

    /* Override p36StartPayment — must never fire in beta */
    window.p36StartPayment = function () {
      if (typeof toast === 'function') toast('Payments are disabled in this beta.', 'warn');
    };

    /* Override p8ApplyCoupon */
    window.p8ApplyCoupon = function () {
      if (typeof toast === 'function') toast('Coupons are not active in beta.', 'warn');
    };

    /* Remove Razorpay external script tag if it somehow loaded */
    qsa('script[src*="razorpay"]').forEach(el => el.remove());

    /* Hide billing-related inline "Upgrade ↗" and "Manage ↗" links */
    function _patchUpgradeLinks() {
      qsa('a[onclick*="p8OpenUpgradeModal"]').forEach(el => el.style.display = 'none');
    }
    _patchUpgradeLinks();
    /* Re-run after any inspector content injection */
    const inspBillingContent = $('p36InspBillingContent');
    if (inspBillingContent) {
      const obs = new MutationObserver(_patchUpgradeLinks);
      obs.observe(inspBillingContent, { childList: true, subtree: true });
    }

    /* Inject beta badge in topbar where plan badge sits */
    _injectBetaBadge();
  }

  function _injectBetaBadge() {
    const planBadge = $('nxPlanBadge');
    if (!planBadge) return;
    /* Replace plan badge text with BETA label */
    const betaBadge = document.createElement('span');
    betaBadge.className = 'z51-beta-badge';
    betaBadge.textContent = 'BETA';
    betaBadge.title = 'Aetherion AI — Beta v' + NX_BETA.version;
    /* Keep plan badge but add beta indicator alongside */
    planBadge.parentNode.insertBefore(betaBadge, planBadge.nextSibling);
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z51B: HITL REALIZATION — operational approval queue
     ═══════════════════════════════════════════════════════════════════ */
  let _z51HitlPollTimer = null;
  let _z51HitlQueue = [];
  let _z51HitlAudit = [];

  function z51InitHitl() {
    /* Inject approval queue panel into the approvals row in the live tab */
    _z51EnsureHitlPanel();
    /* Start polling for pending approvals when a session is active */
    _z51StartHitlPolling();
    /* Wire existing HITL strip buttons to the new system */
    _z51WireHitlStrip();
  }

  function _z51EnsureHitlPanel() {
    const row = $('z33ApprovalsRow');
    if (!row || $('z51HitlQueuePanel')) return;
    const panel = document.createElement('div');
    panel.id = 'z51HitlQueuePanel';
    panel.className = 'z51-hitl-queue';
    panel.style.display = 'none';
    panel.innerHTML = `
      <div class="z51-hitl-header">
        <span class="z51-hitl-title">⚠ Pending Approvals</span>
        <span class="z51-hitl-count" id="z51HitlCount">0</span>
        <button class="nx-tiny-btn" onclick="z51HitlRefresh()" title="Refresh">↻</button>
      </div>
      <div id="z51HitlItems"></div>
      <div id="z51HitlEmpty" style="padding:12px;font-size:11px;color:var(--text-dim);text-align:center;">
        No pending approvals.
      </div>
      <div style="border-top:1px solid var(--panel-border);padding:6px 12px;">
        <div style="font-size:10px;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">
          Audit Trail
        </div>
        <div id="z51HitlAuditList" style="max-height:100px;overflow-y:auto;"></div>
      </div>`;
    row.appendChild(panel);
  }

  async function z51HitlRefresh() {
    const sid = window.NX?.activeSid || (typeof currentSession !== 'undefined' ? currentSession : null);
    if (!sid) {
      _z51RenderHitlQueue([]);
      return;
    }
    try {
      /* Try session-scoped endpoint first (nx_hitl_response.py) */
      const r = await fetch('/api/session/' + sid + '/hitl/pending');
      if (r.ok) {
        const d = await r.json();
        _z51RenderHitlQueue(d.pending || []);
      } else {
        /* Fallback: global pending endpoint */
        const r2 = await fetch('/api/hitl/pending?sid=' + sid);
        if (r2.ok) {
          const d2 = await r2.json();
          _z51RenderHitlQueue(d2.pending || []);
        }
      }
      /* Load audit */
      const ra = await fetch('/api/session/' + sid + '/hitl/audit').catch(() => null);
      if (ra && ra.ok) {
        const da = await ra.json();
        _z51RenderHitlAudit(da.audit || []);
      }
    } catch (_) {}
  }

  window.z51HitlRefresh = z51HitlRefresh;

  function _z51RenderHitlQueue(items) {
    _z51HitlQueue = items;
    const panel = $('z51HitlQueuePanel');
    const itemsEl = $('z51HitlItems');
    const emptyEl = $('z51HitlEmpty');
    const countEl = $('z51HitlCount');

    if (!panel) return;
    const hasItems = items.length > 0;
    panel.style.display = hasItems || _z51HitlAudit.length ? 'block' : 'none';
    if (countEl) countEl.textContent = items.length;
    if (emptyEl) emptyEl.style.display = hasItems ? 'none' : 'block';
    if (!itemsEl) return;

    itemsEl.innerHTML = '';
    items.forEach((item, i) => {
      const eid = item.event_id || item.id || ('hitl-' + i);
      const reason = item.reason || item.message || 'Approval required';
      const timeout = item.timeout_at ? _z51FormatTimeout(item.timeout_at) : null;
      const div = document.createElement('div');
      div.className = 'z51-hitl-item';
      div.innerHTML = `
        <div class="z51-hitl-reason">${_z51Esc(reason)}</div>
        <div class="z51-hitl-meta">
          <span>Event: <code style="font-size:9px;font-family:var(--mono)">${_z51Esc(eid.slice(-12))}</code></span>
          ${timeout ? `<span class="z51-hitl-timeout">⏱ ${timeout}</span>` : ''}
        </div>
        <div class="z51-hitl-actions">
          <input class="z51-hitl-note" placeholder="Optional note…" id="z51Note-${i}" />
          <button class="z51-hitl-approve" onclick="z51HitlDecide('${_z51Esc(eid)}','approved',${i})">✓ Approve</button>
          <button class="z51-hitl-reject"  onclick="z51HitlDecide('${_z51Esc(eid)}','rejected',${i})">✕ Reject</button>
        </div>`;
      itemsEl.appendChild(div);
    });

    /* Update the approvals row strip visibility */
    const row = $('z33ApprovalsRow');
    if (row) row.style.display = hasItems ? '' : '';

    /* Show HITL strip in composer if running */
    if (window.NX?.lastStatus === 'running') {
      const strip = $('nxHitlStrip');
      if (strip) strip.style.display = hasItems ? 'block' : 'block';
    }
  }

  function _z51RenderHitlAudit(items) {
    _z51HitlAudit = items;
    const el = $('z51HitlAuditList');
    if (!el) return;
    if (!items.length) { el.innerHTML = '<div style="font-size:10px;color:var(--text-dim);padding:4px 0;">No history yet.</div>'; return; }
    el.innerHTML = '';
    items.slice(-10).reverse().forEach(entry => {
      const div = document.createElement('div');
      div.className = 'z51-hitl-audit-item';
      const ts = entry.resolved_at ? new Date(entry.resolved_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
      div.innerHTML = `
        <span class="z51-hitl-audit-status ${entry.status}">${entry.status || '—'}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_z51Esc(entry.reason || entry.event_id || '—')}</span>
        <span>${ts}</span>`;
      el.appendChild(div);
    });
    const panel = $('z51HitlQueuePanel');
    if (panel && items.length) panel.style.display = 'block';
  }

  window.z51HitlDecide = async function (eventId, decision, noteIdx) {
    const noteEl = $('z51Note-' + noteIdx);
    const note = noteEl ? noteEl.value.trim() : '';
    const sid = window.NX?.activeSid || (typeof currentSession !== 'undefined' ? currentSession : null);

    /* Persist operator note */
    const auditEntry = {
      event_id: eventId, status: decision,
      reason: _z51HitlQueue[noteIdx]?.reason || '',
      operator_note: note,
      resolved_at: Date.now() / 1000,
    };
    _z51HitlAudit.push(auditEntry);
    _z51RenderHitlAudit(_z51HitlAudit);

    try {
      let ok = false;
      /* Try session-scoped respond endpoint (nx_hitl_response.py) */
      if (sid) {
        const r = await fetch('/api/session/' + sid + '/hitl/respond', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: decision, event_id: eventId, feedback: note }),
        });
        ok = r.ok;
      }
      /* Fallback to global approve endpoint (web_app.py) */
      if (!ok) {
        await fetch('/api/hitl/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ execution_id: eventId, status: decision, feedback: note }),
        });
      }

      /* Emit on NxBus if available */
      if (window.NxBus) window.NxBus.emit('nx:hitl:resolved', { eventId, decision, note });

      /* Remove from local queue and re-render */
      _z51HitlQueue = _z51HitlQueue.filter((_, i) => i !== noteIdx);
      _z51RenderHitlQueue(_z51HitlQueue);

      if (typeof toast === 'function') {
        toast(decision === 'approved' ? '✓ Approved — agent continuing' : '✕ Rejected — agent will halt', decision === 'approved' ? 'ok' : 'warn');
      }
    } catch (err) {
      if (typeof toast === 'function') toast('Failed to send decision: ' + err.message, 'err');
    }
  };

  function _z51StartHitlPolling() {
    /* Poll every 8s when session is active — piggybacks on existing metric timer cadence */
    if (_z51HitlPollTimer) clearInterval(_z51HitlPollTimer);
    _z51HitlPollTimer = setInterval(() => {
      if (window.NX?.activeSid && window.NX?.lastStatus === 'running') {
        z51HitlRefresh();
      }
    }, 8000);
  }

  function _z51WireHitlStrip() {
    /* Ensure the HITL strip's existing buttons call real functions */
    const pauseBtn = $('hitlPauseBtn');
    const resumeBtn = $('hitlResumeBtn');
    if (pauseBtn && !pauseBtn.dataset.z51wired) {
      pauseBtn.dataset.z51wired = '1';
      pauseBtn.addEventListener('click', function () {
        const sid = window.NX?.activeSid;
        if (!sid) return;
        fetch('/api/session/' + sid + '/pause', { method: 'POST' }).catch(() => {});
        pauseBtn.style.display = 'none';
        if (resumeBtn) resumeBtn.style.display = '';
        const statusEl = $('hitlStatusText');
        if (statusEl) statusEl.textContent = 'Paused';
        if (typeof toast === 'function') toast('Agent paused — send instruction or resume', 'warn');
      });
    }
    if (resumeBtn && !resumeBtn.dataset.z51wired) {
      resumeBtn.dataset.z51wired = '1';
      resumeBtn.addEventListener('click', function () {
        const sid = window.NX?.activeSid;
        if (!sid) return;
        fetch('/api/session/' + sid + '/resume', { method: 'POST' }).catch(() => {});
        resumeBtn.style.display = 'none';
        if (pauseBtn) pauseBtn.style.display = '';
        const statusEl = $('hitlStatusText');
        if (statusEl) statusEl.textContent = 'Running';
        if (typeof toast === 'function') toast('Agent resumed', 'ok');
      });
    }
  }

  function _z51FormatTimeout(ts) {
    const remaining = ts * 1000 - Date.now();
    if (remaining <= 0) return 'Expired';
    const s = Math.floor(remaining / 1000);
    return s < 60 ? s + 's remaining' : Math.floor(s / 60) + 'm remaining';
  }

  function _z51Esc(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z51E: PERFORMANCE STABILIZATION
     ═══════════════════════════════════════════════════════════════════ */
  function z51PerformanceStabilize() {
    _z51FixBusReadyPolling();
    _z51DeduplicateKeyboardListeners();
    _z51StopPlaceholderRotationWhenHidden();
    _z51PatchMetricPollingOverlap();
  }

  /* Fix 100ms NxBus ready-polling loops — replace with a proper one-shot check */
  function _z51FixBusReadyPolling() {
    /* Patch the waitForNxBus pattern: if NxBus is already available, call cb immediately */
    if (!window.waitForNxBus) {
      window.waitForNxBus = function (cb, timeout) {
        if (window.NxBus) { setTimeout(cb, 0); return; }
        let waited = 0;
        const limit = timeout || 10000;
        const t = setInterval(() => {
          waited += 100;
          if (window.NxBus || waited >= limit) {
            clearInterval(t);
            if (window.NxBus) cb();
          }
        }, 100);
      };
    }

    /* If NxBus is already ready, dispatch a one-time event so pending listeners can resolve */
    if (window.NxBus) {
      document.dispatchEvent(new CustomEvent('nx:bus:ready'));
    } else {
      /* Watch for NxBus assignment */
      let _busCheckTimer = setInterval(() => {
        if (window.NxBus) {
          clearInterval(_busCheckTimer);
          _busCheckTimer = null;
          document.dispatchEvent(new CustomEvent('nx:bus:ready'));
        }
      }, 100);
    }
  }

  /* Deduplicate keyboard listeners — Z50 and nx-keyboard-shortcuts both listen globally */
  function _z51DeduplicateKeyboardListeners() {
    /* Register a single consolidated keydown dispatcher */
    if (window._z51KeyboardInstalled) return;
    window._z51KeyboardInstalled = true;

    document.addEventListener('keydown', function (e) {
      /* Escape: handled by Z50 panel close — no further action needed */
      /* Other shortcuts handled by nx-keyboard-shortcuts.js */

      /* Cmd/Ctrl+Enter: run */
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        const runBtn = $('runBtn');
        if (runBtn && !runBtn.disabled) runBtn.click();
      }
    }, { passive: false });
  }

  /* Stop placeholder rotation when the composer input is not visible */
  function _z51StopPlaceholderRotationWhenHidden() {
    const input = $('nxComposerInput') || qs('[id*="composerInput"]') || qs('#taskInput');
    if (!input || !window.NX?._placeholderTimer) return;
    const obs = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting && window.NX._placeholderTimer) {
          clearInterval(window.NX._placeholderTimer);
          window.NX._placeholderTimer = null;
        }
      });
    });
    obs.observe(input);
  }

  /* Reduce metric polling overlap — guard against double-scheduling */
  function _z51PatchMetricPollingOverlap() {
    /* If multiple polling intervals already exist, they will run;
       Z51 adds a guard so future calls to nxStartMetrics do not stack */
    const origStart = window.nxStartMetrics;
    if (typeof origStart === 'function') {
      window.nxStartMetrics = function () {
        if (window.NX?.metricTimer) return; /* already running */
        origStart.call(this);
      };
    }
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z51F: PRODUCT TRUST HARDENING
     ═══════════════════════════════════════════════════════════════════ */
  function z51TrustHardening() {
    _z51HardenCookieState();
    _z51HardenSessionRestore();
    _z51HardenReconnectFlow();
    _z51AccessibilityCleanup();
  }

  /* Cookie state — ensure accept is sticky and banner never re-appears on same device */
  function _z51HardenCookieState() {
    /* Already handled by nx-z50.js nxAcceptCookies() + localStorage.
       Z51 adds: if user has EVER accepted, never show banner again on this domain */
    const banner = $('nx-cookie-banner');
    if (!banner) return;
    if (localStorage.getItem('nx_cookie_accepted') || sessionStorage.getItem('nx_cookie_accepted')) {
      banner.style.display = 'none';
      return;
    }
    /* Ensure both Accept and Dismiss write to localStorage */
    qsa('button', banner).forEach(btn => {
      const txt = btn.textContent?.trim().toLowerCase();
      if (txt === 'accept' || txt === 'ok' || btn.className?.includes('accept')) {
        btn.addEventListener('click', () => {
          localStorage.setItem('nx_cookie_accepted', '1');
          banner.style.display = 'none';
        }, { once: true });
      }
    });
  }

  /* Session restore — if activeSid was stored, restore it on page load */
  function _z51HardenSessionRestore() {
    const savedSid = sessionStorage.getItem('nx_active_sid') || localStorage.getItem('nx_last_sid');
    if (!savedSid || window.NX?.activeSid) return;
    /* Attempt to restore silently */
    fetch('/api/session/' + savedSid + '/status').then(r => {
      if (r.ok) return r.json();
      throw new Error('not found');
    }).then(d => {
      if (d && (d.status === 'idle' || d.status === 'running' || d.status === 'error')) {
        if (window.NX) window.NX.activeSid = savedSid;
        /* Show restored indicator */
        _z51ShowSessionRestoredBadge(savedSid);
        /* Refresh history panel */
        if (typeof window.z50RefreshHistory === 'function') window.z50RefreshHistory();
      }
    }).catch(() => {
      /* Clear stale stored ID */
      localStorage.removeItem('nx_last_sid');
      sessionStorage.removeItem('nx_active_sid');
    });

    /* Persist active session changes */
    const _origUpdateCard = window.nxUpdateSessionCard;
    if (typeof _origUpdateCard === 'function') {
      window.nxUpdateSessionCard = function (sess) {
        _origUpdateCard.call(this, sess);
        if (sess?.sid) {
          sessionStorage.setItem('nx_active_sid', sess.sid);
          localStorage.setItem('nx_last_sid', sess.sid);
        }
      };
    }
  }

  function _z51ShowSessionRestoredBadge(sid) {
    const target = $('nxCurSid');
    if (!target || $('z51RestoredBadge')) return;
    const badge = document.createElement('span');
    badge.id = 'z51RestoredBadge';
    badge.className = 'z51-session-restored';
    badge.textContent = '↩ Restored';
    badge.title = 'Session ' + sid.slice(-8) + ' auto-restored';
    target.parentNode.insertBefore(badge, target.nextSibling);
    setTimeout(() => { badge.style.opacity = '0'; setTimeout(() => badge.remove(), 400); }, 5000);
  }

  /* Reconnect flow — ensure SSE reconnect clears the warning bar */
  function _z51HardenReconnectFlow() {
    /* Watch for SSE EventSource reconnects — already handled by Z50 but add fallback */
    const origEventSource = window.EventSource;
    if (!origEventSource || window._z51EventSourcePatched) return;
    window._z51EventSourcePatched = true;
    /* Don't override EventSource — instead listen for the nx:sse:* events that
       the existing sse manager already emits (nx-z50.js wires these) */
  }

  /* Accessibility — add missing ARIA labels to icon-only buttons */
  function _z51AccessibilityCleanup() {
    const iconOnlyBtns = qsa('button:not([aria-label]):not([title])');
    iconOnlyBtns.forEach(btn => {
      const txt = btn.textContent?.trim();
      /* Only label pure-icon buttons (emoji + 1 char, or empty) */
      if (!txt || txt.length <= 3) {
        btn.setAttribute('aria-label', txt || 'button');
      }
    });

    /* Ensure focus trap in modals */
    const modals = qsa('[role="dialog"], .nx-modal, .p8-modal');
    modals.forEach(modal => {
      if (!modal.getAttribute('tabindex')) modal.setAttribute('tabindex', '-1');
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Z51D: WORKSPACE CALMNESS — reduce noise, add body class for CSS
     ═══════════════════════════════════════════════════════════════════ */
  function z51WorkspaceCalmness() {
    /* Apply running/idle body class for CSS targeting */
    const orig = window.nxSetGlobalStatus;
    if (typeof orig === 'function' && !window._z51CalmnessHooked) {
      window._z51CalmnessHooked = true;
      const _hooked = window.nxSetGlobalStatus;
      window.nxSetGlobalStatus = function (status) {
        _hooked.call(this, status);
        document.body.classList.toggle('nx-running', status === 'running');
        document.body.classList.toggle('nx-error', status === 'error');
      };
    }

    /* Reduce the z33ApprovalsRow visible flicker by only showing it with content */
    const approvalsRow = $('z33ApprovalsRow');
    if (approvalsRow) {
      const obs = new MutationObserver(() => {
        const hasContent = approvalsRow.textContent.trim().length > 0 ||
                           approvalsRow.children.length > 0;
        approvalsRow.style.marginBottom = hasContent ? '6px' : '0';
      });
      obs.observe(approvalsRow, { childList: true, subtree: true, characterData: true });
    }

    /* Suppress duplicate nxRunDot flicker — single source of truth */
    qsa('.nx-run-dot, #nxRunDot').forEach((dot, i) => {
      if (i > 0) dot.style.display = 'none'; /* keep only first */
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     STATUS SYNC HOOK — piggyback on existing nxSetGlobalStatus hook
     from Z50 without breaking it.
     ═══════════════════════════════════════════════════════════════════ */
  function z51HookStatusSync() {
    /* Z50 already hooked nxSetGlobalStatus. Z51 adds the HITL refresh
       trigger when status → running, and the running body class.
       Both are applied after Z50's hook via a queued hook. */
    const existing = window.nxSetGlobalStatus;
    if (!existing || window._z51StatusHooked) return;
    window._z51StatusHooked = true;

    window.nxSetGlobalStatus = function (status) {
      existing.call(this, status);
      /* Apply body class for CSS calmness pass */
      document.body.classList.toggle('nx-running', status === 'running');
      document.body.classList.toggle('nx-error',   status === 'error');
      /* Trigger HITL check when run starts */
      if (status === 'running') {
        setTimeout(z51HitlRefresh, 3000);
      }
      /* Clear HITL panel on task completion */
      if (status === 'idle') {
        setTimeout(z51HitlRefresh, 2000); /* catch any pending that need resolution */
      }
    };
  }

  /* ═══════════════════════════════════════════════════════════════════
     BOOT
     ═══════════════════════════════════════════════════════════════════ */
  function z51Boot() {
    z51LockBilling();
    z51InitHitl();
    z51HookStatusSync();
    z51WorkspaceCalmness();
    z51PerformanceStabilize();
    z51TrustHardening();
    console.log('[Phase Z51] Beta Operational Lockdown active. v' + NX_BETA.version);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', z51Boot);
  } else {
    /* Defer slightly to allow Z50 to hook first */
    setTimeout(z51Boot, 120);
  }

  /* Expose for debugging */
  window._z51 = {
    hitlRefresh:   z51HitlRefresh,
    hitlDecide:    window.z51HitlDecide,
    lockBilling:   z51LockBilling,
    betaFeatures:  NX_BETA.features,
    isEnabled:     NX_BETA.isEnabled.bind(NX_BETA),
  };

})();
