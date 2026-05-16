"""
execution/context_compression.py — Phase Z40A: Context Compression Engine
==========================================================================
Prevents long-session context overflow without losing operational continuity.

Subsystems:
  • ContextWindow        — sliding three-tier window (active / compressed / archived)
  • SemanticCompressor   — compresses repetitive traces and low-signal chunks
  • CompressionLedger    — tracks fidelity, reconstruction confidence, preservation scores
"""

import time
import hashlib
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("nexora.context_compression")

# ── Tier constants ─────────────────────────────────────────────────────────────
ACTIVE_MAX_ITEMS     = 50    # items kept in hot active window
COMPRESSED_MAX_ITEMS = 200   # compressed summaries retained
ARCHIVE_MAX_ITEMS    = 500   # archived entries (lineage-only, payload stripped)


# ── Context item ──────────────────────────────────────────────────────────────

@dataclass
class ContextItem:
    item_id:    str
    session_id: str
    content:    str
    item_type:  str          # "trace" | "reasoning" | "replay" | "chunk"
    timestamp:  float = field(default_factory=time.time)
    signal:     float = 1.0  # 0.0 = no signal, 1.0 = max signal
    compressed: bool  = False
    summary:    str   = ""
    lineage_id: str   = ""   # preserved even after compression

    def content_hash(self) -> str:
        return hashlib.sha1(self.content.encode("utf-8", errors="replace")).hexdigest()[:12]


# ── Compression block ─────────────────────────────────────────────────────────

@dataclass
class CompressionBlock:
    block_id:              str
    source_item_ids:       List[str]
    summary:               str
    fidelity_score:        float   # 0.0–1.0
    reconstruction_conf:   float   # 0.0–1.0
    semantic_preservation: float   # 0.0–1.0
    created_at:            float = field(default_factory=time.time)
    lineage_ids:           List[str] = field(default_factory=list)

    def overall_confidence(self) -> float:
        return round(
            self.fidelity_score * 0.40 +
            self.reconstruction_conf * 0.35 +
            self.semantic_preservation * 0.25,
            4
        )


# ── Semantic compressor ────────────────────────────────────────────────────────

class SemanticCompressor:
    """
    Compresses groups of context items into compact summaries.
    Never discards lineage_id — forensic traceability is always preserved.
    """

    # Items with signal below this are candidates for immediate compression
    LOW_SIGNAL_THRESHOLD = 0.30

    def should_compress(self, item: ContextItem) -> bool:
        return (
            item.signal < self.LOW_SIGNAL_THRESHOLD or
            item.compressed or
            (time.time() - item.timestamp) > 3600  # older than 1 hour
        )

    def compress_group(self, items: List[ContextItem], block_id: str) -> CompressionBlock:
        """
        Produces a CompressionBlock from a batch of ContextItems.
        Summary strategy: deduplicate content hashes + concatenate unique excerpts.
        """
        if not items:
            raise ValueError("Cannot compress empty item list")

        seen_hashes = set()
        unique_excerpts = []
        all_lineage = []

        for item in items:
            h = item.content_hash()
            if h not in seen_hashes:
                seen_hashes.add(h)
                excerpt = item.summary if item.compressed else item.content[:120]
                unique_excerpts.append(f"[{item.item_type}] {excerpt}")
            if item.lineage_id:
                all_lineage.append(item.lineage_id)

        summary = " | ".join(unique_excerpts[:10])
        if len(unique_excerpts) > 10:
            summary += f" … (+{len(unique_excerpts) - 10} more)"

        # Score: deduplicate ratio gives fidelity
        total = len(items)
        unique = len(seen_hashes)
        dedup_ratio = unique / max(total, 1)  # 1.0 = all unique (max fidelity)

        avg_signal = sum(i.signal for i in items) / max(len(items), 1)

        fidelity    = round(min(1.0, dedup_ratio * 0.70 + avg_signal * 0.30), 4)
        recon_conf  = round(min(1.0, dedup_ratio * 0.60 + 0.40), 4)
        semantic    = round(min(1.0, avg_signal * 0.50 + 0.50), 4)

        return CompressionBlock(
            block_id=block_id,
            source_item_ids=[i.item_id for i in items],
            summary=summary,
            fidelity_score=fidelity,
            reconstruction_conf=recon_conf,
            semantic_preservation=semantic,
            lineage_ids=list(set(all_lineage)),
        )


# ── Context window ─────────────────────────────────────────────────────────────

class ContextWindow:
    """
    Three-tier sliding context window.
      Active     — full content, most recent ACTIVE_MAX_ITEMS items
      Compressed — CompressionBlocks from evicted active items
      Archived   — lineage-only records of the oldest evictions
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._active:     deque = deque(maxlen=ACTIVE_MAX_ITEMS)
        self._compressed: deque = deque(maxlen=COMPRESSED_MAX_ITEMS)
        self._archived:   deque = deque(maxlen=ARCHIVE_MAX_ITEMS)
        self._compressor  = SemanticCompressor()
        self._block_seq   = 0

    def push(self, item: ContextItem) -> None:
        """Add an item to the active tier; trigger compression if window is full."""
        if len(self._active) >= ACTIVE_MAX_ITEMS:
            self._evict_oldest()
        self._active.append(item)

    def _evict_oldest(self, batch_size: int = 10) -> None:
        """Compress the oldest batch_size items from active → compressed tier."""
        evicted = []
        for _ in range(min(batch_size, len(self._active))):
            evicted.append(self._active.popleft())

        if evicted:
            self._block_seq += 1
            block_id = f"blk_{self.session_id[:8]}_{self._block_seq:04d}"
            block = self._compressor.compress_group(evicted, block_id)
            self._compressed.append(block)

            # Archive lineage-only records
            for item in evicted:
                self._archived.append({
                    "item_id":    item.item_id,
                    "lineage_id": item.lineage_id,
                    "ts":         item.timestamp,
                    "type":       item.item_type,
                })

    def active_items(self) -> List[ContextItem]:
        return list(self._active)

    def compressed_blocks(self) -> List[CompressionBlock]:
        return list(self._compressed)

    def archived_lineage(self) -> List[Dict]:
        return list(self._archived)

    def snapshot(self) -> Dict:
        blocks = list(self._compressed)
        avg_conf = (
            sum(b.overall_confidence() for b in blocks) / len(blocks)
            if blocks else 1.0
        )
        return {
            "session_id":         self.session_id,
            "active_count":       len(self._active),
            "compressed_blocks":  len(blocks),
            "archived_count":     len(self._archived),
            "avg_compression_confidence": round(avg_conf, 4),
        }

    def rebuild_active_context(self) -> str:
        """
        Reconstruct a readable active context string from compressed summaries
        + the current active items. Used for context refresh logic (Z40D).
        """
        parts = []
        for block in list(self._compressed)[-5:]:  # last 5 compressed blocks
            parts.append(f"[COMPRESSED@{block.block_id}] {block.summary}")
        for item in list(self._active):
            excerpt = item.content[:200]
            parts.append(f"[ACTIVE/{item.item_type}] {excerpt}")
        return "\n".join(parts)


# ── Compression ledger ─────────────────────────────────────────────────────────

class CompressionLedger:
    """
    Tracks compression events and aggregate fidelity across all sessions.
    """

    def __init__(self):
        self._sessions: Dict[str, ContextWindow] = {}
        self._total_compressed = 0

    def get_or_create(self, session_id: str) -> ContextWindow:
        if session_id not in self._sessions:
            self._sessions[session_id] = ContextWindow(session_id)
        return self._sessions[session_id]

    def push(self, session_id: str, item: ContextItem) -> None:
        self.get_or_create(session_id).push(item)

    def force_compress(self, session_id: str) -> Optional[CompressionBlock]:
        """Manually trigger eviction on a session (for pressure-triggered compression)."""
        win = self._sessions.get(session_id)
        if win and len(win._active) > 0:
            win._evict_oldest()
            blocks = win.compressed_blocks()
            return blocks[-1] if blocks else None
        return None

    def global_snapshot(self) -> Dict:
        sessions = []
        for sid, win in self._sessions.items():
            sessions.append(win.snapshot())
        total_active     = sum(s["active_count"] for s in sessions)
        total_compressed = sum(s["compressed_blocks"] for s in sessions)
        avg_confidence   = (
            sum(s["avg_compression_confidence"] for s in sessions) / len(sessions)
            if sessions else 1.0
        )
        return {
            "session_count":       len(sessions),
            "total_active_items":  total_active,
            "total_compressed_blocks": total_compressed,
            "avg_compression_confidence": round(avg_confidence, 4),
            "sessions": sessions,
        }

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# Global singleton
_compression_ledger = CompressionLedger()

def get_compression_ledger() -> CompressionLedger:
    return _compression_ledger
