"""
nx_hitl_response.py — HITL Response API Route (Phase: Operational Completion)
══════════════════════════════════════════════════════════════════════════════
Registers the missing /api/session/<sid>/hitl/respond endpoint.

This module patches the Flask app in web_app.py to close the
frontend → backend → runtime → resume HITL lifecycle loop.

Architecture:
  1. Frontend: hitl.response NxBus event → POST /api/session/<sid>/hitl/respond
  2. This endpoint: validates, records audit log, calls global_hitl_tracker
  3. global_hitl_tracker: updates SQLite + unblocks the waiting agent thread
  4. Agent thread resumes with the operator's decision (approve/reject/retry)

SSE broadcast: hitl.resolved emitted back to session so frontend surface updates.
"""

import json
import logging
import time
from functools import wraps

logger = logging.getLogger("nexora.hitl_response")


def register_hitl_routes(app, db_insert_decision, global_hitl_tracker,
                         sse_broadcast_fn, conn_factory,
                         max_event_age_sec=300):
    """
    Register HITL response routes onto an existing Flask app.

    Parameters
    ----------
    app               : Flask application
    db_insert_decision: callable(sid, ts, kind, detail)
    global_hitl_tracker: HITLEventTracker instance
    sse_broadcast_fn  : callable(session_id, event_type, payload)
    conn_factory      : callable() → sqlite3 connection (for audit writes)
    max_event_age_sec : HITL event expiry timeout in seconds
    """

    @app.route("/api/session/<sid>/hitl/respond", methods=["POST"])
    def api_session_hitl_respond(sid):
        """
        Accept an operator HITL decision and unblock the waiting agent thread.

        Request JSON:
          {
            "event_id":  str,   # correlates to hitl.required event_id
            "action":    str,   # "approve" | "reject" | "retry"
            "feedback":  str,   # optional operator note
          }

        Response:
          { "ok": true, "action": "approve", "event_id": "..." }
        """
        from flask import request, jsonify

        data      = request.get_json(silent=True) or {}
        event_id  = (data.get("event_id") or "").strip()
        action    = (data.get("action")   or "approve").strip().lower()
        feedback  = (data.get("feedback") or "").strip()[:500]

        if action not in ("approve", "reject", "retry"):
            return jsonify({
                "ok": False,
                "error": f"invalid_action: '{action}'. Must be approve|reject|retry"
            }), 400

        if not event_id:
            # Fallback: treat sid itself as the execution_id (legacy flow)
            event_id = sid

        # Map action → HITLEventTracker status
        status_map = {"approve": "approved", "reject": "rejected", "retry": "retry"}
        tracker_status = status_map[action]

        # ── Audit log ────────────────────────────────────────────────────
        try:
            db_insert_decision(
                sid, time.time(), "hitl_response",
                f"event={event_id} action={action} feedback={feedback[:120]}"
            )
        except Exception as _audit_err:
            logger.warning(f"[HITL] Audit log failed: {_audit_err}")

        # Write to HITL audit table
        try:
            with conn_factory() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO hitl_audit
                        (event_id, session_id, action, feedback, resolved_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (event_id, sid, action, feedback)
                )
                conn.commit()
        except Exception:
            # Table may not exist yet — ensure it, then retry
            try:
                with conn_factory() as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS hitl_audit (
                            event_id     TEXT PRIMARY KEY,
                            session_id   TEXT NOT NULL,
                            action       TEXT NOT NULL,
                            feedback     TEXT,
                            resolved_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute(
                        "INSERT OR REPLACE INTO hitl_audit "
                        "(event_id, session_id, action, feedback) "
                        "VALUES (?, ?, ?, ?)",
                        (event_id, sid, action, feedback)
                    )
                    conn.commit()
            except Exception as _db_err:
                logger.warning(f"[HITL] DB audit write failed: {_db_err}")

        # ── Unblock the agent thread ──────────────────────────────────────
        resumed = False
        try:
            resumed = global_hitl_tracker.provide_approval(
                event_id, tracker_status, feedback
            )
            if not resumed:
                # Try using sid directly as fallback execution_id
                resumed = global_hitl_tracker.provide_approval(
                    sid, tracker_status, feedback
                )
        except Exception as _resume_err:
            logger.error(f"[HITL] Failed to unblock execution {event_id}: {_resume_err}")
            return jsonify({
                "ok": False,
                "error": f"resume_failed: {_resume_err}",
                "event_id": event_id,
                "action": action,
            }), 500

        # ── Broadcast hitl.resolved SSE ──────────────────────────────────
        try:
            sse_broadcast_fn(sid, "hitl.resolved", {
                "event_id":   event_id,
                "action":     action,
                "session_id": sid,
                "_ts":        time.time(),
            })
        except Exception as _sse_err:
            logger.warning(f"[HITL] SSE resolved broadcast failed: {_sse_err}")

        logger.info(
            f"[HITL] session={sid} event={event_id} "
            f"action={action} resumed={resumed}"
        )

        return jsonify({
            "ok":      True,
            "event_id": event_id,
            "action":   action,
            "resumed":  resumed,
        })

    @app.route("/api/session/<sid>/hitl/pending", methods=["GET"])
    def api_session_hitl_pending(sid):
        """
        Return all pending (unresolved) HITL requests for this session.
        Used by the frontend to restore HITL escalation state on reconnect.
        """
        from flask import jsonify

        try:
            with conn_factory() as conn:
                rows = conn.execute(
                    """
                    SELECT execution_id, payload, status, created_at
                    FROM hitl_requests
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                ).fetchall()
        except Exception as _err:
            return jsonify({"ok": True, "pending": []})

        pending = []
        now = time.time()
        for row in rows:
            try:
                payload = json.loads(row[1] or "{}")
            except Exception:
                payload = {}
            # Filter to this session only, and drop stale events
            if payload.get("session_id", sid) != sid:
                continue
            created_ts = row[3]
            try:
                import datetime as _dt
                created_epoch = _dt.datetime.fromisoformat(
                    str(created_ts)
                ).timestamp()
                if now - created_epoch > max_event_age_sec:
                    continue
            except Exception:
                pass
            pending.append({
                "event_id":   row[0],
                "prompt":     payload.get("prompt", ""),
                "hitl_type":  payload.get("hitl_type", "clarification"),
                "actions":    payload.get("actions", ["approve", "reject"]),
                "created_at": created_ts,
            })

        return jsonify({"ok": True, "pending": pending})

    @app.route("/api/session/<sid>/hitl/audit", methods=["GET"])
    def api_session_hitl_audit(sid):
        """
        Return the HITL resolution audit trail for this session.
        Enterprise audit compliance endpoint.
        """
        from flask import request, jsonify

        limit = min(int(request.args.get("limit", 50)), 200)
        try:
            with conn_factory() as conn:
                rows = conn.execute(
                    """
                    SELECT event_id, action, feedback, resolved_at
                    FROM hitl_audit
                    WHERE session_id = ?
                    ORDER BY resolved_at DESC
                    LIMIT ?
                    """,
                    (sid, limit)
                ).fetchall()
            audit = [
                {
                    "event_id":    r[0],
                    "action":      r[1],
                    "feedback":    r[2] or "",
                    "resolved_at": r[3],
                }
                for r in rows
            ]
        except Exception:
            audit = []

        return jsonify({"ok": True, "session_id": sid, "audit": audit})

    logger.info("[HITL] Routes registered: hitl/respond, hitl/pending, hitl/audit")


# ── NxBus → fetch bridge (frontend side) ─────────────────────────────────────
# Injects hitl.response NxBus event → POST /api/session/<sid>/hitl/respond
# This JS snippet is served inline and referenced by nx-signals.js bridge.
HITL_FRONTEND_BRIDGE = """
/* nx-hitl-bridge — wired by nx_hitl_response.py server render */
(function () {
  'use strict';
  function _wireHitlBridge() {
    if (!window.NxBus) return;

    NxBus.on('hitl.response', function (d) {
      var sid = window._currentSessionId || (window.NxSSERuntime && NxSSERuntime.session);
      if (!sid || !d) return;

      fetch('/api/session/' + sid + '/hitl/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_id: d.event_id || d.eventId || '',
          action:   d.action   || 'approve',
          feedback: d.feedback || '',
        }),
      })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp.ok) {
          console.debug('[HITL] Response accepted:', resp.action, resp.event_id);
        } else {
          console.warn('[HITL] Response rejected:', resp.error);
        }
      })
      .catch(function (err) {
        console.error('[HITL] Fetch failed:', err);
      });
    }, { owner: 'nx-hitl-bridge' });

    /* On session connect, restore pending HITL from server */
    NxBus.on(NxBus.EVENTS.SESSION_RESTORED, function (d) {
      if (!d || !d.sid) return;
      fetch('/api/session/' + d.sid + '/hitl/pending')
        .then(function (r) { return r.json(); })
        .then(function (resp) {
          if (resp.ok && Array.isArray(resp.pending)) {
            resp.pending.forEach(function (ev) {
              NxBus.emit('hitl.required', {
                reason:    ev.prompt,
                actions:   ev.actions,
                event_id:  ev.event_id,
                hitl_type: ev.hitl_type,
              });
            });
          }
        })
        .catch(function () {});
    }, { owner: 'nx-hitl-bridge' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(_wireHitlBridge, 80); });
  } else {
    setTimeout(_wireHitlBridge, 80);
  }
})();
"""
