"""
governance/audit_forensics.py — Operator Audit & Forensics Console
===================================================================
Exposes the live governance state as a structured audit log.
Records all governance events (refusals, escalations, bypasses, drift
alerts, certification changes) in a time-ordered, searchable log.

Provides:
  • audit_log()        – Full immutable governance event timeline
  • live_status()      – Current trust state at a glance
  • escalation_map()   – Pattern analysis of escalations over time
  • refusal_timeline() – Time-series of refusal events
  • governance_digest()– Compact snapshot for operator dashboards
"""

import time
import threading
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Deque
from enum import Enum

logger = logging.getLogger("nexora.governance.audit")


# ── Audit Event Types ─────────────────────────────────────────────────────────

class AuditEventType(str, Enum):
    REFUSAL         = "REFUSAL"
    ESCALATION      = "ESCALATION"
    APPROVAL        = "APPROVAL"
    BYPASS_DETECTED = "BYPASS_DETECTED"
    DRIFT_ALERT     = "DRIFT_ALERT"
    CERT_ISSUED     = "CERT_ISSUED"
    CERT_SUSPENDED  = "CERT_SUSPENDED"
    EVAL_COMPLETED  = "EVAL_COMPLETED"
    RED_TEAM_RUN    = "RED_TEAM_RUN"
    OPERATOR_REVIEW = "OPERATOR_REVIEW"

@dataclass
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    timestamp: float
    severity: str           # "INFO" | "WARN" | "CRITICAL"
    source: str             # Module/component name
    description: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "source": self.source,
            "description": self.description,
            "metadata": self.metadata,
        }


# ── Audit Store ───────────────────────────────────────────────────────────────

class GovernanceAuditLog:
    """
    Immutable, append-only governance event log.
    Supports time-range queries, event-type filtering, and pattern analysis.
    """

    MAX_EVENTS = 10_000   # Rolling window

    def __init__(self):
        self._lock = threading.Lock()
        self._events: Deque[AuditEvent] = deque(maxlen=self.MAX_EVENTS)
        self._counters: Dict[str, int] = defaultdict(int)
        self._last_bypass_ts: Optional[float] = None
        self._last_drift_severity: str = "NONE"
        self._last_cert_level: str = "UNKNOWN"
        self._active_cert_id: Optional[str] = None

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._counters[event.event_type.value] += 1
            if event.event_type == AuditEventType.BYPASS_DETECTED:
                self._last_bypass_ts = event.timestamp
            elif event.event_type in (AuditEventType.DRIFT_ALERT,):
                self._last_drift_severity = event.metadata.get("severity", "UNKNOWN")
            elif event.event_type in (AuditEventType.CERT_ISSUED, AuditEventType.CERT_SUSPENDED):
                self._last_cert_level = event.metadata.get("level", "UNKNOWN")
                self._active_cert_id = event.metadata.get("cert_id")

    def ingest_eval_report(self, report) -> None:
        import uuid
        severity = "WARN" if report.failed > 0 else "INFO"
        self.record(AuditEvent(
            event_id=f"ae_{uuid.uuid4().hex[:8]}",
            event_type=AuditEventType.EVAL_COMPLETED,
            timestamp=time.time(),
            severity=severity,
            source="evaluation_engine",
            description=f"Eval run {report.run_id}: {report.passed}/{report.total_probes} passed ({report.pass_rate:.1%})",
            metadata={"run_id": report.run_id, "pass_rate": report.pass_rate,
                      "failed": report.failed, "avg_latency_ms": report.avg_latency_ms},
        ))
        # Record individual failures as escalation events
        for failure in report.failures:
            self.record(AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:8]}",
                event_type=AuditEventType.ESCALATION,
                timestamp=time.time(),
                severity="WARN",
                source="evaluation_engine",
                description=f"Probe {failure.probe_id} failed: {failure.failure_reason}",
                metadata=failure.to_dict(),
            ))

    def ingest_campaign_report(self, campaign) -> None:
        import uuid
        if campaign.bypassed > 0:
            self.record(AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:8]}",
                event_type=AuditEventType.BYPASS_DETECTED,
                timestamp=time.time(),
                severity="CRITICAL",
                source="red_team",
                description=f"RED-TEAM: {campaign.bypassed} bypasses in campaign {campaign.campaign_id} (rate={campaign.bypass_rate:.1%})",
                metadata={"campaign_id": campaign.campaign_id, "bypasses": campaign.bypassed,
                          "bypass_rate": campaign.bypass_rate,
                          "bypass_details": [b.attack_id for b in campaign.bypasses]},
            ))
        else:
            self.record(AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:8]}",
                event_type=AuditEventType.RED_TEAM_RUN,
                timestamp=time.time(),
                severity="INFO",
                source="red_team",
                description=f"Red-team campaign {campaign.campaign_id}: 0 bypasses ({campaign.total_attacks} attacks)",
                metadata={"campaign_id": campaign.campaign_id, "total": campaign.total_attacks},
            ))

    def ingest_drift_report(self, drift) -> None:
        import uuid
        severity = "INFO" if drift.severity == "NONE" else (
            "WARN" if drift.severity in ("WATCH", "WARNING") else "CRITICAL"
        )
        self.record(AuditEvent(
            event_id=f"ae_{uuid.uuid4().hex[:8]}",
            event_type=AuditEventType.DRIFT_ALERT,
            timestamp=time.time(),
            severity=severity,
            source="drift_detector",
            description=f"Drift: {drift.severity} | {drift.explanation}",
            metadata={"report_id": drift.report_id, "severity": drift.severity,
                      "drifted": drift.drifted_dimensions, "recommendation": drift.recommendation},
        ))

    def ingest_certificate(self, cert) -> None:
        import uuid
        evt_type = (AuditEventType.CERT_SUSPENDED
                    if cert.level.value in ("SUSPENDED", "REVOKED")
                    else AuditEventType.CERT_ISSUED)
        severity = "CRITICAL" if evt_type == AuditEventType.CERT_SUSPENDED else "INFO"
        self.record(AuditEvent(
            event_id=f"ae_{uuid.uuid4().hex[:8]}",
            event_type=evt_type,
            timestamp=time.time(),
            severity=severity,
            source="trust_certification",
            description=f"Certificate {cert.cert_id}: {cert.level.value} | {cert.tier.value}",
            metadata={"cert_id": cert.cert_id, "level": cert.level.value,
                      "tier": cert.tier.value, "caveats": cert.caveats},
        ))

    # ── Query Interface ───────────────────────────────────────────────────────

    def all_events(self, last_n: int = 200) -> List[AuditEvent]:
        with self._lock:
            events = list(self._events)
        return events[-last_n:]

    def events_by_type(self, event_type: AuditEventType, last_n: int = 50) -> List[AuditEvent]:
        with self._lock:
            return [e for e in list(self._events) if e.event_type == event_type][-last_n:]

    def critical_events(self, last_n: int = 50) -> List[AuditEvent]:
        with self._lock:
            return [e for e in list(self._events) if e.severity == "CRITICAL"][-last_n:]

    def refusal_timeline(self) -> List[dict]:
        return [e.to_dict() for e in self.events_by_type(AuditEventType.REFUSAL)]

    def escalation_pattern(self) -> Dict[str, int]:
        """Returns count of escalations per source."""
        pattern: Dict[str, int] = defaultdict(int)
        for e in self.events_by_type(AuditEventType.ESCALATION):
            pattern[e.source] += 1
        return dict(pattern)

    # ── Live Status ────────────────────────────────────────────────────────────

    def live_status(self) -> dict:
        with self._lock:
            total = len(self._events)
            counters = dict(self._counters)
        return {
            "total_governance_events": total,
            "event_counts": counters,
            "last_bypass_ts": self._last_bypass_ts,
            "last_drift_severity": self._last_drift_severity,
            "active_cert_level": self._last_cert_level,
            "active_cert_id": self._active_cert_id,
            "bypass_detected": self._last_bypass_ts is not None,
        }

    def governance_digest(self) -> dict:
        """Compact operator-dashboard payload."""
        status = self.live_status()
        recent = self.all_events(last_n=10)
        criticals = self.critical_events(last_n=5)
        return {
            "status": status,
            "recent_events": [e.to_dict() for e in recent],
            "critical_alerts": [e.to_dict() for e in criticals],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_audit_instance: Optional[GovernanceAuditLog] = None
_audit_lock = threading.Lock()

def get_audit_log() -> GovernanceAuditLog:
    global _audit_instance
    with _audit_lock:
        if _audit_instance is None:
            _audit_instance = GovernanceAuditLog()
    return _audit_instance
