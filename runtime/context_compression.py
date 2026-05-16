"""
runtime/context_compression.py — Phase Z26 Context Compression Pipeline
========================================================================
Beta-grade runtime context management for long-session stability.

Prevents token explosion and context degradation without fake AGI memory.
This is pure runtime bookkeeping — no semantic retrieval, no vector ops.

FUTURE_RUNTIME_NOTE: If semantic long-term recall is added (e.g. chromadb
integration), it must remain loosely coupled via an injected retriever
interface. Never import vector_store directly from this module.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexora.context_compression")

# ── Token budget constants ────────────────────────────────────────────────────
DEFAULT_ACTIVE_WINDOW      = 40     # messages kept verbatim in active window
DEFAULT_EPISODE_BUDGET     = 8      # max compressed episode summaries retained
DEFAULT_CRITICAL_NOTES_MAX = 20     # hard cap on retained critical notes
DEFAULT_TOTAL_TOKEN_BUDGET = 28_000 # soft token budget before compression fires

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ContextMessage:
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    tokens: int = 0
    message_id: str = ""

    def __post_init__(self):
        if not self.message_id:
            self.message_id = hashlib.md5(
                f"{self.role}{self.content[:64]}{self.ts}".encode()
            ).hexdigest()[:12]
        if not self.tokens:
            self.tokens = _estimate_tokens(self.content)


@dataclass
class EpisodeSummary:
    summary_text: str
    source_message_ids: list[str]
    compressed_at: float = field(default_factory=time.time)
    token_count: int = 0
    episode_index: int = 0
    provenance_hash: str = ""

    def __post_init__(self):
        if not self.token_count:
            self.token_count = _estimate_tokens(self.summary_text)
        if not self.provenance_hash:
            joined = "".join(self.source_message_ids)
            self.provenance_hash = hashlib.sha256(joined.encode()).hexdigest()[:16]


@dataclass
class CriticalNote:
    note: str
    note_type: str          # "error", "decision", "constraint", "goal"
    added_at: float = field(default_factory=time.time)
    tokens: int = 0

    def __post_init__(self):
        if not self.tokens:
            self.tokens = _estimate_tokens(self.note)


# ── Token estimation (lightweight, no tiktoken dependency required) ───────────

def _estimate_tokens(text: str) -> int:
    """Fast token estimate: ~4 chars per token (GPT-4 approximation)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── Compression audit log ─────────────────────────────────────────────────────

_audit_log: deque[dict] = deque(maxlen=500)
_audit_lock = threading.Lock()


def _audit(event: str, details: dict):
    entry = {"event": event, "ts": time.time(), **details}
    with _audit_lock:
        _audit_log.append(entry)
    logger.debug("[CtxCompress] %s | %s", event, details)


def get_compression_audit_log(limit: int = 50) -> list[dict]:
    with _audit_lock:
        return list(_audit_log)[-limit:]


# ── Main compression context per session ─────────────────────────────────────

class SessionContext:
    """
    Manages the rolling context window for a single session.

    Layout (outermost → innermost in prompt):
        [system_prompt]
        [critical_notes]       ← always retained
        [episode_summaries]    ← compressed history
        [active_window]        ← recent verbatim messages
    """

    def __init__(
        self,
        sid: str,
        active_window_size: int = DEFAULT_ACTIVE_WINDOW,
        episode_budget: int = DEFAULT_EPISODE_BUDGET,
        critical_notes_max: int = DEFAULT_CRITICAL_NOTES_MAX,
        token_budget: int = DEFAULT_TOTAL_TOKEN_BUDGET,
    ):
        self.sid = sid
        self.active_window_size = active_window_size
        self.episode_budget = episode_budget
        self.critical_notes_max = critical_notes_max
        self.token_budget = token_budget

        self._lock = threading.Lock()
        self._active: deque[ContextMessage] = deque(maxlen=active_window_size * 2)
        self._episodes: deque[EpisodeSummary] = deque(maxlen=episode_budget)
        self._critical_notes: list[CriticalNote] = []
        self._episode_counter = 0
        self._total_messages_ever = 0
        self._last_compression_ts: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def add_message(self, role: str, content: str, tokens: int = 0) -> ContextMessage:
        msg = ContextMessage(role=role, content=content, tokens=tokens or _estimate_tokens(content))
        with self._lock:
            self._active.append(msg)
            self._total_messages_ever += 1
        self._maybe_compress()
        return msg

    def add_critical_note(self, note: str, note_type: str = "decision"):
        cn = CriticalNote(note=note, note_type=note_type)
        with self._lock:
            self._critical_notes.append(cn)
            if len(self._critical_notes) > self.critical_notes_max:
                removed = self._critical_notes.pop(0)
                _audit("critical_note_evicted", {"sid": self.sid, "type": removed.note_type})
        _audit("critical_note_added", {"sid": self.sid, "type": note_type, "tokens": cn.tokens})

    def compress(self, summarizer_fn=None) -> EpisodeSummary | None:
        """
        Compress the oldest half of the active window into an episode summary.
        summarizer_fn: optional callable(messages: list[dict]) -> str
        If not provided, a plain-text rollup summary is generated.
        """
        with self._lock:
            active_list = list(self._active)
            if len(active_list) < self.active_window_size:
                return None

            cutoff = len(active_list) // 2
            to_compress = active_list[:cutoff]
            keep = active_list[cutoff:]

            self._active.clear()
            for m in keep:
                self._active.append(m)

        msg_dicts = [{"role": m.role, "content": m.content} for m in to_compress]
        source_ids = [m.message_id for m in to_compress]

        if summarizer_fn:
            try:
                summary_text = summarizer_fn(msg_dicts)
            except Exception as exc:
                logger.warning("[CtxCompress] summarizer_fn failed: %s — using fallback", exc)
                summary_text = _plain_rollup(msg_dicts)
        else:
            summary_text = _plain_rollup(msg_dicts)

        self._episode_counter += 1
        ep = EpisodeSummary(
            summary_text=summary_text,
            source_message_ids=source_ids,
            episode_index=self._episode_counter,
        )

        with self._lock:
            self._episodes.append(ep)
            self._last_compression_ts = time.time()

        _audit("episode_compressed", {
            "sid": self.sid,
            "episode_index": ep.episode_index,
            "messages_compressed": len(to_compress),
            "summary_tokens": ep.token_count,
            "provenance_hash": ep.provenance_hash,
        })

        return ep

    def build_prompt_context(self) -> list[dict[str, str]]:
        """
        Assemble the full context list ready to pass to an LLM.
        Order: critical_notes → episodes → active_window
        """
        result: list[dict[str, str]] = []

        with self._lock:
            notes = list(self._critical_notes)
            episodes = list(self._episodes)
            active = list(self._active)

        if notes:
            notes_text = "\n".join(
                f"[{n.note_type.upper()}] {n.note}" for n in notes
            )
            result.append({"role": "system", "content": f"RETAINED OPERATIONAL NOTES:\n{notes_text}"})

        for ep in episodes:
            result.append({
                "role": "system",
                "content": (
                    f"[EPISODE {ep.episode_index} SUMMARY | "
                    f"provenance:{ep.provenance_hash}]\n{ep.summary_text}"
                ),
            })

        for msg in active:
            result.append({"role": msg.role, "content": msg.content})

        return result

    def token_usage(self) -> dict[str, int]:
        with self._lock:
            active_tokens  = sum(m.tokens for m in self._active)
            episode_tokens = sum(e.token_count for e in self._episodes)
            note_tokens    = sum(n.tokens for n in self._critical_notes)
        return {
            "active_window":   active_tokens,
            "episode_summaries": episode_tokens,
            "critical_notes":  note_tokens,
            "total":           active_tokens + episode_tokens + note_tokens,
            "budget":          self.token_budget,
            "budget_pct":      round((active_tokens + episode_tokens + note_tokens) / max(1, self.token_budget) * 100, 1),
        }

    def verify_replay_compatibility(self) -> dict[str, Any]:
        """
        Check that episode provenance is intact and replay would be consistent.
        Returns a compatibility report dict.
        """
        with self._lock:
            episodes = list(self._episodes)

        issues = []
        for ep in episodes:
            recomputed = hashlib.sha256(
                "".join(ep.source_message_ids).encode()
            ).hexdigest()[:16]
            if recomputed != ep.provenance_hash:
                issues.append({
                    "episode_index": ep.episode_index,
                    "expected": ep.provenance_hash,
                    "computed": recomputed,
                })

        return {
            "sid": self.sid,
            "episodes_checked": len(episodes),
            "replay_safe": len(issues) == 0,
            "integrity_issues": issues,
            "checked_at": time.time(),
        }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sid":                   self.sid,
                "active_messages":       len(self._active),
                "episode_count":         len(self._episodes),
                "critical_note_count":   len(self._critical_notes),
                "total_messages_ever":   self._total_messages_ever,
                "last_compression_ts":   self._last_compression_ts,
                "token_usage":           self.token_usage(),
            }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _maybe_compress(self):
        usage = self.token_usage()
        if usage["total"] > self.token_budget:
            _audit("auto_compress_triggered", {
                "sid": self.sid,
                "total_tokens": usage["total"],
                "budget": self.token_budget,
            })
            self.compress()


# ── Plain-text rollup (no LLM required) ──────────────────────────────────────

def _plain_rollup(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        snippet = content[:200].replace("\n", " ") + ("…" if len(content) > 200 else "")
        lines.append(f"[{role}] {snippet}")
    return "Prior context summary:\n" + "\n".join(lines)


# ── Session registry ──────────────────────────────────────────────────────────

_sessions: dict[str, SessionContext] = {}
_sessions_lock = threading.Lock()


def get_session_context(sid: str, **kwargs) -> SessionContext:
    with _sessions_lock:
        if sid not in _sessions:
            _sessions[sid] = SessionContext(sid=sid, **kwargs)
        return _sessions[sid]


def drop_session_context(sid: str):
    with _sessions_lock:
        if sid in _sessions:
            del _sessions[sid]
            _audit("session_context_dropped", {"sid": sid})


def list_active_sessions() -> list[str]:
    with _sessions_lock:
        return list(_sessions.keys())


# ── Telemetry snapshot ────────────────────────────────────────────────────────

def compression_telemetry() -> dict[str, Any]:
    with _sessions_lock:
        session_stats = [s.stats() for s in _sessions.values()]
    return {
        "active_session_count": len(session_stats),
        "sessions":             session_stats,
        "audit_log_size":       len(_audit_log),
        "snapshot_ts":          time.time(),
    }
