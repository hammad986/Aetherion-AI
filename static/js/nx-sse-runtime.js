/**
 * nx-sse-runtime.js — Aetherion Resilient SSE Runtime Layer v1
 * ══════════════════════════════════════════════════════════════════════
 * Owns the ONLY EventSource connection to the backend AGI stream.
 * All consumers receive events via NxBus — never direct DOM mutations.
 *
 * Features:
 *   - Exponential backoff reconnect (1s → 2s → 4s … cap 30s)
 *   - Heartbeat watchdog (60s silence = dead connection → reconnect)
 *   - Session binding: auto-closes stream when session changes
 *   - Backpressure: drops events when tab is hidden (resumes on focus)
 *   - Publishes structured NxBus events + raw SSE events on NxBus.EVENTS.*
 *   - Connection state machine: IDLE → CONNECTING → CONNECTED → RECONNECTING → CLOSED
 *   - No synthetic/mock events injected — backend events only
 *
 * Canonical SSE event types dispatched by the backend (AETHERION_REALTIME_V1):
 *   agent.think            → NxBus STREAM_CHUNK  {kind:'think', text, session_id}
 *   agent.action           → NxBus STREAM_CHUNK  {kind:'action', tool, args, session_id}
 *   agent.output           → NxBus STREAM_CHUNK  {kind:'output', text, session_id}
 *   agent.done             → NxBus STREAM_CLOSE  {session_id}
 *   agent.error            → NxBus STREAM_ERROR  {error, session_id}
 *   agent.task_complete    → NxBus AGENT_DONE    {status, confidence, completed_steps, ...}
 *   agent.trust_signal     → NxBus 'nx:trust:signal' {type, verified, confidence, message, step, action}
 *   agent.tool_success     → NxBus STREAM_CHUNK  {kind:'tool_success', ...}
 *   file.modified          → NxBus FILE_CHANGED  {path, ...}
 *   hitl.required          → NxBus 'nx:hitl:required'  {session_id, event_id, prompt, ...}
 *   hitl.resolved          → NxBus 'nx:hitl:resolved'  {event_id, action}
 *   task.cancelled         → NxBus AGENT_STOP    {session_id}
 *   heartbeat              → internal watchdog reset only (not forwarded)
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  /* ── Constants ───────────────────────────────────────────────────── */
  const PRIMARY_ENDPOINT   = (sid) => `/api/stream/${sid}`;
  const FALLBACK_ENDPOINT  = (sid) => `/api/session/${sid}/stream`;
  const BACKOFF_BASE_MS    = 1000;
  const BACKOFF_CAP_MS     = 30000;
  const HEARTBEAT_TTL_MS   = 30000;   // 30s — safe margin below proxy idle timeouts
  const MAX_RECONNECTS     = 20;
  const CONN_TIMEOUT_MS    = 8000;

  /* ── State ───────────────────────────────────────────────────────── */
  let _es            = null;   // active EventSource
  let _session       = null;   // bound session ID
  let _state         = 'IDLE'; // IDLE | CONNECTING | CONNECTED | RECONNECTING | CLOSED
  let _reconnects    = 0;
  let _backoffMs     = BACKOFF_BASE_MS;
  let _reconnectTimer= null;
  let _heartbeatTimer= null;
  let _connTimer     = null;
  let _useFallback   = false;  // flip to true if primary returns 404

  // Sequence-based deduplication: tracks last seen _seq per session
  const _lastSeq     = {};     // { [session_id]: number }

  /* ── Internal helpers ────────────────────────────────────────────── */
  function _setState(s) {
    if (_state === s) return;
    _state = s;
    _emitConnStatus(s);
  }

  function _emitConnStatus(state) {
    const el = document.getElementById('nxLiveConnStatus');
    const labels = {
      IDLE:         'Not connected',
      CONNECTING:   'Connecting…',
      CONNECTED:    '● Live',
      RECONNECTING: '⟳ Reconnecting…',
      CLOSED:       'Disconnected',
    };
    if (el) {
      el.textContent = labels[state] || state;
      el.style.color = state === 'CONNECTED'
        ? 'var(--green, #3fb950)'
        : state === 'RECONNECTING'
          ? 'var(--yellow, #d29922)'
          : 'var(--text-muted, #8b949e)';
    }
    // Also update the SSE status badge from stability.js if present
    const badge = document.getElementById('nx-sse-status');
    if (badge) {
      if (state === 'RECONNECTING' || state === 'CONNECTING') {
        badge.style.display = '';
        const span = badge.querySelector('span');
        if (span) span.textContent = labels[state];
      } else {
        badge.style.display = 'none';
      }
    }
    if (window.NxBus) {
      NxBus.emit(NxBus.EVENTS.WS_STATUS, { state: state.toLowerCase() });
    }
  }

  function _clearTimers() {
    if (_heartbeatTimer) { clearTimeout(_heartbeatTimer); _heartbeatTimer = null; }
    if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
    if (_connTimer)      { clearTimeout(_connTimer);      _connTimer      = null; }
  }

  function _resetHeartbeat() {
    if (_heartbeatTimer) clearTimeout(_heartbeatTimer);
    _heartbeatTimer = setTimeout(() => {
      // Silence for HEARTBEAT_TTL_MS — assume connection is dead
      console.warn('[NxSSE] Heartbeat timeout. Reconnecting.');
      _scheduleReconnect();
    }, HEARTBEAT_TTL_MS);
  }

  function _closeEs() {
    if (_es) {
      try { _es.close(); } catch (_) {}
      _es = null;
    }
  }

  /* ── Reconnect logic ─────────────────────────────────────────────── */
  function _scheduleReconnect() {
    _closeEs();
    _clearTimers();
    if (!_session || _reconnects >= MAX_RECONNECTS) {
      _setState('CLOSED');
      console.warn('[NxSSE] Max reconnects reached or no session. Giving up.');
      return;
    }
    _reconnects++;
    _setState('RECONNECTING');
    _reconnectTimer = setTimeout(() => {
      _connect(_session);
    }, _backoffMs);
    // Exponential backoff with cap
    _backoffMs = Math.min(_backoffMs * 2, BACKOFF_CAP_MS);
  }

  /* ── SSE dispatch ────────────────────────────────────────────────── */
  function _dispatch(type, raw) {
    let payload;
    try {
      payload = typeof raw === 'string' ? JSON.parse(raw) : raw;
    } catch (_) {
      payload = { text: raw };
    }

    // Sequence-based deduplication — drop replayed / duplicate events
    const seq = payload._seq;
    const sid = payload._sid || _session;
    if (seq != null && sid) {
      const lastSeen = _lastSeq[sid] || 0;
      if (seq <= lastSeen) {
        // Already processed — suppress duplicate (common on reconnect)
        console.debug(`[NxSSE] Dedup: dropped seq=${seq} (last=${lastSeen}) for session ${sid}`);
        _resetHeartbeat();
        return;
      }
      if (seq > lastSeen + 1 && lastSeen > 0) {
        // Gap detected — log for debugging but still process the event
        console.warn(`[NxSSE] Gap: seq=${seq}, expected=${lastSeen + 1} — ${seq - lastSeen - 1} events may have been lost`);
      }
      _lastSeq[sid] = seq;
    }

    // Always reset heartbeat on any message
    _resetHeartbeat();

    if (!window.NxBus) return;
    const E = NxBus.EVENTS;

    switch (type) {
      case 'agent.think':
        NxBus.emit(E.STREAM_CHUNK, { kind: 'think', ...payload });
        break;
      case 'agent.action':
        NxBus.emit(E.STREAM_CHUNK, { kind: 'action', ...payload });
        break;
      case 'agent.output':
        NxBus.emit(E.STREAM_CHUNK, { kind: 'output', ...payload });
        break;
      case 'agent.tool_success':
        NxBus.emit(E.STREAM_CHUNK, { kind: 'tool_success', ...payload });
        break;
      case 'agent.task_complete':
        // Trust Engine: forward completion with confidence metadata
        NxBus.emit(E.STREAM_CLOSE, payload);
        NxBus.emit(E.AGENT_DONE, {
          ...payload,
          confidence: payload.confidence ?? null,
          completed_steps: payload.completed_steps ?? null,
          total_steps: payload.total_steps ?? null,
        });
        break;
      case 'agent.trust_signal':
        // Trust Engine: forward trust signals for UI rendering
        NxBus.emit('nx:trust:signal', payload);
        break;
      case 'agent.dag_update':
        // Execution Planner: live DAG state
        NxBus.emit('nx:dag:update', payload);
        break;
      case 'agent.milestone_update':
        // Execution Planner: milestone progress
        NxBus.emit('nx:milestone:update', payload);
        break;
      case 'agent.done':
        NxBus.emit(E.STREAM_CLOSE, payload);
        NxBus.emit(E.AGENT_DONE, payload);
        break;
      case 'agent.error':
        NxBus.emit(E.STREAM_ERROR, payload);
        NxBus.emit(E.AGENT_ERROR, payload);
        break;
      case 'task.cancelled':
        NxBus.emit(E.AGENT_STOP, payload);
        break;
      case 'hitl.required':
        NxBus.emit('nx:hitl:required', payload);
        break;
      case 'agent.coordination_update':
        // Multi-Agent Coordination Bus: live orchestration state
        NxBus.emit('nx:coordination:update', payload);
        break;
      case 'agent.delegation_update':
        // Task Delegation Engine: delegation graph changes
        NxBus.emit('nx:delegation:update', payload);
        break;
      case 'hitl.resolved':
        NxBus.emit('nx:hitl:resolved', payload);
        break;
      case 'heartbeat':
        // Watchdog only — no forwarding
        break;
      // ── Z28: Operator Intelligence Layer ─────────────────────────
      case 'agent.explain':
        // Live decision record — route to Z28 decision feed
        NxBus.emit('nx:z28:decision', payload);
        break;
      case 'agent.context_state':
        // Context compression / token pressure state
        NxBus.emit('nx:z28:context', payload);
        break;
      case 'agent.confidence_warning':
        // Confidence score drop / HITL threshold warning
        NxBus.emit('nx:z28:health', payload);
        break;
      case 'agent.scheduler_state':
        // Scheduler activity state
        NxBus.emit('nx:z28:scheduler', payload);
        break;
      // ── Z29: Operator Control + Mission Governance ──────────────────
      case 'agent.mission_control':
        NxBus.emit('nx:z29:mission_control', payload);
        break;
      case 'agent.governance_request':
      case 'agent.governance_resolved':
        NxBus.emit('nx:z29:governance', payload);
        break;
      case 'agent.override_applied':
        NxBus.emit('nx:z29:override', payload);
        break;
      case 'agent.stability_alert':
      case 'agent.recovery_applied':
        NxBus.emit('nx:z29:stability', payload);
        break;
      default:
        // Forward unknown events generically for extensibility
        NxBus.emit('nx:sse:event', { type, ...payload });
    }

    // Also forward log-compatible events to the existing ingestLogRow pipeline
    if ((type === 'agent.think' || type === 'agent.action' || type === 'agent.output')
        && typeof window.ingestLogRow === 'function') {
      const kindMap = { 'agent.think': 'thought', 'agent.action': 'action', 'agent.output': 'output' };
      window.ingestLogRow({
        kind: kindMap[type] || type,
        text: payload.text || payload.output || '',
        ts: payload.ts || (Date.now() / 1000),
        seq: payload.seq,
        tool: payload.tool,
        args: payload.args,
      });
    }
  }

  /* ── Connect ─────────────────────────────────────────────────────── */
  function _connect(sid) {
    if (!sid) return;
    _closeEs();
    _clearTimers();
    _setState('CONNECTING');

    const url = _useFallback ? FALLBACK_ENDPOINT(sid) : PRIMARY_ENDPOINT(sid);
    let es;
    try {
      es = new EventSource(url);
    } catch (err) {
      console.error('[NxSSE] EventSource construction failed:', err);
      _scheduleReconnect();
      return;
    }
    _es = es;

    // Connection timeout — if no message within CONN_TIMEOUT_MS, try fallback
    _connTimer = setTimeout(() => {
      if (_state !== 'CONNECTED') {
        console.warn('[NxSSE] Connection timeout on', url);
        if (!_useFallback) {
          _useFallback = true;
          console.info('[NxSSE] Switching to fallback endpoint.');
        }
        _scheduleReconnect();
      }
    }, CONN_TIMEOUT_MS);

    // Generic message handler (event: none specified)
    es.onmessage = (ev) => {
      if (_es !== es) return; // stale connection guard
      _setState('CONNECTED');
      _reconnects = 0;
      _backoffMs  = BACKOFF_BASE_MS;
      _dispatch('agent.output', ev.data);
    };

    // Typed event listeners
    const TYPED_EVENTS = [
      'agent.think', 'agent.action', 'agent.output',
      'agent.done',  'agent.error',  'task.cancelled',
      'hitl.required', 'hitl.resolved',
      'heartbeat', 'chunk', 'end', 'error',
    ];
    TYPED_EVENTS.forEach(evtType => {
      es.addEventListener(evtType, (ev) => {
        if (_es !== es) return;
        _setState('CONNECTED');
        _reconnects = 0;
        _backoffMs  = BACKOFF_BASE_MS;
        // Map legacy event names to canonical names
        const canonical = evtType === 'chunk' ? 'agent.output'
          : evtType === 'end' ? 'agent.done'
          : evtType === 'error' ? 'agent.error'
          : evtType;
        _dispatch(canonical, ev.data);
      });
    });

    es.onerror = (err) => {
      if (_es !== es) return;
      // EventSource.readyState: 0=CONNECTING, 1=OPEN, 2=CLOSED
      if (es.readyState === EventSource.CLOSED) {
        console.warn('[NxSSE] Connection closed by server. Reconnecting.');
        _scheduleReconnect();
      }
      // If readyState is CONNECTING, browser is already retrying — let it.
      // We only intervene with our own backoff if the browser gives up (CLOSED).
    };
  }

  /* ── Public API ──────────────────────────────────────────────────── */
  const NxSSERuntime = {
    /**
     * Bind to a session and open the SSE stream.
     * Safe to call multiple times — closes any previous connection first.
     */
    open(sid) {
      if (!sid) return;
      if (_session === sid && _state === 'CONNECTED') return; // already live
      _session     = sid;
      _reconnects  = 0;
      _backoffMs   = BACKOFF_BASE_MS;
      _useFallback = false;
      _connect(sid);
    },

    /**
     * Close and unbind from the current session.
     */
    close() {
      _closeEs();
      _clearTimers();
      _session = null;
      _setState('IDLE');
    },

    /**
     * Force a reconnect on the current session (e.g., after tab focus).
     */
    reconnect() {
      if (!_session) return;
      _reconnects = 0;
      _backoffMs  = BACKOFF_BASE_MS;
      _connect(_session);
    },

    /** Current connection state string. */
    get state() { return _state; },

    /** Currently bound session ID. */
    get session() { return _session; },
  };

  window.NxSSERuntime = NxSSERuntime;

  /* ── Auto-bind on session events ─────────────────────────────────── */
  function _wireBus() {
    if (!window.NxBus) return;
    const E = NxBus.EVENTS;

    NxBus.on(E.SESSION_CREATED, (d) => {
      if (d && d.sid) NxSSERuntime.open(d.sid);
    }, { owner: 'nx-sse-runtime' });

    NxBus.on(E.SESSION_RESTORED, (d) => {
      if (d && d.sid) NxSSERuntime.open(d.sid);
    }, { owner: 'nx-sse-runtime' });

    NxBus.on(E.SESSION_CLEARED, () => {
      NxSSERuntime.close();
    }, { owner: 'nx-sse-runtime' });
  }

  // Wire bus after DOMContentLoaded (boot.js registers tasks first)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireBus);
  } else {
    _wireBus();
  }

  /* ── Reconnect on tab focus after being hidden ───────────────────── */
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _session && _state !== 'CONNECTED') {
      NxSSERuntime.reconnect();
    }
  });

  /* ── Expose open() globally so runtime.js can call it post-session-load ── */
  window._nxSseOpen = (sid) => NxSSERuntime.open(sid);

  /* ── Non-invasive openLogStream bridge ──────────────────────────────
   * Wait for runtime.js to define openLogStream (it's deferred), then
   * wrap it so NxSSERuntime.open() fires in parallel every time a
   * session is selected. Also emit SESSION_RESTORED on NxBus so that
   * nx-observability and other modules get the session lifecycle event.
   * ─────────────────────────────────────────────────────────────────── */
  function _patchOpenLogStream() {
    const _orig = window.openLogStream;
    if (typeof _orig !== 'function') {
      setTimeout(_patchOpenLogStream, 300);
      return;
    }
    window.openLogStream = function (sid) {
      // Emit SESSION_RESTORED → _wireBus handler calls NxSSERuntime.open(sid)
      // Do NOT call NxSSERuntime.open() directly here — that causes a double-open race.
      if (window.NxBus) {
        NxBus.emit(NxBus.EVENTS.SESSION_RESTORED, { sid });
      }
      // Delegate to existing log-tail stream (preserve existing behavior)
      return _orig.apply(this, arguments);
    };
    console.debug('[NxSSE] openLogStream patched → NxSSERuntime bridge active (single-open path).');
  }

  // Defer until after DOMContentLoaded so all deferred scripts have run
  if (document.readyState === 'complete') {
    setTimeout(_patchOpenLogStream, 100);
  } else {
    window.addEventListener('load', () => setTimeout(_patchOpenLogStream, 100));
  }

  /* ── Reset seq tracking on session change ─────────────────────────── */
  function _onSessionChange() {
    if (!window.NxBus) return;
    NxBus.on(NxBus.EVENTS.SESSION_CLEARED, () => {
      if (_session) delete _lastSeq[_session];
    }, { owner: 'nx-sse-seq-reset' });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _onSessionChange);
  } else {
    _onSessionChange();
  };

