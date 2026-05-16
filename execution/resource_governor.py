"""
execution/resource_governor.py — Phase: Resource Governance Engine
==================================================================
Governs ALL shared runtime resources across concurrent agent executions:
  • Token budgets (per-session, global)
  • Browser session pool (max concurrent browsers)
  • Terminal/subprocess slots
  • Memory allocation tracking
  • Retry storm prevention

All limits are hard, enforced in real-time, and emit SSE trust signals on violation.
"""

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
from contextlib import contextmanager

logger = logging.getLogger("nexora.resource_governor")


# ─────────────────────────────────────────────────────────────────────────────
# Tuneable global limits (overridable via env)
# ─────────────────────────────────────────────────────────────────────────────

import os

MAX_CONCURRENT_BROWSERS    = int(os.getenv("MAX_BROWSERS", "2"))
MAX_CONCURRENT_TERMINALS   = int(os.getenv("MAX_TERMINALS", "4"))
MAX_TOKENS_PER_SESSION     = int(os.getenv("MAX_TOKENS_SESSION", "200000"))
MAX_TOKENS_GLOBAL          = int(os.getenv("MAX_TOKENS_GLOBAL", "2000000"))
MAX_RETRIES_STORM_WINDOW   = int(os.getenv("MAX_RETRIES_STORM", "15"))  # max retries in 60s
RETRY_STORM_WINDOW_SEC     = 60


class ResourceExhaustedError(Exception):
    """Raised when a resource limit is breached and the agent must pause/escalate."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Per-session resource counters
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionResourceState:
    session_id: str
    tokens_used: int = 0
    retry_timestamps: list = field(default_factory=list)
    browser_slots_held: int = 0
    terminal_slots_held: int = 0

    def record_retry(self) -> bool:
        """Returns True if retry storm threshold is breached."""
        now = time.time()
        self.retry_timestamps = [t for t in self.retry_timestamps if now - t < RETRY_STORM_WINDOW_SEC]
        self.retry_timestamps.append(now)
        return len(self.retry_timestamps) >= MAX_RETRIES_STORM_WINDOW


# ─────────────────────────────────────────────────────────────────────────────
# Resource Governor (singleton)
# ─────────────────────────────────────────────────────────────────────────────

class ResourceGovernor:
    """
    Central runtime resource registry.
    Thread-safe. Enforces hard limits. Raises ResourceExhaustedError to
    trigger HITL escalation or graceful degradation.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionResourceState] = {}
        self._global_tokens_used: int = 0
        self._browser_semaphore = threading.Semaphore(MAX_CONCURRENT_BROWSERS)
        self._terminal_semaphore = threading.Semaphore(MAX_CONCURRENT_TERMINALS)
        self._browser_owners: Dict[str, str] = {}   # slot_id -> session_id
        self._terminal_owners: Dict[str, str] = {}  # slot_id -> session_id

    def _get_session(self, session_id: str) -> SessionResourceState:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionResourceState(session_id=session_id)
            return self._sessions[session_id]

    # ── Token budget ──────────────────────────────────────────────────────────

    def charge_tokens(self, session_id: str, count: int) -> None:
        """Charges tokens to session + global counter. Raises if either limit exceeded."""
        sess = self._get_session(session_id)
        with self._lock:
            if sess.tokens_used + count > MAX_TOKENS_PER_SESSION:
                raise ResourceExhaustedError(
                    f"Session {session_id} token budget exhausted "
                    f"({sess.tokens_used}/{MAX_TOKENS_PER_SESSION})"
                )
            if self._global_tokens_used + count > MAX_TOKENS_GLOBAL:
                raise ResourceExhaustedError(
                    f"Global token budget exhausted "
                    f"({self._global_tokens_used}/{MAX_TOKENS_GLOBAL})"
                )
            sess.tokens_used += count
            self._global_tokens_used += count
            logger.debug(f"[ResourceGovernor] session={session_id} tokens={sess.tokens_used}/{MAX_TOKENS_PER_SESSION}")

    def get_token_usage(self, session_id: str) -> dict:
        sess = self._get_session(session_id)
        return {
            "session_tokens": sess.tokens_used,
            "session_limit": MAX_TOKENS_PER_SESSION,
            "global_tokens": self._global_tokens_used,
            "global_limit": MAX_TOKENS_GLOBAL,
        }

    # ── Retry storm detection ─────────────────────────────────────────────────

    def record_retry(self, session_id: str) -> bool:
        """Returns True if retry storm detected. Caller should escalate to HITL."""
        sess = self._get_session(session_id)
        is_storm = sess.record_retry()
        if is_storm:
            logger.warning(
                f"[ResourceGovernor] RETRY STORM detected: session={session_id} "
                f"({len(sess.retry_timestamps)} retries in {RETRY_STORM_WINDOW_SEC}s)"
            )
        return is_storm

    # ── Browser session pool ──────────────────────────────────────────────────

    @contextmanager
    def browser_slot(self, session_id: str, timeout: float = 30.0):
        """
        Context manager that acquires a browser slot or raises ResourceExhaustedError.
        Guarantees release even on exceptions.
        """
        acquired = self._browser_semaphore.acquire(timeout=timeout)
        if not acquired:
            raise ResourceExhaustedError(
                f"Browser pool exhausted (max {MAX_CONCURRENT_BROWSERS} concurrent). "
                f"Session {session_id} must wait or be queued."
            )
        slot_id = f"browser_{session_id}_{int(time.time()*1000)}"
        with self._lock:
            self._browser_owners[slot_id] = session_id
        logger.info(f"[ResourceGovernor] Browser slot acquired: {slot_id}")
        try:
            yield slot_id
        finally:
            with self._lock:
                self._browser_owners.pop(slot_id, None)
            self._browser_semaphore.release()
            logger.info(f"[ResourceGovernor] Browser slot released: {slot_id}")

    # ── Terminal/subprocess slots ─────────────────────────────────────────────

    @contextmanager
    def terminal_slot(self, session_id: str, timeout: float = 10.0):
        """
        Context manager that acquires a terminal slot or raises ResourceExhaustedError.
        """
        acquired = self._terminal_semaphore.acquire(timeout=timeout)
        if not acquired:
            raise ResourceExhaustedError(
                f"Terminal pool exhausted (max {MAX_CONCURRENT_TERMINALS} concurrent). "
                f"Session {session_id} must queue."
            )
        slot_id = f"term_{session_id}_{int(time.time()*1000)}"
        with self._lock:
            self._terminal_owners[slot_id] = session_id
        logger.info(f"[ResourceGovernor] Terminal slot acquired: {slot_id}")
        try:
            yield slot_id
        finally:
            with self._lock:
                self._terminal_owners.pop(slot_id, None)
            self._terminal_semaphore.release()
            logger.info(f"[ResourceGovernor] Terminal slot released: {slot_id}")

    # ── Observability snapshot ────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Returns a live snapshot of resource state for SSE/frontend rendering."""
        with self._lock:
            sessions_summary = {
                sid: {
                    "tokens_used": s.tokens_used,
                    "retry_count_60s": len([t for t in s.retry_timestamps
                                            if time.time() - t < RETRY_STORM_WINDOW_SEC]),
                }
                for sid, s in self._sessions.items()
            }
            return {
                "global_tokens_used": self._global_tokens_used,
                "global_token_limit": MAX_TOKENS_GLOBAL,
                "active_browsers": MAX_CONCURRENT_BROWSERS - self._browser_semaphore._value,
                "max_browsers": MAX_CONCURRENT_BROWSERS,
                "active_terminals": MAX_CONCURRENT_TERMINALS - self._terminal_semaphore._value,
                "max_terminals": MAX_CONCURRENT_TERMINALS,
                "browser_owners": dict(self._browser_owners),
                "terminal_owners": dict(self._terminal_owners),
                "sessions": sessions_summary,
            }

    def reset_session(self, session_id: str) -> None:
        """Clears resource state for a completed/cancelled session."""
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.info(f"[ResourceGovernor] Session {session_id} resources cleared.")


# Global singleton
global_resource_governor = ResourceGovernor()
