"""
runtime/override_engine.py — Phase Z29C
=========================================
Live runtime override controls for operator-driven session tuning.

Operators can override:
  - provider (which LLM provider to use)
  - model    (specific model within provider)
  - retry_budget (max retries per step)
  - confidence_threshold (HITL escalation threshold)
  - execution_timeout (per-step timeout in seconds)
  - compression_aggressiveness (0.0–1.0, 0=lazy, 1=aggressive)

Design rules:
  - Overrides are per-session, stored in-memory + emitted via SSE
  - All overrides generate explainability records (DecisionRecord)
  - Overrides do NOT corrupt DAG state or break replay integrity
  - Overrides are cooperative — agent reads them between steps
  - Empty override dict means "use system defaults"
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nexora.override_engine")

# ── Override schema ────────────────────────────────────────────────────────────

OVERRIDE_KEYS = frozenset({
    "provider",
    "model",
    "retry_budget",
    "confidence_threshold",
    "execution_timeout",
    "compression_aggressiveness",
})

OVERRIDE_VALIDATORS: dict[str, Any] = {
    "retry_budget":              (int,   1,   20),
    "confidence_threshold":      (float, 0.0, 1.0),
    "execution_timeout":         (float, 5.0, 600.0),
    "compression_aggressiveness":(float, 0.0, 1.0),
}


def _validate_override(key: str, value: Any) -> tuple[bool, str, Any]:
    """
    Returns (valid, reason, coerced_value).
    """
    if key not in OVERRIDE_KEYS:
        return False, f"Unknown override key '{key}'", value

    if key in OVERRIDE_VALIDATORS:
        typ, lo, hi = OVERRIDE_VALIDATORS[key]
        try:
            v = typ(value)
        except (TypeError, ValueError):
            return False, f"Expected {typ.__name__} for '{key}', got {type(value).__name__}", value
        if not (lo <= v <= hi):
            return False, f"Value {v} out of range [{lo}, {hi}] for '{key}'", value
        return True, "ok", v

    # String overrides (provider, model) — basic sanitize
    if not isinstance(value, str) or not value.strip():
        return False, f"'{key}' must be a non-empty string", value
    return True, "ok", value.strip()[:64]


# ── Per-session override store ────────────────────────────────────────────────

@dataclass
class SessionOverrides:
    sid:      str
    values:   dict[str, Any] = field(default_factory=dict)
    history:  list[dict]     = field(default_factory=list)
    applied_at: float        = field(default_factory=time.time)

    def apply(self, key: str, value: Any, operator_note: str = "") -> tuple[bool, str]:
        ok, reason, coerced = _validate_override(key, value)
        if not ok:
            return False, reason
        prev = self.values.get(key)
        self.values[key] = coerced
        self.applied_at  = time.time()
        self.history.append({
            "key":      key,
            "prev":     prev,
            "new":      coerced,
            "note":     operator_note[:200],
            "ts":       self.applied_at,
        })
        return True, "ok"

    def clear(self, key: str) -> bool:
        if key in self.values:
            prev = self.values.pop(key)
            self.history.append({
                "key": key, "prev": prev, "new": None,
                "note": "cleared", "ts": time.time(),
            })
            return True
        return False

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def to_dict(self) -> dict:
        return {
            "sid":        self.sid,
            "overrides":  dict(self.values),
            "history":    list(self.history[-20:]),
            "applied_at": self.applied_at,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_lock     = threading.Lock()
_sessions: dict[str, SessionOverrides] = {}


def _get_or_create(sid: str) -> SessionOverrides:
    if sid not in _sessions:
        _sessions[sid] = SessionOverrides(sid=sid)
    return _sessions[sid]


# ── Public API ────────────────────────────────────────────────────────────────

def apply_override(
    sid:           str,
    key:           str,
    value:         Any,
    operator_note: str = "",
    emit_fn:       Callable | None = None,
) -> tuple[bool, str]:
    """
    Apply a single override. Returns (success, message).
    Generates an explainability record on success.
    """
    with _lock:
        sess = _get_or_create(sid)
        ok, reason = sess.apply(key, value, operator_note)

    if not ok:
        logger.warning(f"[OverrideEngine] Invalid override {key}={value} for {sid}: {reason}")
        return False, reason

    # Explainability record
    try:
        from runtime.explainability import record_decision, DecisionType
        record_decision(
            sid=sid,
            step_id="operator-override",
            decision_type="provider_switch" if key in ("provider", "model") else "execution_pause",
            summary=f"Override applied: {key} = {value!r}",
            reason_category="policy",
            contributing_factors=[
                f"key={key}", f"value={value!r}",
                f"note={operator_note[:60]}" if operator_note else "no_note",
            ],
            outcome=f"Runtime override active for {key}",
        )
    except Exception:
        pass

    if emit_fn:
        try:
            emit_fn("agent.override_applied", {
                "sid": sid, "key": key, "value": value,
                "note": operator_note,
            })
        except Exception:
            pass

    logger.info(f"[OverrideEngine] {sid} override: {key} = {value!r}")
    return True, "ok"


def apply_overrides_bulk(
    sid:           str,
    overrides:     dict[str, Any],
    operator_note: str = "",
    emit_fn:       Callable | None = None,
) -> dict[str, tuple[bool, str]]:
    """Apply multiple overrides at once. Returns per-key (success, msg) dict."""
    results = {}
    for k, v in overrides.items():
        results[k] = apply_override(sid, k, v, operator_note, emit_fn)
    return results


def clear_override(sid: str, key: str) -> bool:
    with _lock:
        sess = _sessions.get(sid)
        if not sess:
            return False
        return sess.clear(key)


def clear_all_overrides(sid: str) -> int:
    with _lock:
        sess = _sessions.get(sid)
        if not sess:
            return 0
        count = len(sess.values)
        sess.values.clear()
        sess.history.append({"key": "*", "prev": None, "new": None, "note": "all cleared", "ts": time.time()})
        return count


def get_overrides(sid: str) -> dict[str, Any]:
    with _lock:
        sess = _sessions.get(sid)
        return dict(sess.values) if sess else {}


def get_override(sid: str, key: str, default: Any = None) -> Any:
    with _lock:
        sess = _sessions.get(sid)
        return sess.get(key, default) if sess else default


def get_override_snapshot(sid: str) -> dict:
    with _lock:
        sess = _sessions.get(sid)
        return sess.to_dict() if sess else {"sid": sid, "overrides": {}, "history": []}


def list_all_overrides() -> list[dict]:
    with _lock:
        return [s.to_dict() for s in _sessions.values() if s.values]
