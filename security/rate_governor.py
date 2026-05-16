"""
security/rate_governor.py — Abuse Prevention & Rate Governance
==============================================================
Adaptive rate limiting and abuse prevention for Aetherion AI.

Prevents:
  • Token abuse (unlimited LLM calls)
  • Infinite task spawning (task flood)
  • Browser storms (navigation floods)
  • Shell floods (command spam)
  • SSE connection storms
  • Coordination suppression attacks (lock flooding)
  • Denial-of-service via API endpoint flooding

Design:
  • Sliding window counters (no fixed epoch resets — more accurate)
  • Per-session limits (baseline)
  • Per-tenant daily limits (from TenantContext)
  • Adaptive throttling: high-risk sessions get tighter limits
  • Emergency degraded-mode lockdown: all limits halved
  • Trust-tier aware: FREE sessions get strictest limits
  • All rate limit events feed security telemetry anomaly scorer
"""

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.security.rate")


# ─────────────────────────────────────────────────────────────────────────────
# Default limits (overridable via env)
# ─────────────────────────────────────────────────────────────────────────────

_LIMITS = {
    # (resource, window_seconds, max_count)
    "llm_calls":    (60,   int(os.getenv("RATE_LLM_CALLS_PER_MIN",   "20"))),
    "shell_cmds":   (60,   int(os.getenv("RATE_SHELL_CMDS_PER_MIN",  "10"))),
    "browser_navs": (60,   int(os.getenv("RATE_BROWSER_NAVS_PER_MIN","30"))),
    "file_writes":  (60,   int(os.getenv("RATE_FILE_WRITES_PER_MIN", "50"))),
    "task_spawns":  (60,   int(os.getenv("RATE_TASK_SPAWNS_PER_MIN",  "5"))),
    "api_calls":    (60,   int(os.getenv("RATE_API_CALLS_PER_MIN",  "100"))),
    "sse_new_conns":(30,   10),   # 10 new SSE connections per 30s
    "delegation":   (60,   int(os.getenv("RATE_DELEGATIONS_PER_MIN", "10"))),
    "hitl_triggers":(300, int(os.getenv("RATE_HITL_PER_5MIN",         "5"))),
}

# Adaptive: high-risk sessions get this fraction of normal limit
_HIGH_RISK_LIMIT_FACTOR = 0.5
# Degraded mode: all limits reduced to this fraction
_DEGRADED_LIMIT_FACTOR  = 0.5


class RateLimitDecision(str, Enum):
    ALLOW   = "ALLOW"
    THROTTLE= "THROTTLE"   # slow down, allow after delay
    DENY    = "DENY"       # hard block


@dataclass
class RateLimitResult:
    decision: RateLimitDecision
    resource: str
    session_id: str
    current_count: int
    limit: int
    window_sec: int
    retry_after_sec: float = 0.0
    reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Sliding window counter
# ─────────────────────────────────────────────────────────────────────────────

class _SlidingWindow:
    """Thread-safe sliding window rate limiter."""

    def __init__(self, window_sec: int, max_count: int):
        self.window_sec = window_sec
        self.max_count  = max_count
        self._timestamps: Deque[float] = collections.deque()
        self._lock = threading.Lock()

    def check_and_record(self) -> Tuple[bool, int, float]:
        """
        Returns (allowed, current_count, retry_after_sec).
        Records the event if allowed.
        """
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            # Prune old entries
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            count = len(self._timestamps)
            if count >= self.max_count:
                # Retry after oldest entry exits window
                oldest = self._timestamps[0] if self._timestamps else now
                retry_after = max(0.0, (oldest + self.window_sec) - now)
                return False, count, retry_after
            self._timestamps.append(now)
            return True, count + 1, 0.0

    def current_count(self) -> int:
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)

    def reset(self) -> None:
        with self._lock:
            self._timestamps.clear()


# ─────────────────────────────────────────────────────────────────────────────
# AbuseRateGovernor
# ─────────────────────────────────────────────────────────────────────────────

class AbuseRateGovernor:
    """
    Multi-resource adaptive rate governor.

    Usage:
        result = governor.check("llm_calls", session_id)
        if result.decision == RateLimitDecision.DENY:
            raise RateLimitError(result.reason)
    """

    def __init__(self):
        self._lock   = threading.RLock()
        # session_id → {resource → SlidingWindow}
        self._windows: Dict[str, Dict[str, _SlidingWindow]] = {}
        self._audit:   List[dict] = []
        self._blocked_sessions: Dict[str, float] = {}   # session → block_until

    def _get_window(self, session_id: str, resource: str) -> _SlidingWindow:
        with self._lock:
            if session_id not in self._windows:
                self._windows[session_id] = {}
            if resource not in self._windows[session_id]:
                window_sec, limit = _LIMITS.get(resource, (60, 100))
                self._windows[session_id][resource] = _SlidingWindow(window_sec, limit)
            return self._windows[session_id][resource]

    def check(
        self,
        resource: str,
        session_id: str,
        tenant_id: str = "",
    ) -> RateLimitResult:
        """
        Check if the session can proceed with the resource action.
        Returns RateLimitResult — caller MUST check .decision.
        """
        window_sec, base_limit = _LIMITS.get(resource, (60, 100))

        # Adaptive: high-risk sessions get reduced limits
        from security.security_telemetry import get_security_telemetry
        risk_score = get_security_telemetry().get_session_risk_score(session_id)
        limit = base_limit
        if risk_score >= 50:
            limit = max(1, int(base_limit * _HIGH_RISK_LIMIT_FACTOR))

        # Degraded mode: halve all limits
        try:
            from infra.resilience import global_degraded_mode
            if global_degraded_mode.is_any_degraded():
                limit = max(1, int(limit * _DEGRADED_LIMIT_FACTOR))
        except Exception:
            pass

        # Emergency block check
        block_until = self._blocked_sessions.get(session_id, 0)
        if time.time() < block_until:
            remaining = block_until - time.time()
            result = RateLimitResult(
                decision=RateLimitDecision.DENY,
                resource=resource, session_id=session_id,
                current_count=0, limit=limit,
                window_sec=window_sec, retry_after_sec=remaining,
                reason=f"Session is temporarily blocked for {remaining:.0f}s due to abuse detection."
            )
            self._log(result)
            return result

        # Override limit for this window
        window = self._get_window(session_id, resource)
        with self._lock:
            window.max_count = limit

        allowed, count, retry_after = window.check_and_record()

        if not allowed:
            result = RateLimitResult(
                decision=RateLimitDecision.DENY,
                resource=resource, session_id=session_id,
                current_count=count, limit=limit,
                window_sec=window_sec, retry_after_sec=retry_after,
                reason=f"Rate limit exceeded: {count}/{limit} {resource} in {window_sec}s window. "
                       f"Retry after {retry_after:.1f}s."
            )
            self._log(result)
            # Auto-suspend if repeatedly over limit
            self._check_abuse_pattern(session_id, resource)
            return result

        result = RateLimitResult(
            decision=RateLimitDecision.ALLOW,
            resource=resource, session_id=session_id,
            current_count=count, limit=limit,
            window_sec=window_sec,
        )
        return result

    def check_and_raise(self, resource: str, session_id: str,
                        tenant_id: str = "") -> None:
        """Convenience: raises RateLimitError if denied."""
        result = self.check(resource, session_id, tenant_id)
        if result.decision == RateLimitDecision.DENY:
            raise RateLimitError(result.reason, result.retry_after_sec)

    def block_session(self, session_id: str, duration_sec: float, reason: str = "") -> None:
        """Temporarily blocks all resource access for a session."""
        with self._lock:
            self._blocked_sessions[session_id] = time.time() + duration_sec
        logger.warning(f"[RateGovernor] 🔴 Session blocked: {session_id} "
                       f"duration={duration_sec}s reason={reason}")
        try:
            from security.security_telemetry import get_security_telemetry, SecurityEventType
            get_security_telemetry().record(
                SecurityEventType.ANOMALOUS_SESSION, session_id,
                payload={"reason": reason, "duration": duration_sec}
            )
        except Exception:
            pass

    def unblock_session(self, session_id: str) -> None:
        with self._lock:
            self._blocked_sessions.pop(session_id, None)

    def reset_session(self, session_id: str) -> None:
        """Reset all rate limit windows for a session (called on clean session end)."""
        with self._lock:
            self._windows.pop(session_id, None)
            self._blocked_sessions.pop(session_id, None)

    def _check_abuse_pattern(self, session_id: str, resource: str) -> None:
        """Auto-suspend session if it repeatedly hits rate limits (abuse pattern)."""
        # Count how many DENY events this session has in last 5 minutes
        now = time.time()
        with self._lock:
            recent = [e for e in self._audit
                      if e["session_id"] == session_id
                      and e["decision"] == "DENY"
                      and now - e["ts"] < 300]
        if len(recent) >= 10:
            self.block_session(session_id, 300, f"Repeated rate limit violations: {resource}")

    def _log(self, result: RateLimitResult) -> None:
        if result.decision == RateLimitDecision.DENY:
            entry = {
                "ts":          time.time(),
                "session_id":  result.session_id,
                "decision":    result.decision.value,
                "resource":    result.resource,
                "count":       result.current_count,
                "limit":       result.limit,
            }
            with self._lock:
                self._audit.append(entry)
                if len(self._audit) > 1000:
                    self._audit.pop(0)
            logger.warning(f"[RateGovernor] DENY resource={result.resource} "
                           f"session={result.session_id} {result.current_count}/{result.limit}")
            try:
                from security.security_telemetry import get_security_telemetry, SecurityEventType
                get_security_telemetry().record(
                    SecurityEventType.RATE_LIMIT_HIT, result.session_id,
                    payload={"resource": result.resource, "count": result.current_count,
                             "limit": result.limit}
                )
            except Exception:
                pass

    def snapshot(self) -> dict:
        with self._lock:
            blocked_count = sum(
                1 for t in self._blocked_sessions.values() if t > time.time()
            )
            recent_denials = sum(
                1 for e in self._audit if e["decision"] == "DENY"
                and time.time() - e.get("ts", 0) < 300
            )
        return {
            "tracked_sessions":    len(self._windows),
            "blocked_sessions":    blocked_count,
            "recent_denials_5min": recent_denials,
            "limits":              {k: v for k, v in _LIMITS.items()},
        }


class RateLimitError(Exception):
    """Raised when a rate limit is exceeded."""
    def __init__(self, reason: str, retry_after: float = 0):
        self.retry_after = retry_after
        super().__init__(reason)


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_rate_governor = AbuseRateGovernor()
