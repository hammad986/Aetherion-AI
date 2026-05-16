/**
 * nx-trust.js — Nexora Trusted Execution Intelligence v1
 * ═══════════════════════════════════════════════════════════════════
 * Confidence model, retry intelligence, validation evidence,
 * failure explanation, HITL escalation, and memory continuity.
 *
 * All output surfaces:
 *   - Inspector causality panel (primary)
 *   - Execution chunk annotations (inline, via NxChunker)
 *   - HITL escalation surface (targeted DOM injection)
 *
 * Zero rewrites to backend, runtime, or SSE logic.
 * ═══════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  /* ══════════════════════════════════════════════════════════════════
     CONFIDENCE MODEL
     ══════════════════════════════════════════════════════════════════ */
  const _conf = {
    current:     1.0,    // 0–1 float
    baseline:    1.0,
    retryCount:  0,
    errorCount:  0,
    sessionId:   null,
  };

  function _adjustConf(delta) {
    _conf.current = Math.max(0, Math.min(1.0, _conf.current + delta));
    _renderConfBar();
  }

  function _resetConf() {
    _conf.current    = 1.0;
    _conf.baseline   = 1.0;
    _conf.retryCount = 0;
    _conf.errorCount = 0;
    _renderConfBar();
  }

  function _renderConfBar() {
    const bar  = $('nxTrustConfBar');
    const label = $('nxTrustConfLabel');
    if (!bar && !label) return;

    const pct   = Math.round(_conf.current * 100);
    const color = pct >= 80 ? '#3fb950' : pct >= 50 ? '#f59e0b' : '#f85149';

    if (bar) {
      bar.style.width      = pct + '%';
      bar.style.background = color;
    }
    if (label) {
      label.textContent  = pct + '%';
      label.style.color  = color;
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     TRUSTED MEMORY — tracks flaky patterns across session
     ══════════════════════════════════════════════════════════════════ */
  const _memory = {
    flakyFiles:       {},   // path → fail count
    recurringErrors:  {},   // error signature → count
    retryLoops:       {},   // action signature → count
    recoveryAttempts: 0,
  };

  function _recordFileIssue(path) {
    if (!path) return;
    _memory.flakyFiles[path] = (_memory.flakyFiles[path] || 0) + 1;
  }

  function _recordError(sig) {
    if (!sig) return;
    const key = sig.slice(0, 60);
    _memory.recurringErrors[key] = (_memory.recurringErrors[key] || 0) + 1;
    if (_memory.recurringErrors[key] >= 2) {
      _appendInspectorNote('pattern', `Recurring error: "${key.slice(0,50)}" (${_memory.recurringErrors[key]}x)`);
    }
  }

  function _recordRetry(action) {
    if (!action) return;
    const key = action.slice(0, 60);
    _memory.retryLoops[key] = (_memory.retryLoops[key] || 0) + 1;
  }

  function _isFlakyFile(path) {
    return (_memory.flakyFiles[path] || 0) >= 2;
  }

  /* ══════════════════════════════════════════════════════════════════
     VALIDATION EVIDENCE
     ══════════════════════════════════════════════════════════════════ */
  const EVIDENCE_PATTERNS = [
    { rx: /syntax (ok|valid|passed|error|invalid)/i,  label: 'Syntax',   ok: d => /ok|valid|passed/i.test(d) },
    { rx: /test[s]? (pass|fail|skip)/i,               label: 'Tests',    ok: d => /pass/i.test(d) },
    { rx: /endpoint reachable|200 OK|connected/i,     label: 'Endpoint', ok: () => true },
    { rx: /import error|module not found/i,           label: 'Imports',  ok: () => false },
    { rx: /runtime (healthy|ok|error|crash)/i,        label: 'Runtime',  ok: d => /healthy|ok/i.test(d) },
    { rx: /build (success|fail|complete)/i,           label: 'Build',    ok: d => /success|complete/i.test(d) },
    { rx: /retry|attempt \d+/i,                       label: 'Retry',    ok: () => false },
    { rx: /validation (pass|fail|complete)/i,         label: 'Validation', ok: d => /pass|complete/i.test(d) },
  ];

  function _detectEvidence(text) {
    const found = [];
    EVIDENCE_PATTERNS.forEach(p => {
      const m = p.rx.exec(text);
      if (m) {
        const ok = p.ok(m[0]);
        found.push({ label: p.label, ok, match: m[0] });
        if (!ok) _adjustConf(-0.06);
        else      _adjustConf(+0.02);
      }
    });
    return found;
  }

  function _renderEvidencePills(evidence) {
    if (!evidence.length) return '';
    return '<div class="nx-trust-pills">' +
      evidence.map(e =>
        `<span class="nx-trust-pill nx-trust-pill--${e.ok ? 'ok' : 'fail'}">${e.label}</span>`
      ).join('') +
    '</div>';
  }

  /* ══════════════════════════════════════════════════════════════════
     RETRY INTELLIGENCE
     ══════════════════════════════════════════════════════════════════ */
  let _retryChain      = null;   // active retry group element
  let _retryChainTimer = null;
  let _retryChainCount = 0;
  const MAX_RETRIES_BEFORE_ESCALATION = 4;

  function _handleRetry(text, kind) {
    _conf.retryCount++;
    _retryChainCount++;
    _conf.current = Math.max(0, _conf.current - 0.08);
    _renderConfBar();
    _recordRetry(text);

    _appendInspectorNote('retry',
      `Retry ${_retryChainCount}: ${text.slice(0,120)}` +
      ` — Confidence: ${Math.round(_conf.current * 100)}%`
    );

    if (_retryChainCount >= MAX_RETRIES_BEFORE_ESCALATION) {
      _appendInspectorNote('escalation',
        `Escalation threshold reached (${_retryChainCount} retries). ` +
        `Operator review may be needed.`
      );
    }

    // Reset chain if no retry seen for 8s
    clearTimeout(_retryChainTimer);
    _retryChainTimer = setTimeout(() => {
      _retryChainCount = 0;
      _retryChain = null;
    }, 8000);
  }

  /* ══════════════════════════════════════════════════════════════════
     FAILURE EXPLANATION
     ══════════════════════════════════════════════════════════════════ */
  function _explainFailure(payload) {
    const err  = payload.error || payload.text || 'Unknown error';
    const hint = _diagnose(err);
    _conf.errorCount++;
    _adjustConf(-0.15);
    _recordError(err);

    _appendInspectorNote('escalation',
      `Execution stopped: ${err.slice(0,200)}` +
      (hint ? `\n\nProbable cause: ${hint.cause}. Suggested: ${hint.action}` : '')
    );
  }

  const DIAGNOSTIC_MAP = [
    { rx: /module not found|no module named/i,
      cause: 'Missing Python dependency',         action: 'Check requirements.txt and install missing packages' },
    { rx: /permission denied/i,
      cause: 'File system permission error',       action: 'Verify file ownership and execution permissions' },
    { rx: /connection refused|ECONNREFUSED/i,
      cause: 'Service not reachable',             action: 'Confirm the target service is running and port is correct' },
    { rx: /syntax error/i,
      cause: 'Syntax error in generated code',    action: 'Review the last modified file for syntax issues' },
    { rx: /out of memory|MemoryError/i,
      cause: 'Insufficient memory',               action: 'Reduce batch size or increase available memory' },
    { rx: /timeout|timed out/i,
      cause: 'Operation exceeded time limit',     action: 'Check network latency or increase timeout threshold' },
    { rx: /api key|unauthorized|401/i,
      cause: 'Authentication failure',            action: 'Verify API key configuration in environment settings' },
    { rx: /rate limit|429/i,
      cause: 'API rate limit reached',            action: 'Wait before retrying or switch to fallback model' },
  ];

  function _diagnose(errText) {
    for (const d of DIAGNOSTIC_MAP) {
      if (d.rx.test(errText)) return d;
    }
    return null;
  }

  /* ══════════════════════════════════════════════════════════════════
     HITL ESCALATION SURFACE
     ══════════════════════════════════════════════════════════════════ */
  function _renderHitl(payload) {
    const panel = $('nxInspectorContent');
    if (!panel) return;

    const risk = payload.risk || 'medium';
    const riskColor = { high: '#f85149', medium: '#f59e0b', low: '#3fb950' }[risk] || '#8b949e';

    // Remove any previous HITL card
    const prev = panel.querySelector('.nx-hitl-card');
    if (prev) prev.remove();

    const card = document.createElement('div');
    card.className = 'nx-hitl-card';
    card.dataset.eventId = payload.event_id || '';
    card.innerHTML = `
      <div class="nx-hitl-header">
        <span class="nx-hitl-badge" style="border-color:${riskColor};color:${riskColor}">
          ${risk.toUpperCase()} RISK
        </span>
        <span class="nx-hitl-title">Operator Approval Required</span>
      </div>
      <div class="nx-hitl-prompt">${_esc(payload.prompt || 'Action requires verification')}</div>
      ${payload.blocked_by ? `<div class="nx-hitl-dep">Blocked by: <code>${_esc(payload.blocked_by)}</code></div>` : ''}
      <div class="nx-hitl-conf">Confidence dropped to ${Math.round(_conf.current * 100)}%</div>
      <div class="nx-hitl-actions">
        <button class="nx-hitl-btn nx-hitl-approve" onclick="nxTrustApprove('${payload.event_id}')">
          Approve
        </button>
        <button class="nx-hitl-btn nx-hitl-reject" onclick="nxTrustReject('${payload.event_id}')">
          Reject
        </button>
        <button class="nx-hitl-btn nx-hitl-inspect" onclick="nxToggleInspector()">
          Review Trace
        </button>
      </div>
    `;

    panel.insertBefore(card, panel.firstChild);

    // Auto-open inspector if closed
    const inspPanel = $('nxInspectorPanel');
    if (inspPanel && !inspPanel.classList.contains('is-open')) {
      inspPanel.classList.add('is-open');
    }
    _adjustConf(-0.12);
  }

  /* ── Global HITL action handlers ── */
  window.nxTrustApprove = function(eventId) {
    _removeHitlCard(eventId);
    _adjustConf(+0.1);
    _appendInspectorNote('validation', 'Operator approved. Execution continuing.');
    if (window.hitlApprove) hitlApprove(eventId);
    else if (window.NxBus) NxBus.emit('nx:hitl:action', { event_id: eventId, action: 'approve' });
  };

  window.nxTrustReject = function(eventId) {
    _removeHitlCard(eventId);
    _adjustConf(-0.15);
    _appendInspectorNote('escalation', 'Operator rejected action. Execution halted.');
    if (window.hitlReject) hitlReject(eventId);
    else if (window.NxBus) NxBus.emit('nx:hitl:action', { event_id: eventId, action: 'reject' });
  };

  function _removeHitlCard(eventId) {
    const card = document.querySelector(`.nx-hitl-card[data-event-id="${eventId}"]`);
    if (card) card.remove();
  }

  /* ══════════════════════════════════════════════════════════════════
     INSPECTOR NOTE WRITER (confidence-aware)
     ══════════════════════════════════════════════════════════════════ */
  const KIND_META = {
    retry:      { label: 'Retry',      color: '#f59e0b' },
    escalation: { label: 'Escalation', color: '#f85149' },
    validation: { label: 'Validated',  color: '#3fb950' },
    pattern:    { label: 'Pattern',    color: '#79c0ff' },
    trust:      { label: 'Trust',      color: '#bc8cff' },
  };

  function _appendInspectorNote(kind, text) {
    const panel = $('nxInspectorContent');
    if (!panel) return;

    const m = KIND_META[kind] || { label: kind, color: '#8b949e' };

    const note = document.createElement('div');
    note.className = 'nx-inspector-section';
    note.innerHTML = `
      <div class="nx-inspector-section-label" style="color:${m.color}">${m.label}</div>
      <div class="nx-inspector-section-body" style="white-space:pre-wrap">${_esc(text)}</div>
    `;
    panel.insertBefore(note, panel.firstChild);

    // Cap sections
    const all = panel.querySelectorAll('.nx-inspector-section');
    if (all.length > 14) all[all.length - 1].remove();
  }

  function _esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  /* ══════════════════════════════════════════════════════════════════
     TRUST SIGNAL FROM BACKEND
     ══════════════════════════════════════════════════════════════════ */
  function _handleTrustSignal(payload) {
    const type       = payload.type       || 'verify';
    const confidence = payload.confidence ?? null;
    const message    = payload.message    || '';
    const verified   = payload.verified   ?? null;

    if (confidence != null) {
      _conf.current = confidence;
      _renderConfBar();
    }

    const label = verified === true ? 'Verified'
      : verified === false ? 'Unverified'
      : 'Trust Signal';

    _appendInspectorNote('trust',
      `[${type.toUpperCase()}] ${message.slice(0, 200)}` +
      (confidence != null ? ` — Confidence: ${Math.round(confidence * 100)}%` : '')
    );
  }

  /* ══════════════════════════════════════════════════════════════════
     CONFIDENCE BAR INJECTION
     ══════════════════════════════════════════════════════════════════ */
  function _injectConfBar() {
    const header = document.querySelector('.nx-inspector-header');
    if (!header || $('nxTrustConfBarWrap')) return;

    const wrap = document.createElement('div');
    wrap.id = 'nxTrustConfBarWrap';
    wrap.className = 'nx-trust-conf-wrap';
    wrap.innerHTML = `
      <span class="nx-trust-conf-lbl">CONFIDENCE</span>
      <div class="nx-trust-conf-track">
        <div class="nx-trust-conf-fill" id="nxTrustConfBar" style="width:100%;background:#3fb950;"></div>
      </div>
      <span id="nxTrustConfLabel" style="color:#3fb950;">100%</span>
    `;
    header.appendChild(wrap);
  }

  /* ══════════════════════════════════════════════════════════════════
     NxBus WIRING
     ══════════════════════════════════════════════════════════════════ */
  let _initialized = false;

  function _wire() {
    if (_initialized) return;
    if (!window.NxBus || !NxBus.EVENTS) { setTimeout(_wire, 200); return; }
    _initialized = true;

    _injectConfBar();

    const E = NxBus.EVENTS;

    /* ── Stream chunks: detect evidence + retry patterns ── */
    NxBus.on(E.STREAM_CHUNK, (d) => {
      const text = d.text || d.output || '';
      if (!text) return;

      // Detect evidence
      const evidence = _detectEvidence(text);
      if (evidence.length && window.NxChunker) {
        // NxChunker renders the chunk; we just emit an annotated event
        // via inspector (non-invasive)
        const pills = evidence.map(e => `${e.label}:${e.ok ? 'OK' : 'FAIL'}`).join(' | ');
        if (evidence.some(e => !e.ok)) {
          _appendInspectorNote('validation', `Evidence: ${pills}`);
        }
      }

      // Detect retry signals
      if (/retry|attempt \d+|retrying/i.test(text)) {
        _handleRetry(text, d.kind);
      }

      // Detect recovery
      if (/recover|fallback|switching to/i.test(text)) {
        _memory.recoveryAttempts++;
        _appendInspectorNote('validation', `Recovery attempt ${_memory.recoveryAttempts}: ${text.slice(0,120)}`);
      }

    }, { owner: 'nx-trust' });

    /* ── Trust signals from backend ── */
    NxBus.on('nx:trust:signal', _handleTrustSignal, { owner: 'nx-trust' });

    /* ── HITL escalation ── */
    NxBus.on('nx:hitl:required', (d) => {
      _adjustConf(-0.10);
      _renderHitl(d);
    }, { owner: 'nx-trust' });

    /* ── File changed: mark flaky files ── */
    NxBus.on(E.FILE_CHANGED, (d) => {
      if (d.status === 'failed' || d.action === 'deleted') {
        _recordFileIssue(d.path);
        if (_isFlakyFile(d.path)) {
          _appendInspectorNote('pattern',
            `Unstable file: ${d.path} has failed ${_memory.flakyFiles[d.path]}x`);
        }
      }
    }, { owner: 'nx-trust' });

    /* ── Errors ── */
    NxBus.on(E.STREAM_ERROR, _explainFailure, { owner: 'nx-trust' });
    NxBus.on(E.AGENT_ERROR,  _explainFailure, { owner: 'nx-trust' });

    /* ── Done: restore confidence ── */
    NxBus.on(E.AGENT_DONE, (d) => {
      if (d && d.confidence != null) {
        _conf.current = d.confidence;
        _renderConfBar();
        _appendInspectorNote('validation',
          `Execution complete. Final confidence: ${Math.round(d.confidence * 100)}%` +
          (d.completed_steps ? ` — ${d.completed_steps}/${d.total_steps || '?'} steps` : '')
        );
      }
      _retryChainCount = 0;
    }, { owner: 'nx-trust' });

    /* ── Session reset ── */
    NxBus.on(E.SESSION_CLEARED, () => {
      _resetConf();
      _memory.recoveryAttempts = 0;
    }, { owner: 'nx-trust' });

  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 400));
  } else {
    setTimeout(_wire, 400);
  }

  window.NxTrust = { getConf: () => _conf.current, memory: () => _memory };

})();
