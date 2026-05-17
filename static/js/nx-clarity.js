/**
 * nx-clarity.js — Aetherion Operator Clarity & First-Run Experience v1
 * ═══════════════════════════════════════════════════════════════════
 * Phase Q: First-run onboarding, execution transparency,
 * human-readable HITL wording, recovery messaging,
 * lightweight local analytics, and deployment readiness banner.
 *
 * No new systems — surgical additions to existing surfaces.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const STORAGE_KEY_ONBOARD = 'nx_onboard_done_v1';
  const STORAGE_KEY_ANALYTICS = 'nx_analytics_v1';

  /* ══════════════════════════════════════════════════════════════════
     1. LIGHTWEIGHT LOCAL ANALYTICS
     — localStorage only. No external calls. Operator-only.
     ══════════════════════════════════════════════════════════════════ */
  const NxAnalytics = (() => {
    function _load() {
      try { return JSON.parse(localStorage.getItem(STORAGE_KEY_ANALYTICS) || '{}'); } catch(_) { return {}; }
    }
    function _save(d) {
      try { localStorage.setItem(STORAGE_KEY_ANALYTICS, JSON.stringify(d)); } catch(_) {}
    }
    function track(event, extra) {
      const d = _load();
      if (!d[event]) d[event] = { count: 0, last: 0 };
      d[event].count++;
      d[event].last = Date.now();
      if (extra) d[event].last_detail = String(extra).slice(0, 60);
      _save(d);
    }
    function report() {
      const d = _load();
      return Object.entries(d).map(([k, v]) => ({
        event: k, count: v.count,
        last: new Date(v.last).toLocaleString(),
        detail: v.last_detail || ''
      })).sort((a, b) => b.count - a.count);
    }
    function reset() { localStorage.removeItem(STORAGE_KEY_ANALYTICS); }
    return { track, report, reset };
  })();

  window.NxAnalytics = NxAnalytics;

  /* ══════════════════════════════════════════════════════════════════
     2. BUS WIRING FOR ANALYTICS
     ══════════════════════════════════════════════════════════════════ */
  function _wireAnalytics() {
    if (!window.NxBus) { setTimeout(_wireAnalytics, 200); return; }

    NxBus.on('nx:agent:start',   () => NxAnalytics.track('task_started'),           { owner: 'nx-clarity' });
    NxBus.on('nx:agent:done',    () => NxAnalytics.track('task_completed'),         { owner: 'nx-clarity' });
    NxBus.on('nx:agent:stop',    () => NxAnalytics.track('task_stopped'),           { owner: 'nx-clarity' });
    NxBus.on('nx:stream:error',  () => NxAnalytics.track('stream_error'),           { owner: 'nx-clarity' });
    NxBus.on('nx:hitl:required', () => NxAnalytics.track('hitl_escalation'),        { owner: 'nx-clarity' });
    NxBus.on('nx:ws:status',     (d) => {
      if (d.state === 'reconnecting') NxAnalytics.track('reconnect_attempt');
    }, { owner: 'nx-clarity' });
    NxBus.on('nx:tab:change',    (d) => NxAnalytics.track('tab_used', d.tab),       { owner: 'nx-clarity' });
    NxBus.on('nx:session:restored', () => NxAnalytics.track('session_restored'),   { owner: 'nx-clarity' });

    // Inspector open/close
    const insp = $('nxInspectorPanel');
    if (insp) {
      new MutationObserver(() => {
        if (insp.classList.contains('is-open')) NxAnalytics.track('inspector_opened');
      }).observe(insp, { attributes: true, attributeFilter: ['class'] });
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     3. FIRST-RUN WALKTHROUGH
     — Shows a non-blocking tip strip on first visit.
       Dismisses permanently after operator completes or skips.
     ══════════════════════════════════════════════════════════════════ */
  const _TIPS = [
    { icon: '▶', title: 'Define a task', body: 'Type what you want built in the composer below. Be specific — the agent plans and executes autonomously.' },
    { icon: '⬡', title: 'Watch execution', body: 'The Output tab shows the agent\'s live reasoning, actions, and results grouped by phase.' },
    { icon: '◈', title: 'Review trust signals', body: 'The inspector (Ctrl+\\) shows confidence and validation evidence. Green = verified. Amber = needs review.' },
    { icon: '⏸', title: 'Intervene when needed', body: 'If the agent escalates, an approval card appears. You can approve, reject, or review the trace.' },
    { icon: '⌨', title: 'Keyboard shortcuts', body: 'Ctrl+1-4: switch tabs · Ctrl+\\ inspector · Ctrl+. stop · Ctrl+Shift+D diagnostics' },
  ];

  let _tipIndex = 0;
  let _tipPanel = null;

  function _initFirstRun() {
    if (localStorage.getItem(STORAGE_KEY_ONBOARD)) return;
    _showTip(0);
    NxAnalytics.track('first_run_started');
  }

  function _showTip(idx) {
    const tip = _TIPS[idx];
    if (!tip) { _dismissOnboard(); return; }
    _tipIndex = idx;

    if (!_tipPanel) {
      _tipPanel = document.createElement('div');
      _tipPanel.id = 'nxOnboardTip';
      _tipPanel.className = 'nx-onboard-tip';
      document.body.appendChild(_tipPanel);
    }

    _tipPanel.innerHTML = `
      <div class="nx-tip-icon">${tip.icon}</div>
      <div class="nx-tip-body">
        <div class="nx-tip-title">${tip.title}</div>
        <div class="nx-tip-text">${tip.body}</div>
      </div>
      <div class="nx-tip-nav">
        <span class="nx-tip-counter">${idx + 1} / ${_TIPS.length}</span>
        <button class="nx-tip-btn" id="nxTipNext">${idx < _TIPS.length - 1 ? 'Next' : 'Start'}</button>
        <button class="nx-tip-skip" id="nxTipSkip">Skip</button>
      </div>
    `;
    _tipPanel.classList.add('visible');

    $('nxTipNext').onclick = () => _showTip(idx + 1);
    $('nxTipSkip').onclick = _dismissOnboard;
  }

  function _dismissOnboard() {
    if (_tipPanel) { _tipPanel.classList.remove('visible'); }
    localStorage.setItem(STORAGE_KEY_ONBOARD, '1');
    NxAnalytics.track('first_run_completed');
  }

  /* ══════════════════════════════════════════════════════════════════
     4. EXECUTION TRANSPARENCY — human-readable chunk labels
     — Replaces terse internal kind names with operator-friendly labels
       injected as a data-label attribute on each chunk header.
     ══════════════════════════════════════════════════════════════════ */
  const _KIND_LABELS = {
    think:       'Analyzing',
    action:      'Executing',
    tool_success:'Completed',
    validation:  'Validating',
    recovery:    'Recovering',
    escalation:  'Awaiting approval',
    output:      'Output',
    plan:        'Planning',
    result:      'Result',
  };

  function _labelChunks() {
    document.querySelectorAll('.nx-exec-chunk[data-kind]').forEach(ch => {
      if (ch.dataset.labeled) return;
      const kind  = ch.dataset.kind;
      const label = _KIND_LABELS[kind] || kind;
      const hdr   = ch.querySelector('.nx-chunk-header, .nx-chunk-label');
      if (hdr && !hdr.querySelector('.nx-kind-label')) {
        const span = document.createElement('span');
        span.className = 'nx-kind-label';
        span.textContent = label;
        hdr.insertBefore(span, hdr.firstChild);
      }
      ch.dataset.labeled = '1';
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     5. HITL WORDING IMPROVEMENT
     — Patches escalation card wording at render time.
       NxTrust renders the card; we intercept and clean the copy.
     ══════════════════════════════════════════════════════════════════ */
  function _patchHitlWording() {
    const insp = $('nxInspectorContent');
    if (!insp) return;

    new MutationObserver((muts) => {
      muts.forEach(m => {
        m.addedNodes.forEach(node => {
          if (!(node instanceof HTMLElement)) return;
          const card = node.classList?.contains('nx-hitl-card') ? node
            : node.querySelector?.('.nx-hitl-card');
          if (!card) return;

          // Humanize the escalation title
          const title = card.querySelector('.nx-hitl-title, h4, strong');
          if (title && title.textContent.includes('HITL')) {
            title.textContent = 'Agent needs your decision';
          }

          // Humanize generic "Approve / Reject" to contextual wording
          const btns = card.querySelectorAll('button, .nx-hitl-btn');
          btns.forEach(btn => {
            const t = btn.textContent.trim().toLowerCase();
            if (t === 'approve') btn.textContent = 'Proceed';
            if (t === 'reject')  btn.textContent = 'Cancel action';
          });

          NxAnalytics.track('hitl_card_shown');
        });
      });
    }).observe(insp, { childList: true, subtree: true });
  }

  /* ══════════════════════════════════════════════════════════════════
     6. FAILURE / RECOVERY MESSAGING — human-readable banners
     ══════════════════════════════════════════════════════════════════ */
  function _wireRecoveryMessages() {
    if (!window.NxBus) return;

    NxBus.on('nx:stream:error', (d) => {
      _showStatusBanner('error', 'Something went wrong. The agent is attempting to recover automatically.');
    }, { owner: 'nx-clarity' });

    NxBus.on('nx:agent:done', (d) => {
      if (d && d.status === 'failed') {
        _showStatusBanner('warn', 'Mission could not be completed. Check the trace for details.');
      } else {
        _clearStatusBanner();
      }
    }, { owner: 'nx-clarity' });

    NxBus.on('nx:ws:status', (d) => {
      if (d.state === 'reconnecting') {
        _showStatusBanner('warn', 'Connection lost — attempting to reconnect. Your session is preserved.');
      } else if (d.state === 'connected') {
        _clearStatusBanner();
      }
    }, { owner: 'nx-clarity' });

    NxBus.on('nx:session:restored', () => {
      _showStatusBanner('ok', 'Session restored. Continuing from last checkpoint.');
      setTimeout(_clearStatusBanner, 4000);
    }, { owner: 'nx-clarity' });
  }

  let _statusBanner = null;
  function _showStatusBanner(type, msg) {
    if (!_statusBanner) {
      _statusBanner = document.createElement('div');
      _statusBanner.id = 'nxStatusBanner';
      _statusBanner.className = 'nx-status-banner';
      const shell = document.querySelector('.nx-shell-root') || document.body;
      shell.appendChild(_statusBanner);
    }
    const colors = { error: '#f85149', warn: '#f59e0b', ok: '#3fb950' };
    _statusBanner.style.borderLeftColor = colors[type] || '#484f58';
    _statusBanner.textContent = msg;
    _statusBanner.classList.add('visible');
  }
  function _clearStatusBanner() {
    if (_statusBanner) _statusBanner.classList.remove('visible');
  }

  /* ══════════════════════════════════════════════════════════════════
     7. DEPLOYMENT READINESS BANNER
     — Checks critical env signals on page load and shows
       a non-blocking operator warning if something is wrong.
     ══════════════════════════════════════════════════════════════════ */
  function _checkDeploymentReadiness() {
    // Hit /api/health to get readiness signal from backend
    fetch('/api/health', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => {
        if (d.status === 'degraded' || d.warnings?.length) {
          const msg = d.warnings?.[0] || 'Deployment warnings detected — check /api/health';
          _showStatusBanner('warn', msg);
          NxAnalytics.track('deployment_warning', msg);
        }
      })
      .catch(() => {
        // /api/health not found or offline — silent (common in dev)
      });
  }

  /* ══════════════════════════════════════════════════════════════════
     8. CHUNK LABELING OBSERVER
     ══════════════════════════════════════════════════════════════════ */
  function _initChunkLabeling() {
    const timeline = document.querySelector('#nxTab-logs, #nxExecutionStream');
    if (!timeline) return;

    _labelChunks(); // label existing
    new MutationObserver(_labelChunks)
      .observe(timeline, { childList: true, subtree: true });
  }

  /* ══════════════════════════════════════════════════════════════════
     INIT
     ══════════════════════════════════════════════════════════════════ */
  function _init() {
    _wireAnalytics();
    _wireRecoveryMessages();
    _initChunkLabeling();
    _patchHitlWording();
    _checkDeploymentReadiness();

    // First-run: small delay so workspace renders first
    setTimeout(_initFirstRun, 1200);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_init, 800));
  } else {
    setTimeout(_init, 800);
  }

  window.NxClarity = { analytics: NxAnalytics };

})();
