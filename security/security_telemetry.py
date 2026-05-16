"""
security/security_telemetry.py — Security Observability & Incident Response
============================================================================
Live security event stream, anomaly scoring, incident timelines,
and emergency operator intervention controls.

Tracks:
  • Injection attempts (count, confidence, session)
  • Command policy denials (score, signals)
  • Secret access attempts (denied/blocked paths)
  • Browser SSRF attempts
  • Retry storms (security-relevant)
  • Lock abuse
  • Delegation hijacks
  • Token exhaustion events
  • Anomalous session behavior

Incident classification:
  SEV_1 — Active attack (SSRF/injection with >0.8 confidence)
  SEV_2 — Probable attack (repeated denials, high-score commands)
  SEV_3 — Suspicious activity (elevated scoring, anomalous patterns)
  INFO  — Normal security events (policy denials within threshold)

Operator intervention:
  /api/security/emergency_lockdown  → activates kill-switch + session freeze
  /api/security/release_lockdown    → requires explicit operator action
  /api/security/incidents           → recent incident timeline
  /api/security/audit               → full security audit stream
"""

import collections
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional

logger = logging.getLogger("nexora.security.telemetry")


# ─────────────────────────────────────────────────────────────────────────────
# Event & incident types
# ─────────────────────────────────────────────────────────────────────────────

class SecurityEventType(str, Enum):
    INJECTION_ATTEMPT    = "injection_attempt"
    COMMAND_DENIED       = "command_denied"
    COMMAND_ESCALATED    = "command_escalated"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    SENSITIVE_PATH_BLOCK = "sensitive_path_block"
    BROWSER_SSRF         = "browser_ssrf"
    BROWSER_BLOCKED      = "browser_blocked"
    RETRY_STORM          = "retry_storm"
    LOCK_TIMEOUT         = "lock_timeout"
    DELEGATION_HIJACK    = "delegation_hijack"
    TOKEN_EXHAUSTION     = "token_exhaustion"
    QUOTA_EXCEEDED       = "quota_exceeded"
    RATE_LIMIT_HIT       = "rate_limit_hit"
    KILLSWITCH_ACTIVATED = "killswitch_activated"
    SANDBOX_VIOLATION    = "sandbox_violation"
    ANOMALOUS_SESSION    = "anomalous_session"

class IncidentSeverity(str, Enum):
    SEV1 = "SEV1"    # Active attack
    SEV2 = "SEV2"    # Probable attack
    SEV3 = "SEV3"    # Suspicious activity
    INFO = "INFO"

@dataclass
class SecurityEvent:
    event_id:   str
    event_type: SecurityEventType
    severity:   IncidentSeverity
    session_id: str
    tenant_id:  str
    payload:    dict
    ts:         float = field(default_factory=time.time)
    mitigated:  bool  = False

@dataclass
class SecurityIncident:
    incident_id: str
    severity:    IncidentSeverity
    title:       str
    session_id:  str
    tenant_id:   str
    events:      List[str]       # event_ids
    opened_at:   float
    closed_at:   float = 0.0
    resolved:    bool  = False
    resolution:  str   = ""


# ─────────────────────────────────────────────────────────────────────────────
# Anomaly scoring — session-level threat accumulation
# ─────────────────────────────────────────────────────────────────────────────

# Event type → anomaly score contribution
_ANOMALY_SCORES: Dict[SecurityEventType, int] = {
    SecurityEventType.INJECTION_ATTEMPT:    40,
    SecurityEventType.COMMAND_DENIED:       20,
    SecurityEventType.COMMAND_ESCALATED:    15,
    SecurityEventType.SECRET_ACCESS_DENIED: 30,
    SecurityEventType.SENSITIVE_PATH_BLOCK: 25,
    SecurityEventType.BROWSER_SSRF:         50,
    SecurityEventType.BROWSER_BLOCKED:      10,
    SecurityEventType.RETRY_STORM:          20,
    SecurityEventType.LOCK_TIMEOUT:          5,
    SecurityEventType.DELEGATION_HIJACK:    45,
    SecurityEventType.TOKEN_EXHAUSTION:     10,
    SecurityEventType.QUOTA_EXCEEDED:       10,
    SecurityEventType.RATE_LIMIT_HIT:       15,
    SecurityEventType.KILLSWITCH_ACTIVATED: 100,
    SecurityEventType.SANDBOX_VIOLATION:    60,
    SecurityEventType.ANOMALOUS_SESSION:    30,
}

# Score thresholds for automatic incident creation
_SEV1_THRESHOLD = 80
_SEV2_THRESHOLD = 50
_SEV3_THRESHOLD = 25

# Session score decay: reset daily, decay 10%/5min
_SCORE_DECAY_INTERVAL = 300   # seconds
_SCORE_DECAY_RATE     = 0.10   # 10% reduction per interval


# ─────────────────────────────────────────────────────────────────────────────
# SecurityTelemetry
# ─────────────────────────────────────────────────────────────────────────────

class SecurityTelemetry:
    """
    Live security event stream, anomaly scoring, and incident management.
    Provides real-time operator visibility into the security posture.
    """

    def __init__(self):
        self._lock = threading.RLock()

        # Event ring buffer
        self._events: Deque[SecurityEvent] = collections.deque(maxlen=2000)

        # Session anomaly scores: session_id → score (0-100)
        self._session_scores: Dict[str, float] = {}
        self._session_last_event: Dict[str, float] = {}

        # Open incidents
        self._incidents: Dict[str, SecurityIncident] = {}
        self._closed_incidents: Deque[SecurityIncident] = collections.deque(maxlen=200)

        # Emergency lockdown state
        self._lockdown_active = False
        self._lockdown_reason = ""
        self._lockdown_ts     = 0.0

        # SSE broadcast callback (set by web_app after init)
        self._sse_broadcast_fn = None

        # Score decay background thread
        t = threading.Thread(target=self._decay_loop, daemon=True, name="sec-score-decay")
        t.start()

    # ── Event recording ───────────────────────────────────────────────────────

    def record(
        self,
        event_type: SecurityEventType,
        session_id: str = "",
        tenant_id: str  = "",
        payload: dict   = None,
        mitigated: bool = False,
    ) -> SecurityEvent:
        """Records a security event and updates session anomaly score."""
        payload = payload or {}
        score_delta = _ANOMALY_SCORES.get(event_type, 5)

        # Determine severity from score
        current_score = self._get_score(session_id) + score_delta
        if current_score >= _SEV1_THRESHOLD:
            sev = IncidentSeverity.SEV1
        elif current_score >= _SEV2_THRESHOLD:
            sev = IncidentSeverity.SEV2
        elif current_score >= _SEV3_THRESHOLD:
            sev = IncidentSeverity.SEV3
        else:
            sev = IncidentSeverity.INFO

        evt = SecurityEvent(
            event_id   = uuid.uuid4().hex[:16],
            event_type = event_type,
            severity   = sev,
            session_id = session_id,
            tenant_id  = tenant_id,
            payload    = payload,
            mitigated  = mitigated,
        )

        with self._lock:
            self._events.append(evt)
            # Update session score
            self._session_scores[session_id] = min(100.0, current_score)
            self._session_last_event[session_id] = time.time()

        # Log at appropriate level
        log_level = {
            IncidentSeverity.SEV1: logging.CRITICAL,
            IncidentSeverity.SEV2: logging.ERROR,
            IncidentSeverity.SEV3: logging.WARNING,
            IncidentSeverity.INFO: logging.INFO,
        }[sev]
        logger.log(log_level,
            f"[Security] {sev.value} {event_type.value} session={session_id} "
            f"score={int(current_score)} mitigated={mitigated}")

        # Auto-create incident if threshold crossed
        if sev in (IncidentSeverity.SEV1, IncidentSeverity.SEV2):
            self._ensure_incident(evt, sev)

        # Broadcast to SSE
        self._broadcast(evt)

        return evt

    # ── Convenience event recorders ───────────────────────────────────────────

    def record_injection(self, session_id: str, confidence: float,
                         signals: list, source: str) -> SecurityEvent:
        return self.record(
            SecurityEventType.INJECTION_ATTEMPT, session_id,
            payload={"confidence": confidence, "signals": signals, "source": source},
            mitigated=(confidence < 0.8),
        )

    def record_command_denied(self, session_id: str, risk_score: int,
                              signals: list, preview: str) -> SecurityEvent:
        return self.record(
            SecurityEventType.COMMAND_DENIED, session_id,
            payload={"risk_score": risk_score, "signals": signals, "preview": preview[:80]},
            mitigated=True,
        )

    def record_ssrf_attempt(self, session_id: str, url: str, signals: list) -> SecurityEvent:
        return self.record(
            SecurityEventType.BROWSER_SSRF, session_id,
            payload={"url": url[:150], "signals": signals},
            mitigated=True,
        )

    def record_sandbox_violation(self, session_id: str, path: str) -> SecurityEvent:
        return self.record(
            SecurityEventType.SANDBOX_VIOLATION, session_id,
            payload={"path": path[:150]},
            mitigated=True,
        )

    def record_sensitive_path_block(self, session_id: str, path: str) -> SecurityEvent:
        return self.record(
            SecurityEventType.SENSITIVE_PATH_BLOCK, session_id,
            payload={"path": path[:150]},
            mitigated=True,
        )

    # ── Session anomaly scoring ───────────────────────────────────────────────

    def _get_score(self, session_id: str) -> float:
        return self._session_scores.get(session_id, 0.0)

    def get_session_risk_score(self, session_id: str) -> float:
        return self._get_score(session_id)

    def is_session_high_risk(self, session_id: str,
                              threshold: float = _SEV2_THRESHOLD) -> bool:
        return self._get_score(session_id) >= threshold

    def _decay_loop(self) -> None:
        """Periodically decays session anomaly scores."""
        while True:
            time.sleep(_SCORE_DECAY_INTERVAL)
            with self._lock:
                for sid in list(self._session_scores.keys()):
                    self._session_scores[sid] *= (1.0 - _SCORE_DECAY_RATE)
                    if self._session_scores[sid] < 1.0:
                        self._session_scores.pop(sid, None)

    # ── Incident management ───────────────────────────────────────────────────

    def _ensure_incident(self, evt: SecurityEvent, sev: IncidentSeverity) -> None:
        """Opens a new incident if no open incident exists for this session."""
        with self._lock:
            # Check if open incident exists for this session
            for inc in self._incidents.values():
                if inc.session_id == evt.session_id and not inc.resolved:
                    inc.events.append(evt.event_id)
                    if sev == IncidentSeverity.SEV1 and inc.severity != IncidentSeverity.SEV1:
                        inc.severity = IncidentSeverity.SEV1  # Escalate
                    return

            # Create new incident
            incident = SecurityIncident(
                incident_id = uuid.uuid4().hex[:12],
                severity    = sev,
                title       = f"{sev.value}: {evt.event_type.value} from session {evt.session_id[:8]}",
                session_id  = evt.session_id,
                tenant_id   = evt.tenant_id,
                events      = [evt.event_id],
                opened_at   = time.time(),
            )
            self._incidents[incident.incident_id] = incident
            logger.critical(
                f"[Security] 🔴 INCIDENT OPENED: {incident.incident_id} "
                f"sev={sev.value} session={evt.session_id}"
            )

    def resolve_incident(self, incident_id: str, resolution: str,
                         operator: str = "") -> bool:
        with self._lock:
            inc = self._incidents.pop(incident_id, None)
            if not inc:
                return False
            inc.resolved    = True
            inc.closed_at   = time.time()
            inc.resolution  = f"[{operator}] {resolution}"[:300]
            self._closed_incidents.append(inc)
        logger.info(f"[Security] Incident {incident_id} resolved by {operator}: {resolution[:80]}")
        return True

    def open_incidents(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "incident_id": i.incident_id,
                    "severity":    i.severity.value,
                    "title":       i.title,
                    "session_id":  i.session_id,
                    "events":      len(i.events),
                    "opened_at":   i.opened_at,
                }
                for i in self._incidents.values()
            ]

    # ── Emergency lockdown ────────────────────────────────────────────────────

    def activate_emergency_lockdown(self, reason: str, operator: str = "") -> None:
        """
        Activates system-wide emergency lockdown:
          1. Activates command policy kill-switch
          2. Freezes all new session creation
          3. Emits SEV1 incident
          4. Broadcasts to all SSE clients
        """
        from security.command_policy import global_command_policy
        with self._lock:
            self._lockdown_active = True
            self._lockdown_reason = f"[{operator}] {reason}"
            self._lockdown_ts     = time.time()

        global_command_policy.activate_killswitch(reason)
        self.record(SecurityEventType.KILLSWITCH_ACTIVATED,
                    payload={"reason": reason, "operator": operator})
        logger.critical(f"[Security] 🚨 EMERGENCY LOCKDOWN: operator={operator} reason={reason}")

    def release_emergency_lockdown(self, operator: str = "") -> None:
        """Releases lockdown. Requires explicit operator action."""
        from security.command_policy import global_command_policy
        with self._lock:
            self._lockdown_active = False
            self._lockdown_reason = ""

        global_command_policy.deactivate_killswitch()
        logger.warning(f"[Security] Emergency lockdown released by: {operator}")

    def is_lockdown_active(self) -> bool:
        return self._lockdown_active

    # ── SSE broadcast ─────────────────────────────────────────────────────────

    def set_sse_broadcast(self, fn) -> None:
        self._sse_broadcast_fn = fn

    def _broadcast(self, evt: SecurityEvent) -> None:
        if self._sse_broadcast_fn and evt.severity in (IncidentSeverity.SEV1, IncidentSeverity.SEV2):
            try:
                self._sse_broadcast_fn(
                    evt.session_id,
                    "security.incident",
                    {
                        "event_id":   evt.event_id,
                        "event_type": evt.event_type.value,
                        "severity":   evt.severity.value,
                        "payload":    evt.payload,
                        "ts":         evt.ts,
                    }
                )
            except Exception:
                pass

    # ── Snapshot & audit ──────────────────────────────────────────────────────

    def recent_events(self, n: int = 50, severity_filter: Optional[str] = None) -> List[dict]:
        with self._lock:
            events = list(self._events)[-n:]
        result = []
        for e in events:
            if severity_filter and e.severity.value != severity_filter:
                continue
            result.append({
                "event_id":   e.event_id,
                "event_type": e.event_type.value,
                "severity":   e.severity.value,
                "session_id": e.session_id,
                "ts":         e.ts,
                "mitigated":  e.mitigated,
                "payload":    e.payload,
            })
        return result

    def snapshot(self) -> dict:
        with self._lock:
            total = len(self._events)
            by_severity = collections.Counter(e.severity.value for e in self._events)
            by_type     = collections.Counter(e.event_type.value for e in self._events)
            top_risk_sessions = sorted(
                self._session_scores.items(), key=lambda x: x[1], reverse=True
            )[:5]
        return {
            "lockdown_active":    self._lockdown_active,
            "lockdown_reason":    self._lockdown_reason,
            "open_incidents":     len(self._incidents),
            "events_total":       total,
            "by_severity":        dict(by_severity),
            "by_type":            dict(by_type),
            "top_risk_sessions":  [
                {"session_id": s, "score": round(sc, 1)} for s, sc in top_risk_sessions
            ],
            "open_incident_list": self.open_incidents(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

_instance: Optional[SecurityTelemetry] = None
_instance_lock = threading.Lock()

def get_security_telemetry() -> SecurityTelemetry:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = SecurityTelemetry()
    return _instance
