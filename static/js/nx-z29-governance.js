/**
 * nx-z29-governance.js — Phase Z29 Operator Control + Mission Governance UI
 *
 * Exposes window._z29 = { mount(container, sid), setSid(sid), refresh(), destroy() }
 *
 * Four panels:
 *   A. Mission Controls   — pause/resume/cancel/retry/inject/replan
 *   B. Governance Queue   — pending approvals, severity-classified
 *   C. Override Controls  — provider/model/retry/confidence/timeout/compression
 *   D. Recovery Surface   — stability score, anomalies, recovery actions
 */

(function () {
  'use strict';

  if (window._z29) return;

  /* ── State ─────────────────────────────────────────────────────────────── */
  var _sid = null;
  var _timers = [];
  var _busHandlers = [];
  var _mounted = false;
  var _root = null;
  var _toastWrap = null;

  /* ── Polling intervals ─────────────────────────────────────────────────── */
  var POLL_CONTROLS  = 5000;
  var POLL_QUEUE     = 6000;
  var POLL_OVERRIDES = 10000;
  var POLL_RECOVERY  = 8000;

  /* ── DOM refs ──────────────────────────────────────────────────────────── */
  var els = {};

  /* ── Utility ───────────────────────────────────────────────────────────── */
  function _ts(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000);
    return d.getHours().toString().padStart(2,'0') + ':' +
           d.getMinutes().toString().padStart(2,'0') + ':' +
           d.getSeconds().toString().padStart(2,'0');
  }

  function _ago(ts) {
    var s = Math.floor(Date.now()/1000 - ts);
    if (s < 60)  return s + 's ago';
    if (s < 3600) return Math.floor(s/60) + 'm ago';
    return Math.floor(s/3600) + 'h ago';
  }

  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function _toast(msg, type) {
    type = type || 'ok';
    if (!_toastWrap) return;
    var el = document.createElement('div');
    el.className = 'z29-toast z29-toast--' + type;
    el.textContent = msg;
    _toastWrap.appendChild(el);
    setTimeout(function () { el.remove(); }, 3500);
  }

  async function _api(method, url, body) {
    try {
      var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      var r = await fetch(url, opts);
      return await r.json();
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  /* ── HTML skeleton ─────────────────────────────────────────────────────── */
  function _buildHTML() {
    return [
      '<div class="z29-root">',

        /* Header */
        '<div class="z29-header">',
          '<span class="z29-header-title">Governance</span>',
          '<span class="z29-badge" id="z29-state-badge">—</span>',
          '<span class="z29-badge z29-badge--warn" id="z29-queue-badge" style="display:none">0 pending</span>',
          '<span class="z29-badge z29-badge--crit" id="z29-crit-badge" style="display:none">CRITICAL</span>',
        '</div>',

        /* Four-panel grid */
        '<div class="z29-grid">',

          /* A: Mission Controls */
          '<div class="z29-panel">',
            '<div class="z29-panel-head">&#9654; Mission Controls</div>',
            '<div class="z29-panel-body" id="z29-controls-body">',
              '<div class="z29-mission-state" id="z29-mission-state">',
                '<span class="z29-state-dot" id="z29-state-dot"></span>',
                '<span class="z29-state-label" id="z29-state-label">—</span>',
                '<span class="z29-state-sid" id="z29-state-sid">—</span>',
              '</div>',
              '<div class="z29-ctrl-grid">',
                '<button class="z29-btn z29-btn--pause"   id="z29-btn-pause"   onclick="_z29._ctrl(\'pause\')"  title="Pause execution"><span class="z29-btn-icon">&#9646;&#9646;</span>Pause</button>',
                '<button class="z29-btn z29-btn--resume"  id="z29-btn-resume"  onclick="_z29._ctrl(\'resume\')" title="Resume execution"><span class="z29-btn-icon">&#9654;</span>Resume</button>',
                '<button class="z29-btn z29-btn--cancel"  id="z29-btn-cancel"  onclick="_z29._ctrl(\'cancel\')" title="Cancel execution"><span class="z29-btn-icon">&#9632;</span>Cancel</button>',
                '<button class="z29-btn z29-btn--retry"   id="z29-btn-retry"   onclick="_z29._ctrl(\'retry\')"  title="Retry current step"><span class="z29-btn-icon">&#8635;</span>Retry</button>',
                '<button class="z29-btn z29-btn--replan"  id="z29-btn-replan"  onclick="_z29._ctrl(\'replan\')" title="Request replanning"><span class="z29-btn-icon">&#9741;</span>Replan</button>',
                '<button class="z29-btn z29-btn--inject"  id="z29-btn-inject"  onclick="_z29._focusInject()"   title="Send runtime instruction"><span class="z29-btn-icon">&#8628;</span>Inject</button>',
              '</div>',
              '<div class="z29-inject-row">',
                '<input class="z29-inject-input" id="z29-inject-input" placeholder="Runtime instruction…" ',
                  'onkeydown="if(event.key===\'Enter\')_z29._sendInject()">',
                '<button class="z29-inject-send" onclick="_z29._sendInject()">Send</button>',
              '</div>',
              '<div class="z29-action-log" id="z29-action-log"></div>',
            '</div>',
          '</div>',

          /* B: Governance Queue */
          '<div class="z29-panel">',
            '<div class="z29-panel-head">&#9654; Governance Queue</div>',
            '<div class="z29-panel-body" id="z29-queue-body">',
              '<div id="z29-queue-items"></div>',
            '</div>',
          '</div>',

          /* C: Override Controls */
          '<div class="z29-panel">',
            '<div class="z29-panel-head">&#9654; Runtime Overrides</div>',
            '<div class="z29-panel-body" id="z29-overrides-body">',
              '<div id="z29-override-form"></div>',
              '<div class="z29-override-apply-row">',
                '<button class="z29-apply-btn"     onclick="_z29._applyOverrides()">Apply Overrides</button>',
                '<button class="z29-clear-all-btn" onclick="_z29._clearOverrides()">Clear All</button>',
              '</div>',
            '</div>',
          '</div>',

          /* D: Recovery */
          '<div class="z29-panel">',
            '<div class="z29-panel-head">&#9654; Mission Recovery</div>',
            '<div class="z29-panel-body" id="z29-recovery-body">',
              '<div id="z29-stability-area"></div>',
              '<div id="z29-anomaly-list"></div>',
              '<div class="z29-recovery-btn-grid" id="z29-recovery-btns"></div>',
            '</div>',
          '</div>',

        '</div>', /* end grid */

        /* Toast container */
        '<div class="z29-toast-wrap" id="z29-toast-wrap"></div>',

      '</div>',
    ].join('');
  }

  /* ── Override form builder ─────────────────────────────────────────────── */
  var _OVERRIDE_FIELDS = [
    { key: 'provider',                  label: 'Provider',              type: 'text',  placeholder: 'e.g. openai' },
    { key: 'model',                     label: 'Model',                 type: 'text',  placeholder: 'e.g. gpt-4o' },
    { key: 'retry_budget',              label: 'Retry Budget',          type: 'number',placeholder: '1–20' },
    { key: 'confidence_threshold',      label: 'Confidence Threshold',  type: 'number',placeholder: '0.0–1.0' },
    { key: 'execution_timeout',         label: 'Step Timeout (s)',      type: 'number',placeholder: '5–600' },
    { key: 'compression_aggressiveness',label: 'Compression',           type: 'number',placeholder: '0.0–1.0' },
  ];

  function _buildOverrideForm(activeOverrides) {
    activeOverrides = activeOverrides || {};
    var html = [];
    _OVERRIDE_FIELDS.forEach(function (f) {
      var val = activeOverrides[f.key] !== undefined ? activeOverrides[f.key] : '';
      var active = val !== '' ? ' z29-override-input--active' : '';
      html.push(
        '<div class="z29-override-row">',
          '<span class="z29-override-label">' + _esc(f.label) + '</span>',
          '<input class="z29-override-input' + active + '" ',
            'id="z29-ov-' + f.key + '" ',
            'data-key="' + f.key + '" ',
            'type="' + f.type + '" ',
            'placeholder="' + _esc(f.placeholder) + '" ',
            'value="' + _esc(String(val)) + '">',
          '<button class="z29-override-clear" onclick="_z29._clearOverride(\'' + f.key + '\')" title="Clear override">&#215;</button>',
        '</div>'
      );
    });
    return html.join('');
  }

  /* ── Render functions ──────────────────────────────────────────────────── */

  function _renderMissionState(data) {
    if (!els.stateDot) return;
    var state = data.state || 'unknown';
    var signal = data.pending_signal || 'none';
    els.stateDot.className   = 'z29-state-dot z29-state-dot--' + state;
    els.stateLabel.textContent = state;
    els.stateSid.textContent  = (_sid || '—').slice(0, 14) + '…';

    var badge = document.getElementById('z29-state-badge');
    if (badge) {
      badge.textContent = state;
      badge.className = 'z29-badge' +
        (state === 'paused'    ? ' z29-badge--warn' :
         state === 'cancelled' || state === 'failed' ? ' z29-badge--crit' :
         state === 'running'   ? ' z29-badge--ok' : '');
    }

    // Enable/disable buttons based on state
    var pauseBtn  = document.getElementById('z29-btn-pause');
    var resumeBtn = document.getElementById('z29-btn-resume');
    if (pauseBtn)  pauseBtn.disabled  = (state !== 'running');
    if (resumeBtn) resumeBtn.disabled = (state !== 'paused');
  }

  function _renderActionLog(actions) {
    var el = document.getElementById('z29-action-log');
    if (!el || !actions) return;
    if (!actions.length) {
      el.innerHTML = '<div style="font-size:11px;color:#555;padding:4px 0">No operator actions yet</div>';
      return;
    }
    var html = [];
    var recent = actions.slice(-8).reverse();
    recent.forEach(function (a) {
      html.push(
        '<div class="z29-action-item">',
          '<span class="z29-action-type">' + _esc(a.action) + '</span>',
          '<span>' + _esc((a.operator_note || '').slice(0, 60)) + '</span>',
          '<span class="z29-action-time">' + _ts(a.ts) + '</span>',
        '</div>'
      );
    });
    el.innerHTML = html.join('');
  }

  function _renderQueue(data) {
    var items = (data && data.pending_items) ? data.pending_items : [];
    var pending = items;
    var qBadge = document.getElementById('z29-queue-badge');
    var cBadge = document.getElementById('z29-crit-badge');
    var critCount = (data && data.critical_count) || 0;

    if (qBadge) {
      if (pending.length > 0) {
        qBadge.textContent = pending.length + ' pending';
        qBadge.style.display = '';
      } else {
        qBadge.style.display = 'none';
      }
    }
    if (cBadge) {
      cBadge.style.display = critCount > 0 ? '' : 'none';
    }

    var el = document.getElementById('z29-queue-items');
    if (!el) return;
    if (!pending.length) {
      el.innerHTML = '<div class="z29-empty-state"><div class="z29-empty-icon">&#10003;</div>No pending approvals</div>';
      return;
    }

    var html = [];
    pending.forEach(function (req) {
      html.push(
        '<div class="z29-approval-item z29-approval-item--' + _esc(req.severity) + '" id="z29-req-' + _esc(req.request_id) + '">',
          '<div class="z29-approval-head">',
            '<span class="z29-sev-pill z29-sev-pill--' + _esc(req.severity) + '">' + _esc(req.severity.replace('_', ' ')) + '</span>',
            '<span class="z29-approval-op">' + _esc(req.op_type.replace(/_/g, ' ')) + '</span>',
            '<span class="z29-approval-age">' + _ago(req.created_at) + '</span>',
          '</div>',
          '<div class="z29-approval-sum">' + _esc((req.summary || '').slice(0, 100)) + '</div>',
          '<div class="z29-approval-actions">',
            '<button class="z29-approve-btn" onclick="_z29._resolve(\'' + req.request_id + '\',\'approve\')">&#10003; Approve</button>',
            '<button class="z29-reject-btn"  onclick="_z29._resolve(\'' + req.request_id + '\',\'reject\')">&#10007; Reject</button>',
          '</div>',
        '</div>'
      );
    });
    el.innerHTML = html.join('');

    // Pulse governance tab dot
    var dot = document.getElementById('nxGovernDot');
    if (dot && pending.length > 0) {
      dot.style.background = '#ff9944';
      dot.style.opacity    = '1';
    }
  }

  function _renderOverrides(data) {
    var el = document.getElementById('z29-override-form');
    if (!el) return;
    var active = (data && data.overrides) || {};
    el.innerHTML = _buildOverrideForm(active);
  }

  function _renderRecovery(data) {
    var health   = data && data.health    ? data.health   : {};
    var anomalies = data && data.anomalies ? data.anomalies : [];

    // Stability bar
    var stabEl = document.getElementById('z29-stability-area');
    if (stabEl) {
      var score    = health.stability_score !== undefined ? health.stability_score : 1.0;
      var scorePct = Math.round(score * 100);
      var barColor = score > 0.7 ? '#44cc88' : score > 0.4 ? '#ffb732' : '#ff5555';
      stabEl.innerHTML = [
        '<div class="z29-stability-bar-wrap">',
          '<div class="z29-stability-label">',
            '<span>Stability Score</span>',
            '<span class="z29-stability-score">' + scorePct + '%</span>',
          '</div>',
          '<div class="z29-stability-bar-bg">',
            '<div class="z29-stability-bar-fill" style="width:' + scorePct + '%;background:' + barColor + '"></div>',
          '</div>',
          (health.auto_paused ? '<div style="font-size:10px;color:#ffb732;margin-top:4px">&#9654; Auto-paused by stability monitor</div>' : ''),
        '</div>',
        '<div style="font-size:10px;color:#555;margin-bottom:6px">',
          'Retries: <b style="color:#e2e2e8">' + (health.consecutive_failures||0) + '</b> &nbsp;|&nbsp; ',
          'Loops: <b style="color:#e2e2e8">' + (health.loop_count||0) + '</b> &nbsp;|&nbsp; ',
          'Replans: <b style="color:#e2e2e8">' + (health.replan_count||0) + '</b>',
        '</div>',
      ].join('');
    }

    // Anomaly list
    var anomEl = document.getElementById('z29-anomaly-list');
    if (anomEl) {
      if (!anomalies.length) {
        anomEl.innerHTML = '<div style="font-size:11px;color:#555;margin-bottom:8px">No anomalies detected</div>';
      } else {
        var html = [];
        anomalies.forEach(function (a) {
          html.push(
            '<div class="z29-anomaly-item z29-anomaly-item--' + _esc(a.severity) + '">',
              '<span class="z29-anomaly-type">' + _esc((a.type||'').replace(/_/g,' ')) + '</span>',
              '<span class="z29-anomaly-detail">' + _esc((a.detail||'').slice(0,60)) + '</span>',
              '<span class="z29-anomaly-rec">' + _esc((a.recommended||'').replace(/_/g,' ')) + '</span>',
            '</div>'
          );
        });
        anomEl.innerHTML = html.join('');
      }
    }

    // Recovery action buttons
    var btnsEl = document.getElementById('z29-recovery-btns');
    if (btnsEl) {
      var recommended = (data && data.recommended_actions) ? data.recommended_actions : [];
      var allActions  = ['pause','checkpoint_resume','reduce_retries','switch_provider','compress_context','operator_review'];
      // Highlight recommended
      var html2 = [];
      allActions.forEach(function (a) {
        var highlight = recommended.indexOf(a) >= 0 ? ' style="border-color:#7c6af7;color:#bb99ff"' : '';
        html2.push(
          '<button class="z29-recovery-btn"' + highlight + ' onclick="_z29._recovery(\'' + a + '\')">' +
            a.replace(/_/g,' ') +
          '</button>'
        );
      });
      btnsEl.innerHTML = html2.join('');
    }
  }

  /* ── API calls ─────────────────────────────────────────────────────────── */

  async function _pollControls() {
    if (!_sid) return;
    var snap = await _api('GET', '/api/z29/mission/' + encodeURIComponent(_sid) + '/snapshot');
    if (snap && snap.ok !== false) {
      _renderMissionState(snap);
    }
    var actions = await _api('GET', '/api/z29/mission/' + encodeURIComponent(_sid) + '/actions?limit=8');
    if (actions && actions.actions) _renderActionLog(actions.actions);
  }

  async function _pollQueue() {
    var data = await _api('GET', '/api/z29/governance/queue');
    if (data) _renderQueue(data);
  }

  async function _pollOverrides() {
    if (!_sid) return;
    var data = await _api('GET', '/api/z29/overrides/' + encodeURIComponent(_sid));
    if (data) _renderOverrides(data);
  }

  async function _pollRecovery() {
    if (!_sid) return;
    var data = await _api('GET', '/api/z29/recovery/' + encodeURIComponent(_sid));
    if (data) _renderRecovery(data);
  }

  /* ── Control actions ───────────────────────────────────────────────────── */

  async function _ctrl(action) {
    if (!_sid) { _toast('No active session', 'warn'); return; }
    var note = { pause:'Operator paused', resume:'Operator resumed', cancel:'Operator cancelled',
                 retry:'Operator retry', replan:'Operator replan' }[action] || action;
    var res = await _api('POST', '/api/z29/mission/' + encodeURIComponent(_sid) + '/control',
                         { action: action, note: note });
    if (res && res.ok) {
      _toast(action.charAt(0).toUpperCase() + action.slice(1) + ' applied', 'ok');
      setTimeout(_pollControls, 300);
    } else {
      _toast('Error: ' + (res && res.error ? res.error : 'unknown'), 'err');
    }
  }

  function _focusInject() {
    var inp = document.getElementById('z29-inject-input');
    if (inp) { inp.focus(); inp.scrollIntoView({ block: 'nearest' }); }
  }

  async function _sendInject() {
    if (!_sid) { _toast('No active session', 'warn'); return; }
    var inp = document.getElementById('z29-inject-input');
    var instruction = inp ? inp.value.trim() : '';
    if (!instruction) { _toast('Enter instruction text', 'warn'); return; }
    var res = await _api('POST', '/api/z29/mission/' + encodeURIComponent(_sid) + '/control',
                         { action: 'inject', note: 'Operator inject', instruction: instruction });
    if (res && res.ok) {
      _toast('Instruction injected', 'ok');
      if (inp) inp.value = '';
      setTimeout(_pollControls, 300);
    } else {
      _toast('Inject error: ' + (res && res.error ? res.error : 'unknown'), 'err');
    }
  }

  /* ── Governance approval ───────────────────────────────────────────────── */

  async function _resolve(requestId, decision) {
    var res = await _api('POST', '/api/z29/governance/' + decision + '/' + encodeURIComponent(requestId),
                         { resolved_by: 'operator', resolution_note: decision });
    if (res && res.ok !== false) {
      _toast(decision.charAt(0).toUpperCase() + decision.slice(1) + 'd', decision === 'approve' ? 'ok' : 'warn');
      var reqEl = document.getElementById('z29-req-' + requestId);
      if (reqEl) reqEl.style.opacity = '0.3';
      setTimeout(_pollQueue, 500);
    } else {
      _toast('Error resolving request', 'err');
    }
  }

  /* ── Override apply ────────────────────────────────────────────────────── */

  async function _applyOverrides() {
    if (!_sid) { _toast('No active session', 'warn'); return; }
    var overrides = {};
    _OVERRIDE_FIELDS.forEach(function (f) {
      var inp = document.getElementById('z29-ov-' + f.key);
      if (inp && inp.value.trim() !== '') {
        var v = f.type === 'number' ? parseFloat(inp.value) : inp.value.trim();
        if (!isNaN(v) || f.type !== 'number') overrides[f.key] = v;
      }
    });
    if (!Object.keys(overrides).length) { _toast('No overrides to apply', 'warn'); return; }
    var res = await _api('POST', '/api/z29/overrides/' + encodeURIComponent(_sid),
                         { overrides: overrides, note: 'Operator override' });
    if (res && res.ok !== false) {
      _toast('Overrides applied (' + Object.keys(overrides).length + ')', 'ok');
      setTimeout(_pollOverrides, 300);
    } else {
      _toast('Override error: ' + (res && res.error ? res.error : 'unknown'), 'err');
    }
  }

  async function _clearOverride(key) {
    if (!_sid) return;
    await _api('DELETE', '/api/z29/overrides/' + encodeURIComponent(_sid) + '/' + encodeURIComponent(key));
    var inp = document.getElementById('z29-ov-' + key);
    if (inp) { inp.value = ''; inp.classList.remove('z29-override-input--active'); }
    _toast('Override \'' + key + '\' cleared', 'ok');
  }

  async function _clearOverrides() {
    if (!_sid) return;
    await _api('DELETE', '/api/z29/overrides/' + encodeURIComponent(_sid));
    _toast('All overrides cleared', 'ok');
    setTimeout(_pollOverrides, 300);
  }

  /* ── Recovery action ───────────────────────────────────────────────────── */

  async function _recovery(action) {
    if (!_sid) { _toast('No active session', 'warn'); return; }
    var res = await _api('POST', '/api/z29/recovery/' + encodeURIComponent(_sid) + '/action',
                         { action: action, params: {} });
    if (res && res.ok) {
      _toast('Recovery: ' + action.replace(/_/g,' '), 'ok');
      setTimeout(_pollRecovery, 500);
      setTimeout(_pollControls, 500);
    } else {
      _toast('Recovery error: ' + (res && res.error ? res.error : 'unknown'), 'err');
    }
  }

  /* ── NxBus wiring ──────────────────────────────────────────────────────── */

  function _wireBus() {
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_wireBus, 300); return; }

    function _on(ev, fn) {
      NxBus.on(ev, fn);
      _busHandlers.push({ ev: ev, fn: fn });
    }

    _on('nx:z29:mission_control', function (payload) {
      if (payload && payload.sid !== _sid) return;
      setTimeout(_pollControls, 200);
    });

    _on('nx:z29:governance', function () {
      setTimeout(_pollQueue, 200);
    });

    _on('nx:z29:override', function (payload) {
      if (payload && payload.sid !== _sid) return;
      setTimeout(_pollOverrides, 200);
    });

    _on('nx:z29:stability', function (payload) {
      if (payload && payload.sid !== _sid) return;
      setTimeout(_pollRecovery, 200);
      // Pulse tab dot
      var dot = document.getElementById('nxGovernDot');
      if (dot && payload && payload.auto_paused) {
        dot.style.background = '#ff5555';
        dot.style.opacity    = '1';
      }
    });

    _on(NxBus.EVENTS.SESSION_CREATED, function (e) {
      if (e && e.session_id) _z29.setSid(e.session_id);
    });

    _on(NxBus.EVENTS.SESSION_RESTORED, function (e) {
      if (e && e.sid) _z29.setSid(e.sid);
    });
  }

  /* ── Timer management ──────────────────────────────────────────────────── */

  function _startPolling() {
    _timers.push(setInterval(_pollControls,  POLL_CONTROLS));
    _timers.push(setInterval(_pollQueue,     POLL_QUEUE));
    _timers.push(setInterval(_pollOverrides, POLL_OVERRIDES));
    _timers.push(setInterval(_pollRecovery,  POLL_RECOVERY));
  }

  function _stopPolling() {
    _timers.forEach(clearInterval);
    _timers = [];
  }

  /* ── Public API ────────────────────────────────────────────────────────── */

  var _z29 = {
    _ctrl:         _ctrl,
    _focusInject:  _focusInject,
    _sendInject:   _sendInject,
    _resolve:      _resolve,
    _applyOverrides: _applyOverrides,
    _clearOverride:  _clearOverride,
    _clearOverrides: _clearOverrides,
    _recovery:     _recovery,

    mount: function (container, sid) {
      if (_mounted) return;
      _mounted = true;
      _sid     = sid || null;

      container.innerHTML = _buildHTML();
      _root     = container.querySelector('.z29-root');
      _toastWrap = document.getElementById('z29-toast-wrap');

      // Grab DOM refs
      els.stateDot   = document.getElementById('z29-state-dot');
      els.stateLabel = document.getElementById('z29-state-label');
      els.stateSid   = document.getElementById('z29-state-sid');

      // Build override form skeleton
      _renderOverrides(null);

      // Wire NxBus
      _wireBus();

      // Initial render with idle state
      _renderMissionState({ state: 'unknown', pending_signal: 'none' });
      _renderQueue({ pending_items: [], critical_count: 0 });
      _renderRecovery({});

      // Start polling
      _startPolling();

      // Immediate first poll
      if (_sid) {
        _pollControls();
        _pollOverrides();
        _pollRecovery();
      }
      _pollQueue();
    },

    setSid: function (sid) {
      _sid = sid;
      if (!_mounted) return;
      if (els.stateSid) els.stateSid.textContent = (sid || '—').slice(0, 14) + '…';
      _pollControls();
      _pollOverrides();
      _pollRecovery();
    },

    refresh: function () {
      _pollControls();
      _pollQueue();
      _pollOverrides();
      _pollRecovery();
    },

    destroy: function () {
      _stopPolling();
      _busHandlers.forEach(function (h) {
        if (window.NxBus) NxBus.off(h.ev, h.fn);
      });
      _busHandlers = [];
      _mounted = false;
    },
  };

  window._z29 = _z29;

})();
