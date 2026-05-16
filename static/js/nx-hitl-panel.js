/**
 * nx-hitl-panel.js — Nexora HITL Approval/Rejection Panel v1
 * ══════════════════════════════════════════════════════════════════════
 * Handles operator approval/rejection of blocked agent threads.
 * Wired to POST /api/hitl/approve (backend added during hardening phase).
 *
 * Integrates with NxObservability for UI state and NxBus for events.
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  let _pendingEventId = null;
  let _pendingSession = null;

  function _getSession() {
    return _pendingSession
      || (window.currentSession)
      || null;
  }

  function _setBusy(busy) {
    const approveBtn = document.getElementById('nx-hitl-approve-btn');
    const rejectBtn  = document.getElementById('nx-hitl-reject-btn');
    if (approveBtn) approveBtn.disabled = busy;
    if (rejectBtn)  rejectBtn.disabled  = busy;
  }

  function _showResult(msg, ok) {
    const res = document.getElementById('nx-hitl-result');
    if (!res) return;
    res.textContent = msg;
    res.style.color = ok ? 'var(--green, #3fb950)' : 'var(--red, #f85149)';
    res.style.display = '';
    setTimeout(() => { if (res) res.style.display = 'none'; }, 4000);
  }

  async function _sendApproval(action, eventId, sessionId, comment) {
    const sid = sessionId || _getSession();
    if (!sid) {
      _showResult('No active session.', false);
      return;
    }
    _setBusy(true);
    try {
      const body = { action, session_id: sid };
      if (eventId)  body.event_id = eventId;
      if (comment)  body.comment  = comment;

      const res = await fetch('/api/hitl/approve', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));

      if (res.ok && (data.ok !== false)) {
        _showResult(action === 'approve' ? '✓ Approved — agent resumed.' : '✓ Rejected.', true);
        if (window.NxBus) {
          NxBus.emit('nx:hitl:resolved', { event_id: eventId, action, session_id: sid });
        }
        _pendingEventId = null;
      } else {
        const err = (data && (data.error || data.message)) || 'Request failed.';
        _showResult('✗ ' + err, false);
      }
    } catch (err) {
      _showResult('✗ Network error: ' + err.message, false);
    } finally {
      _setBusy(false);
    }
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  const NxHitlPanel = {
    /** Called by observability banner buttons with event ID from SSE payload */
    approve(eventId) {
      const eid = eventId || _pendingEventId;
      const comment = _getInlineComment();
      _sendApproval('approve', eid, _pendingSession, comment);
    },

    reject(eventId) {
      const eid = eventId || _pendingEventId;
      const comment = _getInlineComment();
      _sendApproval('reject', eid, _pendingSession, comment);
    },

    /** Called from the HITL strip in the left panel */
    approveFromStrip() {
      const input = document.getElementById('nxHitlInput');
      const comment = input ? input.value.trim() : '';
      _sendApproval('approve', _pendingEventId, _pendingSession, comment);
    },

    rejectFromStrip() {
      const input = document.getElementById('nxHitlInput');
      const comment = input ? input.value.trim() : '';
      _sendApproval('reject', _pendingEventId, _pendingSession, comment);
    },
  };

  function _getInlineComment() {
    const inp = document.getElementById('nx-hitl-comment-input')
      || document.getElementById('nxHitlInput');
    return inp ? inp.value.trim() : '';
  }

  window.NxHitlPanel = NxHitlPanel;

  /* ── Bus wiring ──────────────────────────────────────────────────── */
  function _wire() {
    if (!window.NxBus) { setTimeout(_wire, 200); return; }

    NxBus.on('nx:hitl:required', (d) => {
      if (!d) return;
      _pendingEventId = d.event_id || null;
      _pendingSession = d.session_id || null;

      // Show the HITL strip in the left panel (existing UI)
      const strip = document.getElementById('nxHitlStrip');
      if (strip) strip.style.display = '';

      const statusText = document.getElementById('hitlStatusText');
      if (statusText) {
        statusText.textContent = 'Waiting for approval…';
        statusText.style.color = 'var(--yellow, #d29922)';
      }
      const dot = document.getElementById('hitlDot');
      if (dot) {
        dot.style.background = 'var(--yellow, #d29922)';
        dot.style.boxShadow  = '0 0 6px var(--yellow, #d29922)';
      }
    }, { owner: 'nx-hitl-panel' });

    NxBus.on('nx:hitl:resolved', () => {
      _pendingEventId = null;
      const statusText = document.getElementById('hitlStatusText');
      if (statusText) {
        statusText.textContent = 'Resolved';
        statusText.style.color = 'var(--green, #3fb950)';
      }
      const dot = document.getElementById('hitlDot');
      if (dot) {
        dot.style.background = 'var(--green, #3fb950)';
        dot.style.boxShadow  = 'none';
      }
    }, { owner: 'nx-hitl-panel' });

    // Hook existing hitlPause / hitlResume buttons to the new API
    // (preserve backward compat — these functions may already exist)
    if (typeof window.hitlPause !== 'function') {
      window.hitlPause = () => {
        const btn = document.getElementById('hitlPauseBtn');
        const res = document.getElementById('hitlResumeBtn');
        if (btn) btn.style.display = 'none';
        if (res) res.style.display = '';
        // Signal pause to the backend (cancel = soft stop)
        const sid = _getSession();
        if (sid) {
          fetch('/api/stop', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ session_id: sid }),
          }).catch(() => {});
        }
      };
    }

    if (typeof window.hitlResume !== 'function') {
      window.hitlResume = () => {
        const btn = document.getElementById('hitlPauseBtn');
        const res = document.getElementById('hitlResumeBtn');
        if (btn) btn.style.display = '';
        if (res) res.style.display = 'none';
        // Resume via approve (no specific event ID = resume current)
        _sendApproval('approve', _pendingEventId, _pendingSession, '');
      };
    }

    if (typeof window.hitlInject !== 'function') {
      window.hitlInject = () => {
        const el = document.getElementById('hitlInjectInput')
          || document.getElementById('nxHitlInput');
        if (!el || !el.value.trim()) return;
        const comment = el.value.trim();
        _sendApproval('approve', _pendingEventId, _pendingSession, comment);
        el.value = '';
      };
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wire);
  } else {
    _wire();
  }

})();
