/**
 * nx-hitl-bridge.js — HITL Frontend Lifecycle Bridge
 * ════════════════════════════════════════════════════
 * Wires the NxBus 'hitl.response' event → POST /api/session/<sid>/hitl/respond
 * and restores pending HITL events on session reconnect.
 *
 * This closes the final gap in the HITL lifecycle:
 *   hitl.required (SSE) → NxBus → NxAgiSurface (overlay)
 *        ↓ operator clicks Approve/Reject/Retry
 *   hitl.response (NxBus) → THIS FILE → POST backend
 *        ↓ backend unblocks agent thread
 *   hitl.resolved (SSE) → NxBus → NxAgiSurface (overlay cleared)
 */
'use strict';

(function () {

  if (window._NxHitlBridgeLoaded) return;
  window._NxHitlBridgeLoaded = true;

  /* ── Pending state ─────────────────────────────────────────────── */
  const _pendingEventId = {};   // sid → last active event_id

  /* ── Resolve helper ────────────────────────────────────────────── */
  function _respond(sid, eventId, action, feedback = '') {
    if (!sid) {
      console.warn('[HITL Bridge] No session ID — cannot submit response.');
      return;
    }
    return fetch(`/api/session/${encodeURIComponent(sid)}/hitl/respond`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_id: eventId, action, feedback }),
    })
      .then(r => r.json())
      .then(resp => {
        if (resp.ok) {
          console.debug(`[HITL Bridge] ${action} accepted. event=${eventId}`);
          delete _pendingEventId[sid];
        } else {
          console.warn('[HITL Bridge] Backend rejected:', resp.error);
        }
        return resp;
      })
      .catch(err => {
        console.error('[HITL Bridge] Fetch error:', err);
      });
  }

  /* ── Get current session ID ────────────────────────────────────── */
  function _getSid() {
    // Try multiple sources in priority order
    if (window.NxSSERuntime?.session) return NxSSERuntime.session;
    if (window._currentSessionId)     return window._currentSessionId;
    const el = document.getElementById('nxCurrentSid');
    if (el?.value)                    return el.value;
    return null;
  }

  /* ── Wire NxBus ────────────────────────────────────────────────── */
  function _wire() {
    if (!window.NxBus) { setTimeout(_wire, 200); return; }

    /* Track active HITL event IDs */
    NxBus.on('nx:hitl:required', d => {
      const sid = _getSid();
      if (sid && d?.event_id) _pendingEventId[sid] = d.event_id;
    }, { owner: 'nx-hitl-bridge' });

    /* Main response bridge */
    NxBus.on('hitl.response', d => {
      if (!d) return;
      const sid     = _getSid();
      const eventId = d.event_id || d.eventId || _pendingEventId[sid] || '';
      _respond(sid, eventId, d.action || 'approve', d.feedback || '');
    }, { owner: 'nx-hitl-bridge' });

    /* Restore pending HITL events on session reconnect/restore */
    const _onSessionRestore = (d) => {
      const sid = d?.sid || _getSid();
      if (!sid) return;
      fetch(`/api/session/${encodeURIComponent(sid)}/hitl/pending`)
        .then(r => r.json())
        .then(resp => {
          if (!resp.ok || !Array.isArray(resp.pending)) return;
          resp.pending.forEach(ev => {
            NxBus.emit('nx:hitl:required', {
              event_id:   ev.event_id,
              prompt:     ev.prompt,
              hitl_type:  ev.hitl_type,
              actions:    ev.actions,
              session_id: sid,
            });
          });
          if (resp.pending.length) {
            console.info(`[HITL Bridge] Restored ${resp.pending.length} pending HITL event(s).`);
          }
        })
        .catch(() => {});
    };

    NxBus.on(NxBus.EVENTS.SESSION_RESTORED, _onSessionRestore, { owner: 'nx-hitl-bridge' });
    NxBus.on(NxBus.EVENTS.SESSION_CREATED,  _onSessionRestore, { owner: 'nx-hitl-bridge' });

    /* Poll for stale HITL expiry every 60s */
    setInterval(() => {
      const sid = _getSid();
      if (!sid) return;
      fetch(`/api/session/${encodeURIComponent(sid)}/hitl/pending`)
        .then(r => r.json())
        .then(resp => {
          // If pending list is empty, clear any dangling HITL overlay
          if (resp.ok && resp.pending?.length === 0) {
            const area = document.getElementById('agiHitlArea');
            const block = area?.querySelector('.agi-hitl-escalation:not(.resolved)');
            if (block) {
              // Mark as timed-out without operator action
              block.classList.add('resolved');
              const title = block.querySelector('.agi-hitl-title');
              if (title) title.textContent = 'HITL — Expired (no operator response)';
              const actions = block.querySelector('.agi-hitl-actions');
              if (actions) actions.innerHTML = '';
              console.warn('[HITL Bridge] HITL event expired — no operator response within TTL.');
            }
          }
        })
        .catch(() => {});
    }, 60000);
  }

  /* ── Expose _respond globally for NxAgiSurface._resolveHitl ────── */
  window.NxHitlBridge = { respond: _respond };

  /* ── Init ──────────────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(_wire, 100));
  } else {
    setTimeout(_wire, 100);
  }

})();
