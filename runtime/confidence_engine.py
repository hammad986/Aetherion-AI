"""
runtime/confidence_engine.py — Phase Z26 Uncertainty Estimation System
=======================================================================
Lightweight runtime confidence scoring. Detects low-confidence states,
contradictions, retry instability, and hallucination suspicion.

Exposes ONLY decision confidence summaries — never chain-of-thought.

FUTURE_RUNTIME_NOTE: If a learned confidence calibrator is added later,
it must be injected as a stateless scoring callable. Never couple this
module to a training loop or model weights directly.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("nexora.confidence_engine")

# ── Thresholds ────────────────────────────────────────────────────────────────

HITL_ESCALATION_THRESHOLD  = 0.35   # confidence below this → escalate to operator
LOW_CONFIDENCE_THRESHOLD   = 0.50   # warn operator
RETRY_INSTABILITY_PENALTY  = 0.10   # deducted per retry past the first
MAX_RETRY_PENALTY          = 0.40   # floor: retries alone can't tank score below 0.60 - MAX
HALLUCINATION_PENALTY      = 0.15   # applied when suspicion markers detected
CONTRADICTION_PENALTY      = 0.12   # applied on detected output contradiction


class ConfidenceLevel(Enum):
    HIGH       = "high"        # >= 0.75
    MODERATE   = "moderate"    # 0.50 – 0.74
    LOW        = "low"         # 0.35 – 0.49
    CRITICAL   = "critical"    # < 0.35  → HITL escalation


def _level(score: float) -> ConfidenceLevel:
    if score >= 0.75:
        return ConfidenceLevel.HIGH
    if score >= 0.50:
        return ConfidenceLevel.MODERATE
    if score >= 0.35:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.CRITICAL


# ── Signal types ──────────────────────────────────────────────────────────────

HALLUCINATION_MARKERS = [
    "as an ai",
    "i cannot actually",
    "i don't have access to",
    "i'm not able to browse",
    "i cannot verify",
    "i may be wrong",
    "i'm not sure",
    "i cannot confirm",
    "i might be hallucinating",
    "this is speculative",
    "i cannot guarantee",
]

CONTRADICTION_PATTERNS = [
    ("success", "failed"),
    ("completed", "error"),
    ("true", "false"),
    ("yes", "no"),
    ("created", "deleted"),
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ConfidenceSignal:
    signal_type: str         # "retry", "hallucination", "contradiction", "tool_failure", "low_evidence"
    penalty: float
    detail: str
    ts: float = field(default_factory=time.time)


@dataclass
class ConfidenceReport:
    sid: str
    step_id: str
    base_score: float
    final_score: float
    level: str
    signals: list[ConfidenceSignal]
    requires_hitl: bool
    operator_alert: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid":           self.sid,
            "step_id":       self.step_id,
            "final_score":   round(self.final_score, 3),
            "level":         self.level,
            "requires_hitl": self.requires_hitl,
            "alert":         self.operator_alert,
            "signal_count":  len(self.signals),
            "signals":       [
                {"type": s.signal_type, "penalty": s.penalty, "detail": s.detail}
                for s in self.signals
            ],
            "ts":            self.ts,
        }


# ── Telemetry log ─────────────────────────────────────────────────────────────

_telemetry: list[dict] = []
_tel_lock = threading.Lock()
_MAX_TEL = 1000


def _record_telemetry(report: ConfidenceReport):
    entry = {
        "sid":   report.sid,
        "score": report.final_score,
        "level": report.level,
        "hitl":  report.requires_hitl,
        "ts":    report.ts,
    }
    with _tel_lock:
        _telemetry.append(entry)
        if len(_telemetry) > _MAX_TEL:
            _telemetry.pop(0)


def get_confidence_telemetry(limit: int = 100) -> list[dict]:
    with _tel_lock:
        return list(_telemetry)[-limit:]


# ── Core scoring engine ───────────────────────────────────────────────────────

def score_step(
    sid: str,
    step_id: str,
    output_text: str = "",
    retry_count: int = 0,
    tool_failures: int = 0,
    evidence_count: int = 1,
    prior_outputs: list[str] | None = None,
    base_score: float = 1.0,
) -> ConfidenceReport:
    """
    Score a single execution step and return a ConfidenceReport.

    Parameters
    ----------
    sid           : session id
    step_id       : unique step identifier
    output_text   : the text output from this step (used for marker scanning)
    retry_count   : number of retries that occurred (0 = first try)
    tool_failures : number of tool calls that failed during this step
    evidence_count: number of supporting evidence items gathered
    prior_outputs : previous outputs in this session (used for contradiction check)
    base_score    : starting score (default 1.0)
    """
    signals: list[ConfidenceSignal] = []
    score = base_score

    # ── Retry instability penalty ─────────────────────────────────────────────
    if retry_count > 0:
        penalty = min(retry_count * RETRY_INSTABILITY_PENALTY, MAX_RETRY_PENALTY)
        score -= penalty
        signals.append(ConfidenceSignal(
            signal_type="retry",
            penalty=penalty,
            detail=f"{retry_count} retries detected",
        ))

    # ── Tool failure penalty ──────────────────────────────────────────────────
    if tool_failures > 0:
        penalty = min(tool_failures * 0.08, 0.30)
        score -= penalty
        signals.append(ConfidenceSignal(
            signal_type="tool_failure",
            penalty=penalty,
            detail=f"{tool_failures} tool failure(s)",
        ))

    # ── Low evidence penalty ──────────────────────────────────────────────────
    if evidence_count < 1:
        score -= 0.20
        signals.append(ConfidenceSignal(
            signal_type="low_evidence",
            penalty=0.20,
            detail="insufficient evidence for decision",
        ))

    # ── Hallucination suspicion scan ──────────────────────────────────────────
    if output_text:
        lower = output_text.lower()
        found = [m for m in HALLUCINATION_MARKERS if m in lower]
        if found:
            score -= HALLUCINATION_PENALTY
            signals.append(ConfidenceSignal(
                signal_type="hallucination",
                penalty=HALLUCINATION_PENALTY,
                detail=f"suspicion markers: {found[:3]}",
            ))

    # ── Contradiction scan ────────────────────────────────────────────────────
    if output_text and prior_outputs:
        combined_prior = " ".join(prior_outputs[-5:]).lower()
        current_lower = output_text.lower()
        for pos, neg in CONTRADICTION_PATTERNS:
            if pos in current_lower and neg in combined_prior:
                score -= CONTRADICTION_PENALTY
                signals.append(ConfidenceSignal(
                    signal_type="contradiction",
                    penalty=CONTRADICTION_PENALTY,
                    detail=f"'{pos}' vs prior '{neg}'",
                ))
                break

    score = max(0.0, min(1.0, score))
    level = _level(score)
    requires_hitl = level == ConfidenceLevel.CRITICAL

    # ── Operator alert message ────────────────────────────────────────────────
    if requires_hitl:
        alert = f"CRITICAL confidence ({score:.2f}) — operator review required before continuing."
    elif level == ConfidenceLevel.LOW:
        alert = f"Low confidence ({score:.2f}) — consider reviewing this step."
    else:
        alert = ""

    report = ConfidenceReport(
        sid=sid,
        step_id=step_id,
        base_score=base_score,
        final_score=score,
        level=level.value,
        signals=signals,
        requires_hitl=requires_hitl,
        operator_alert=alert,
    )

    _record_telemetry(report)

    if requires_hitl:
        logger.warning("[Confidence] HITL escalation | sid=%s step=%s score=%.2f", sid, step_id, score)
    elif level == ConfidenceLevel.LOW:
        logger.info("[Confidence] LOW | sid=%s step=%s score=%.2f", sid, step_id, score)

    return report


# ── Session-level confidence tracker ─────────────────────────────────────────

class SessionConfidenceTracker:
    """
    Tracks rolling confidence scores for a session.
    Used to detect sustained low confidence requiring escalation.
    """

    def __init__(self, sid: str, window: int = 5):
        self.sid = sid
        self._window = window
        self._scores: list[float] = []
        self._lock = threading.Lock()

    def record(self, report: ConfidenceReport):
        with self._lock:
            self._scores.append(report.final_score)
            if len(self._scores) > self._window * 3:
                self._scores.pop(0)

    def rolling_average(self, window: int | None = None) -> float:
        w = window or self._window
        with self._lock:
            recent = self._scores[-w:]
        if not recent:
            return 1.0
        return sum(recent) / len(recent)

    def is_sustained_low(self) -> bool:
        return self.rolling_average() < LOW_CONFIDENCE_THRESHOLD

    def is_sustained_critical(self) -> bool:
        return self.rolling_average() < HITL_ESCALATION_THRESHOLD

    def summary(self) -> dict[str, Any]:
        avg = self.rolling_average()
        return {
            "sid":              self.sid,
            "rolling_avg":      round(avg, 3),
            "level":            _level(avg).value,
            "sustained_low":    self.is_sustained_low(),
            "sustained_critical": self.is_sustained_critical(),
            "sample_count":     len(self._scores),
        }


_trackers: dict[str, SessionConfidenceTracker] = {}
_tracker_lock = threading.Lock()


def get_tracker(sid: str) -> SessionConfidenceTracker:
    with _tracker_lock:
        if sid not in _trackers:
            _trackers[sid] = SessionConfidenceTracker(sid)
        return _trackers[sid]


def drop_tracker(sid: str):
    with _tracker_lock:
        _trackers.pop(sid, None)


# ── Aggregate telemetry ───────────────────────────────────────────────────────

def confidence_telemetry_snapshot() -> dict[str, Any]:
    with _tel_lock:
        total = len(_telemetry)
        if total == 0:
            return {"total_scored": 0, "hitl_escalations": 0, "low_pct": 0.0}
        hitl_count = sum(1 for e in _telemetry if e["hitl"])
        low_count  = sum(1 for e in _telemetry if e["level"] in ("low", "critical"))
        avg_score  = sum(e["score"] for e in _telemetry) / total
    return {
        "total_scored":     total,
        "hitl_escalations": hitl_count,
        "low_pct":          round(low_count / total * 100, 1),
        "avg_score":        round(avg_score, 3),
        "snapshot_ts":      time.time(),
    }
