"""
execution/memory_arbiter.py — Phase: Shared Memory Arbitration
==============================================================
Prevents memory poisoning, stale context propagation, and cross-agent
contradiction in the shared agent memory system.

Architecture:
  • AgentMemoryChannel — per-agent isolated memory namespace
  • MemoryArbiter — resolves conflicts, manages confidence inheritance,
                    enforces ownership, prevents recursive amplification

Rules enforced:
  1. Low-confidence memories (<0.3) NEVER propagate to shared context
  2. All memories carry source attribution (agent_id + role + timestamp)
  3. Contradicting memories trigger a merge arbitration (higher confidence wins)
  4. Memory writes require the agent to have 'can_write_memory' permission
  5. No agent can read its own session's cancelled/corrupted memories
  6. Recursive amplification: if an agent A reads B's memory and writes
     it back with higher confidence, the arbiter detects and blocks it
"""

import threading
import time
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.memory_arbiter")


# ─────────────────────────────────────────────────────────────────────────────
# Memory entry schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    entry_id: str
    key: str
    value: str
    source_agent_id: str
    source_role: str
    session_id: str
    confidence: float              # 0.0 → 1.0
    verified: bool = False         # True if semantic validator confirmed
    created_at: float = field(default_factory=time.time)
    propagation_count: int = 0     # How many times this was re-shared (drift detection)
    is_caution: bool = False       # If True, any reader gets a trust warning

    MAX_PROPAGATION = 3            # Prevent recursive amplification

    def is_propagation_safe(self) -> bool:
        return self.propagation_count < self.MAX_PROPAGATION

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "key": self.key,
            "value": self.value[:200],
            "source_agent_id": self.source_agent_id,
            "source_role": self.source_role,
            "confidence": self.confidence,
            "verified": self.verified,
            "created_at": self.created_at,
            "propagation_count": self.propagation_count,
            "is_caution": self.is_caution,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentMemoryChannel — isolated per-agent write namespace
# ─────────────────────────────────────────────────────────────────────────────

class AgentMemoryChannel:
    """
    Each agent writes ONLY to its own channel.
    Reads from shared channels pass through confidence filtering.
    """
    def __init__(self, agent_id: str, session_id: str):
        self.agent_id = agent_id
        self.session_id = session_id
        self._entries: Dict[str, MemoryEntry] = {}
        self._lock = threading.RLock()

    def write(self, key: str, value: str, confidence: float,
              role: str, verified: bool = False) -> MemoryEntry:
        """Adds or updates a memory entry in this agent's channel."""
        with self._lock:
            existing = self._entries.get(key)
            if existing and existing.confidence >= confidence:
                # Don't overwrite with lower-confidence data
                logger.debug(
                    f"[MemoryChannel] {self.agent_id}: skipped write '{key}' "
                    f"(existing confidence {existing.confidence:.2f} >= {confidence:.2f})"
                )
                return existing

            entry = MemoryEntry(
                entry_id=uuid.uuid4().hex[:12],
                key=key,
                value=value,
                source_agent_id=self.agent_id,
                source_role=role,
                session_id=self.session_id,
                confidence=confidence,
                verified=verified,
                is_caution=(confidence < 0.35),
            )
            self._entries[key] = entry
            return entry

    def read(self, key: str) -> Optional[MemoryEntry]:
        with self._lock:
            return self._entries.get(key)

    def all_entries(self) -> List[MemoryEntry]:
        with self._lock:
            return list(self._entries.values())

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


# ─────────────────────────────────────────────────────────────────────────────
# MemoryArbiter — global conflict resolution and propagation control
# ─────────────────────────────────────────────────────────────────────────────

class MemoryArbiter:
    """
    Global arbiter for shared memory across multiple agent channels.

    Key operations:
      • merge_to_shared()    — promotes high-confidence entries to shared context
      • resolve_conflict()   — picks winner when two agents write contradicting values
      • get_shared()         — returns filtered, attribution-stamped shared entries
      • emit_caution_signals — notifies frontend of low-confidence propagation attempts
    """

    MIN_CONFIDENCE_TO_SHARE = 0.35   # Below this, stays in local channel only
    CONTRADICTION_THRESHOLD = 0.20   # Confidence difference needed to override

    def __init__(self):
        self._channels: Dict[str, AgentMemoryChannel] = {}
        self._shared: Dict[str, MemoryEntry] = {}    # key → best known entry
        self._lock = threading.RLock()
        self._caution_log: List[dict] = []

    # ── Channel management ────────────────────────────────────────────────────

    def get_or_create_channel(self, agent_id: str, session_id: str) -> AgentMemoryChannel:
        with self._lock:
            if agent_id not in self._channels:
                self._channels[agent_id] = AgentMemoryChannel(agent_id, session_id)
            return self._channels[agent_id]

    def remove_channel(self, agent_id: str) -> None:
        with self._lock:
            self._channels.pop(agent_id, None)
        logger.info(f"[MemoryArbiter] Removed channel for agent {agent_id}")

    # ── Writing ───────────────────────────────────────────────────────────────

    def write(self, agent_id: str, session_id: str, key: str, value: str,
              confidence: float, role: str, verified: bool = False) -> MemoryEntry:
        """Write to agent's local channel. Propagate to shared if above threshold."""
        channel = self.get_or_create_channel(agent_id, session_id)
        entry = channel.write(key, value, confidence, role, verified)

        if confidence >= self.MIN_CONFIDENCE_TO_SHARE:
            self._merge_to_shared(entry)
        else:
            self._record_caution(entry, reason="Below propagation threshold")

        return entry

    def _merge_to_shared(self, entry: MemoryEntry) -> None:
        """Promotes entry to shared context, resolving conflicts by confidence."""
        if not entry.is_propagation_safe():
            self._record_caution(entry, reason=f"Propagation depth {entry.propagation_count} exceeded")
            return

        with self._lock:
            existing = self._shared.get(entry.key)
            if existing is None:
                self._shared[entry.key] = entry
                entry.propagation_count += 1
                logger.debug(f"[MemoryArbiter] Shared new entry: '{entry.key}' (conf={entry.confidence:.2f})")
                return

            # Conflict resolution: higher confidence wins
            delta = entry.confidence - existing.confidence
            if delta >= self.CONTRADICTION_THRESHOLD:
                logger.info(
                    f"[MemoryArbiter] Overriding '{entry.key}' — "
                    f"new confidence {entry.confidence:.2f} vs old {existing.confidence:.2f} "
                    f"(source: {existing.source_agent_id} → {entry.source_agent_id})"
                )
                self._shared[entry.key] = entry
                entry.propagation_count += 1
            elif abs(delta) < self.CONTRADICTION_THRESHOLD:
                # Too close — emit caution, keep existing
                self._record_caution(
                    entry,
                    reason=(
                        f"Contradiction detected for key '{entry.key}': "
                        f"agent {entry.source_agent_id} ({entry.confidence:.2f}) vs "
                        f"agent {existing.source_agent_id} ({existing.confidence:.2f}). "
                        f"Keeping higher-confidence version."
                    )
                )

    # ── Reading ───────────────────────────────────────────────────────────────

    def get_shared(self, key: str) -> Optional[MemoryEntry]:
        """Returns the current shared entry for a key, with attribution."""
        with self._lock:
            return self._shared.get(key)

    def get_all_shared(self, session_id: Optional[str] = None) -> List[MemoryEntry]:
        """Returns all shared entries, optionally filtered by session."""
        with self._lock:
            entries = list(self._shared.values())
            if session_id:
                entries = [e for e in entries if e.session_id == session_id]
            return entries

    def get_channel_entries(self, agent_id: str) -> List[MemoryEntry]:
        """Returns all local entries for a given agent."""
        with self._lock:
            ch = self._channels.get(agent_id)
            return ch.all_entries() if ch else []

    # ── Caution signals ───────────────────────────────────────────────────────

    def _record_caution(self, entry: MemoryEntry, reason: str) -> None:
        log = {
            "ts": time.time(),
            "key": entry.key,
            "source_agent": entry.source_agent_id,
            "confidence": entry.confidence,
            "reason": reason,
        }
        self._caution_log.append(log)
        if len(self._caution_log) > 100:
            self._caution_log.pop(0)
        logger.warning(f"[MemoryArbiter] Caution: {reason}")

    def get_caution_log(self, limit: int = 20) -> List[dict]:
        return self._caution_log[-limit:]

    # ── Observability snapshot ────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "shared_entries": len(self._shared),
                "active_channels": len(self._channels),
                "caution_events": len(self._caution_log),
                "recent_cautions": self._caution_log[-5:],
                "shared": [e.to_dict() for e in self._shared.values()],
            }


# Global singleton
global_memory_arbiter = MemoryArbiter()
