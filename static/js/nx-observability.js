/**
 * nx-observability.js — Nexora AGI Observability Panel v1
 * ══════════════════════════════════════════════════════════════════════
 * Renders live agent orchestration events into the right-side panel.
 *
 * Panel sections:
 *   1. Connection status strip
 *   2. Agent thought stream (agent.think events)
 *   3. Action timeline (agent.action events)
 *   4. HITL event indicator (hitl.required / hitl.resolved)
 *   5. Execution counters (actions, thoughts, errors)
 *
 * All data comes from NxBus — zero direct DOM manipulation from runtime.js.
 * CRITICAL: No synthetic/fake events. Only events from the real SSE stream.
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── DOM element IDs (must match index.html) ─────────────────────── */
  const IDs = {
    panel:          'nx-obs-panel',
    connDot:        'nx-obs-conn-dot',
    connLabel:      'nx-obs-conn-label',
    thinkFeed:      'nx-obs-think-feed',
    actionFeed:     'nx-obs-action-feed',
    counterThinks:  'nx-obs-count-thinks',
    counterActions: 'nx-obs-count-actions',
    counterErrors:  'nx-obs-count-errors',
    hitlBanner:     'nx-obs-hitl-banner',
    clearBtn:       'nx-obs-clear',
    sessionLabel:   'nx-obs-session-label',
  };

  /* ── State ───────────────────────────────────────────────────────── */
  let _counts = { thinks: 0, actions: 0, errors: 0 };
  let _currentSid = null;
  const MAX_ROWS = 200;  // cap DOM rows per feed to prevent overflow

  /* ── Helpers ─────────────────────────────────────────────────────── */
  function _el(id) { return document.getElementById(id); }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _tsNow() {
    const d = new Date();
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function _trimFeed(feedId) {
    const feed = _el(feedId);
    if (!feed) return;
    while (feed.children.length > MAX_ROWS) {
      feed.removeChild(feed.firstChild);
    }
  }

  function _scrollToBottom(feedId) {
    const feed = _el(feedId);
    if (feed) {
      requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
    }
  }

  function _incrementCounter(key) {
    _counts[key]++;
    const el = _el(IDs['counter' + key.charAt(0).toUpperCase() + key.slice(1)]);
    if (el) el.textContent = _counts[key];
  }

  /* ── Renderer helpers ────────────────────────────────────────────── */
  function _appendThought(text) {
    const feed = _el(IDs.thinkFeed);
    if (!feed) return;
    const row = document.createElement('div');
    row.className = 'nx-obs-thought-row';
    row.innerHTML =
      `<span class="nx-obs-ts">${_tsNow()}</span>` +
      `<span class="nx-obs-thought-text">${_esc(text)}</span>`;
    feed.appendChild(row);
    _trimFeed(IDs.thinkFeed);
    _scrollToBottom(IDs.thinkFeed);
    _incrementCounter('thinks');
  }

  function _appendAction(payload) {
    const feed = _el(IDs.actionFeed);
    if (!feed) return;
    const tool  = _esc(payload.tool || payload.action || 'unknown');
    const args  = payload.args || payload.arguments || {};
    const argsStr = typeof args === 'string'
      ? _esc(args.slice(0, 120))
      : _esc(JSON.stringify(args).slice(0, 120));
    const row = document.createElement('div');
    row.className = 'nx-obs-action-row';
    row.innerHTML =
      `<span class="nx-obs-ts">${_tsNow()}</span>` +
      `<span class="nx-obs-tool-badge">${tool}</span>` +
      (argsStr ? `<span class="nx-obs-args">${argsStr}</span>` : '');
    feed.appendChild(row);
    _trimFeed(IDs.actionFeed);
    _scrollToBottom(IDs.actionFeed);
    _incrementCounter('actions');
  }

  function _setConnStatus(connected, label) {
    const dot   = _el(IDs.connDot);
    const lbl   = _el(IDs.connLabel);
    if (dot) {
      dot.style.background = connected
        ? 'var(--green, #3fb950)'
        : 'var(--text-muted, #8b949e)';
      dot.style.boxShadow  = connected
        ? '0 0 6px var(--green, #3fb950)'
        : 'none';
    }
    if (lbl) lbl.textContent = label || (connected ? 'Live' : 'Offline');
  }

  function _showHitlBanner(payload) {
    const banner = _el(IDs.hitlBanner);
    if (!banner) return;
    const prompt = _esc((payload && payload.prompt) || 'Agent requires approval to continue.');
    const eid    = payload && payload.event_id;
    banner.innerHTML =
      `<div class="nx-obs-hitl-icon">⚠</div>` +
      `<div class="nx-obs-hitl-body">` +
        `<div class="nx-obs-hitl-label">HITL — Awaiting Approval</div>` +
        `<div class="nx-obs-hitl-prompt">${prompt}</div>` +
      `</div>` +
      `<div class="nx-obs-hitl-actions">` +
        `<button class="nx-obs-hitl-approve" onclick="NxHitlPanel.approve('${_esc(eid || '')}')">Approve</button>` +
        `<button class="nx-obs-hitl-reject"  onclick="NxHitlPanel.reject('${_esc(eid || '')}')">Reject</button>` +
      `</div>`;
    banner.style.display = '';
    banner.classList.add('nx-obs-hitl-active');
  }

  function _hideHitlBanner() {
    const banner = _el(IDs.hitlBanner);
    if (!banner) return;
    banner.style.display = 'none';
    banner.classList.remove('nx-obs-hitl-active');
    banner.innerHTML = '';
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  const NxObservability = {
    clear() {
      const tf = _el(IDs.thinkFeed);
      const af = _el(IDs.actionFeed);
      if (tf) tf.innerHTML = '<div class="nx-obs-empty">Waiting for agent events…</div>';
      if (af) af.innerHTML = '<div class="nx-obs-empty">No actions yet.</div>';
      _counts = { thinks: 0, actions: 0, errors: 0 };
      ['counterThinks', 'counterActions', 'counterErrors'].forEach(k => {
        const el = _el(IDs[k]);
        if (el) el.textContent = '0';
      });
      _hideHitlBanner();
    },

    bindSession(sid) {
      _currentSid = sid;
      this.clear();
      const lbl = _el(IDs.sessionLabel);
      if (lbl) lbl.textContent = sid ? sid.slice(0, 14) + '…' : '—';
    },
  };

  window.NxObservability = NxObservability;

  /* ── Bus wiring ──────────────────────────────────────────────────── */
  function _wire() {
    if (!window.NxBus) { setTimeout(_wire, 200); return; }
    const E = NxBus.EVENTS;

    // Stream events
    NxBus.on(E.STREAM_CHUNK, (d) => {
      if (!d) return;
      if (d.kind === 'think') {
        _appendThought(d.text || d.content || '');
        // Also pipe to the existing left-panel thought slots
        _pipeToLegacyThought(d.text || d.content || '');
      } else if (d.kind === 'action') {
        _appendAction(d);
      } else if (d.kind === 'output') {
        // outputs go to logs tab via ingestLogRow (already done in nx-sse-runtime)
      }
    }, { owner: 'nx-observability' });

    NxBus.on(E.STREAM_OPEN, () => {
      _setConnStatus(true, 'Live');
    }, { owner: 'nx-observability' });

    NxBus.on(E.STREAM_CLOSE, () => {
      _setConnStatus(false, 'Done');
    }, { owner: 'nx-observability' });

    NxBus.on(E.STREAM_ERROR, (d) => {
      _setConnStatus(false, 'Error');
      _incrementCounter('errors');
    }, { owner: 'nx-observability' });

    // WS / SSE connection state → conn status strip
    NxBus.on(E.WS_STATUS, (d) => {
      if (!d) return;
      const connected = d.state === 'connected';
      const label = {
        connected:    'Live',
        connecting:   'Connecting…',
        reconnecting: 'Reconnecting…',
        disconnected: 'Offline',
        error:        'Error',
      }[d.state] || d.state;
      _setConnStatus(connected, label);
    }, { owner: 'nx-observability' });

    // HITL events
    NxBus.on('nx:hitl:required', (d) => {
      _showHitlBanner(d);
    }, { owner: 'nx-observability' });

    NxBus.on('nx:hitl:resolved', () => {
      _hideHitlBanner();
    }, { owner: 'nx-observability' });

    // Session lifecycle
    NxBus.on(E.SESSION_CREATED, (d) => {
      if (d && d.sid) NxObservability.bindSession(d.sid);
    }, { owner: 'nx-observability' });

    NxBus.on(E.SESSION_RESTORED, (d) => {
      if (d && d.sid) NxObservability.bindSession(d.sid);
    }, { owner: 'nx-observability' });

    NxBus.on(E.SESSION_CLEARED, () => {
      NxObservability.bindSession(null);
      _setConnStatus(false, 'Offline');
    }, { owner: 'nx-observability' });

    NxBus.on(E.AGENT_START, () => {
      NxObservability.clear();
      _setConnStatus(false, 'Starting…');
    }, { owner: 'nx-observability' });

    NxBus.on(E.AGENT_DONE, () => {
      _setConnStatus(false, 'Done');
    }, { owner: 'nx-observability' });
  }

  /* ── Legacy bridge: pipe thought to existing left-panel slots ────── */
  function _pipeToLegacyThought(text) {
    const slot = document.getElementById('nxThoughtSlot');
    if (!slot || !text) return;
    // Remove placeholder if present
    const placeholder = slot.querySelector('.nx-obs-empty');
    if (placeholder) placeholder.remove();
    const item = document.createElement('div');
    item.className = 'nx-think-item';
    item.textContent = text;
    // Timestamp
    const ts = document.createElement('span');
    ts.className = 'nx-think-ts';
    ts.textContent = _tsNow();
    item.prepend(ts);
    slot.appendChild(item);
    // Cap at 50 items
    while (slot.querySelectorAll('.nx-think-item').length > 50) {
      const first = slot.querySelector('.nx-think-item');
      if (first) first.remove();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wire);
  } else {
    _wire();
  }

  /* ── Clear button wiring (set after DOM ready) ───────────────────── */
  document.addEventListener('DOMContentLoaded', () => {
    const btn = _el(IDs.clearBtn);
    if (btn) btn.addEventListener('click', () => NxObservability.clear());
  });

})();
