"""
backend/execution/memory/cache.py — Multi-Tenant Two-Layer Embedding Cache for Phase 6.
Provides in-memory bounded LRU + persistent SQLite backing with strict tenant isolation.

Phase 8 additions:
  - protection_drop counter: cross-tenant key collision removals in get()
  - admission_denial counter: cache-full rejections when no same-tenant LRU entry exists
  - invalidation counter: explicit invalidate() removals
  - SQLite prune per-tenant attribution via SELECT-before-DELETE set-based approach
"""
from __future__ import annotations

import collections
import hashlib
import logging
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..persistence.db import StateDatabase

logger = logging.getLogger("aetherion.memory.cache")

_SENTINEL = object()


def compute_content_hash(text: str) -> str:
    """Deterministic cryptographic content hash of normalized text."""
    if not isinstance(text, str):
        text = str(text or "")
    normalized = text.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_cache_key(
    owner_id: str,
    model_name: str,
    dimensions: int,
    content_hash: str,
    version: int = 1,
    provider: str = "default",
) -> str:
    """
    Composite cache key enforcing strict cryptographic tenant isolation and versioning.
    Cache keys are completely disjoint across tenants, models, and embedding versions.
    """
    # NOTE: Use canonical JSON encoding (sorted keys) to prevent delimiter collision attacks.
    # Colon-separated strings allow: owner="a:b", model="c" to collide with owner="a", model="b:c".
    # JSON encoding makes each field boundary unambiguous and collision-impossible.
    import json as _json
    if version == 1 and provider == "default":
        raw = _json.dumps(
            {"owner_id": owner_id, "model_name": model_name, "dimensions": dimensions, "content_hash": content_hash},
            sort_keys=True, separators=(",", ":"),
        )
    else:
        raw = _json.dumps(
            {"owner_id": owner_id, "provider": provider, "model_name": model_name, "dimensions": dimensions, "version": version, "content_hash": content_hash},
            sort_keys=True, separators=(",", ":"),
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_valid_owner_id(owner_id: Any) -> str:
    """Validate that owner_id is strictly a non-empty string."""
    if not isinstance(owner_id, str):
        raise KeyError("A valid non-empty string owner_id is required.")
    normalized = owner_id.strip()
    if not normalized:
        raise KeyError("A valid non-empty string owner_id is required.")
    return normalized


class EmbeddingCache:
    """
    Thread-safe, multi-tenant embedding cache with in-memory LRU and SQLite persistence.
    Guarantees zero cross-tenant cache leakage, TTL expiration, and bounded persistence.

    METRIC TAXONOMY (Phase 8):
        evictions          : LRU evictions of same-tenant entries to make room for new entries
        protection_drops   : Cross-tenant key collision removals in get() — invariant guard
        admission_denials  : Admission rejected because cache is full and no same-tenant entry to evict
        invalidations      : Entries removed via explicit invalidate() calls
        expired_evictions  : TTL-expired entries removed from memory or SQLite (memory + SQLite prune)
    """

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        max_memory_entries: int = 1000,
        max_persistent_entries: int = 10000,
        default_ttl: Optional[float] = None,
        db: Optional[StateDatabase] = None,
    ):
        self.db = db or StateDatabase(db_path)
        self.max_memory_entries = max(1, max_memory_entries)
        self.max_persistent_entries = max(10, max_persistent_entries)
        self.default_ttl = default_ttl
        self._lock = threading.RLock()

        # In-memory LRU: cache_key -> (owner_id, content_hash, list[float], Optional[float] expires_at)
        self._memory_cache: collections.OrderedDict[str, Tuple[str, str, List[float], Optional[float]]] = collections.OrderedDict()

        # Metrics - Global
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expired_evictions = 0
        self._protection_drops = 0     # Phase 8: cross-tenant key-collision removals
        self._admission_denials = 0    # Phase 8: admission denied (cache full, no same-tenant LRU)
        self._invalidations = 0        # Phase 8: explicit invalidate() removals

        # Metrics - Per-Tenant Partitioned Counters
        self._tenant_hits: Dict[str, int] = collections.defaultdict(int)
        self._tenant_misses: Dict[str, int] = collections.defaultdict(int)
        self._tenant_evictions: Dict[str, int] = collections.defaultdict(int)
        self._tenant_expired_evictions: Dict[str, int] = collections.defaultdict(int)
        self._tenant_protection_drops: Dict[str, int] = collections.defaultdict(int)  # Phase 8
        self._tenant_admission_denials: Dict[str, int] = collections.defaultdict(int)  # Phase 8
        self._tenant_invalidations: Dict[str, int] = collections.defaultdict(int)     # Phase 8

    def get(
        self,
        owner_id: str,
        text: str,
        model_name: str,
        dimensions: int,
        version: int = 1,
        provider: str = "default",
    ) -> Optional[List[float]]:
        """
        Fetch cached vector for (owner_id, text, model, dimensions, version, provider).
        Checks memory LRU first, then SQLite backing store, respecting TTL expiration.
        """
        owner_id = _require_valid_owner_id(owner_id)
        if not text:
            return None

        content_hash = compute_content_hash(text)
        cache_key = compute_cache_key(owner_id, model_name, dimensions, content_hash, version, provider)
        now = time.time()

        with self._lock:
            # 1. Check in-memory LRU
            if cache_key in self._memory_cache:
                entry = self._memory_cache[cache_key]
                cached_owner = entry[0]
                cached_hash = entry[1]
                vector = entry[2]
                expires_at = entry[3] if len(entry) > 3 else None

                # Check expiration
                if expires_at is not None and now > expires_at:
                    del self._memory_cache[cache_key]
                    self._expired_evictions += 1
                    if cached_owner:
                        self._tenant_expired_evictions[cached_owner] += 1
                    # Also remove from DB
                    conn = self.db.get_connection()
                    try:
                        conn.execute("DELETE FROM embedding_cache WHERE cache_key = ?;", (cache_key,))
                    except Exception:
                        pass
                    finally:
                        conn.close()
                elif cached_owner == owner_id and cached_hash == content_hash:
                    self._memory_cache.move_to_end(cache_key)
                    self._hits += 1
                    self._tenant_hits[owner_id] += 1
                    return list(vector)
                else:
                    # Invariant violation guard — cross-tenant key collision: remove and count
                    del self._memory_cache[cache_key]
                    # Phase 8: count as protection_drop attributed to the evicted tenant's owner
                    self._protection_drops += 1
                    self._tenant_protection_drops[cached_owner] += 1

            # 2. Check persistent SQLite store
            conn = self.db.get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT vector_blob, dimensions, ttl, created_at, version
                    FROM embedding_cache
                    WHERE cache_key = ? AND owner_id = ?
                    """,
                    (cache_key, owner_id),
                )
                row = cur.fetchone()
                if row:
                    ttl = row["ttl"] if "ttl" in row.keys() else None
                    created_at = row["created_at"]
                    # Check TTL expiration
                    if ttl is not None and (created_at + ttl) < now:
                        conn.execute("DELETE FROM embedding_cache WHERE cache_key = ?;", (cache_key,))
                        self._expired_evictions += 1
                        self._tenant_expired_evictions[owner_id] += 1
                        self._misses += 1
                        self._tenant_misses[owner_id] += 1
                        return None

                    dim = row["dimensions"]
                    blob = row["vector_blob"]
                    vector = list(struct.unpack(f"{dim}f", blob))
                    item_expires_at = (created_at + ttl) if ttl is not None else None

                    # Populate memory LRU with exact metadata
                    self._put_memory_lru(cache_key, owner_id, content_hash, vector, item_expires_at)

                    # Update last_accessed_at in DB asynchronously / opportunistically
                    try:
                        conn.execute(
                            "UPDATE embedding_cache SET last_accessed_at = ? WHERE cache_key = ?",
                            (now, cache_key),
                        )
                    except Exception as ex:
                        logger.debug("Failed updating cache last_accessed_at: %s", ex)

                    self._hits += 1
                    self._tenant_hits[owner_id] += 1
                    return vector
            except Exception as exc:
                logger.error("EmbeddingCache SQLite lookup error: %s", exc)
            finally:
                conn.close()

            self._misses += 1
            self._tenant_misses[owner_id] += 1
            return None

    def put(
        self,
        owner_id: str,
        text: str,
        model_name: str,
        dimensions: int,
        vector: List[float],
        ttl: Optional[float] = None,
        version: int = 1,
        provider: str = "default",
    ) -> None:
        """
        Store vector in both memory LRU and SQLite backing store under tenant ownership.
        """
        owner_id = _require_valid_owner_id(owner_id)
        if not text or not vector:
            return
        if len(vector) != dimensions:
            raise ValueError(
                f"Vector dimension mismatch: expected {dimensions}, got {len(vector)}"
            )

        effective_ttl = ttl if ttl is not None else self.default_ttl
        content_hash = compute_content_hash(text)
        cache_key = compute_cache_key(owner_id, model_name, dimensions, content_hash, version, provider)
        now = time.time()
        expires_at = (now + effective_ttl) if effective_ttl is not None else None

        with self._lock:
            # 1. Update in-memory LRU
            self._put_memory_lru(cache_key, owner_id, content_hash, vector, expires_at)

            # 2. Persist to SQLite
            dim = len(vector)
            vector_blob = struct.pack(f"{dim}f", *[float(x) for x in vector])

            conn = self.db.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO embedding_cache (
                        cache_key, owner_id, content_hash, model_name, dimensions,
                        vector_blob, created_at, last_accessed_at, ttl, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        vector_blob = excluded.vector_blob,
                        dimensions = excluded.dimensions,
                        last_accessed_at = excluded.last_accessed_at,
                        ttl = excluded.ttl,
                        version = excluded.version
                    WHERE embedding_cache.owner_id = excluded.owner_id
                    """,
                    (cache_key, owner_id, content_hash, model_name, dim, vector_blob, now, now, effective_ttl, version),
                )

                # Bounded persistent store enforcement
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM embedding_cache WHERE owner_id = ?;",
                    (owner_id,),
                )
                row = cur.fetchone()
                count = row["cnt"] if row else 0
                if count > self.max_persistent_entries:
                    excess = count - self.max_persistent_entries
                    conn.execute(
                        """
                        DELETE FROM embedding_cache WHERE cache_key IN (
                            SELECT cache_key FROM embedding_cache WHERE owner_id = ? ORDER BY last_accessed_at ASC LIMIT ?
                        );
                        """,
                        (owner_id, excess),
                    )
            except Exception as exc:
                logger.error("EmbeddingCache SQLite persist error: %s", exc)
            finally:
                conn.close()

    def _put_memory_lru(
        self,
        cache_key: str,
        owner_id: str,
        content_hash: str,
        vector: List[float],
        expires_at: Optional[float] = None,
    ) -> None:
        """Internal helper to insert into bounded in-memory LRU with exact tenant and hash metadata."""
        if cache_key in self._memory_cache:
            self._memory_cache.move_to_end(cache_key)
            self._memory_cache[cache_key] = (owner_id, content_hash, vector, expires_at)
            return

        # Bounded in-memory LRU enforcement.
        # TENANT ISOLATION INVARIANT: Tenant A MUST NEVER evict Tenant B's in-memory cache entries
        # through normal tenant-scoped operations. Cache miss is preferable to cross-tenant eviction.
        #
        # Strategy:
        # 1. First, evict the LRU entry belonging to the same owner_id (same-tenant LRU).
        # 2. If no same-tenant entry exists AND the global cache is full, DENY ADMISSION for this
        #    tenant. The entry remains in SQLite for future warm-up; we do NOT cross-tenant evict.
        while len(self._memory_cache) >= self.max_memory_entries:
            # Try to find the oldest (LRU) entry for this specific owner_id
            evict_key = None
            for k, val in self._memory_cache.items():
                if len(val) >= 1 and val[0] == owner_id:
                    evict_key = k
                    break  # OrderedDict: first match is the LRU (insertion-order = LRU order)
            if evict_key is None:
                # No same-tenant entry available; deny admission rather than cross-tenant evict.
                # The entry is persisted in SQLite and will warm-up on next access.
                logger.debug(
                    "EmbeddingCache: admission denied for owner=%s — cache full, no same-tenant entry to evict.",
                    owner_id,
                )
                # Phase 8: count admission denial
                self._admission_denials += 1
                self._tenant_admission_denials[owner_id] += 1
                return
            del self._memory_cache[evict_key]
            self._evictions += 1
            self._tenant_evictions[owner_id] += 1

        self._memory_cache[cache_key] = (owner_id, content_hash, vector, expires_at)

    def prune_expired(self) -> int:
        """Prune all expired entries from memory and SQLite. Returns total entries removed."""
        now = time.time()
        pruned_count = 0
        with self._lock:
            # Memory LRU prune
            to_remove = [
                k for k, v in self._memory_cache.items()
                if len(v) > 3 and v[3] is not None and v[3] < now
            ]
            for k in to_remove:
                entry_owner = self._memory_cache[k][0] if len(self._memory_cache[k]) >= 1 else None
                del self._memory_cache[k]
                self._expired_evictions += 1
                if entry_owner:
                    self._tenant_expired_evictions[entry_owner] += 1
                pruned_count += 1

            # SQLite prune — Phase 8: use SELECT-before-DELETE (set-based, not N+1) for per-tenant attribution
            conn = self.db.get_connection()
            try:
                # Step 1: Collect per-tenant counts of about-to-expire rows (1 aggregating SELECT)
                cur = conn.execute(
                    """
                    SELECT owner_id, COUNT(*) as cnt
                    FROM embedding_cache
                    WHERE ttl IS NOT NULL AND (created_at + ttl) < ?
                    GROUP BY owner_id
                    """,
                    (now,),
                )
                tenant_expire_counts: Dict[str, int] = {}
                for row in cur.fetchall():
                    tenant_expire_counts[row["owner_id"]] = row["cnt"]

                # Step 2: Delete all expired rows (1 DELETE)
                cur2 = conn.execute(
                    "DELETE FROM embedding_cache WHERE ttl IS NOT NULL AND (created_at + ttl) < ?;",
                    (now,),
                )
                db_pruned = cur2.rowcount
                pruned_count += db_pruned

                # Step 3: Attribute per-tenant (set-based, not N+1)
                self._expired_evictions += db_pruned
                for tenant_id, count in tenant_expire_counts.items():
                    self._tenant_expired_evictions[tenant_id] += count
            finally:
                conn.close()

        return pruned_count

    def invalidate(
        self,
        owner_id: str,
        text: Optional[str] = None,
        version: Optional[int] = None,
    ) -> int:
        """
        Invalidate cached embeddings for a tenant, optionally restricted to specific text or version.
        Enforces exact equality matching against in-memory LRU metadata and persistent storage.
        Returns number of entries removed from persistent storage.

        Phase 8: Counts each invalidated in-memory entry in invalidation counters.
        """
        owner_id = _require_valid_owner_id(owner_id)

        with self._lock:
            # 1. Query SQLite for matching cache keys
            conn = self.db.get_connection()
            db_keys: Set[str] = set()
            try:
                sel_query = "SELECT cache_key FROM embedding_cache WHERE owner_id = ?"
                params: List[Any] = [owner_id]
                if text:
                    sel_query += " AND content_hash = ?"
                    params.append(compute_content_hash(text))
                if version is not None:
                    sel_query += " AND version = ?"
                    params.append(version)

                cur = conn.execute(sel_query, tuple(params))
                for row in cur.fetchall():
                    db_keys.add(row["cache_key"])

                # Execute delete in DB
                del_query = "DELETE FROM embedding_cache WHERE owner_id = ?"
                del_params: List[Any] = [owner_id]
                if text:
                    del_query += " AND content_hash = ?"
                    del_params.append(compute_content_hash(text))
                if version is not None:
                    del_query += " AND version = ?"
                    del_params.append(version)
                conn.execute(del_query, tuple(del_params))
            finally:
                conn.close()

            # 2. Match in memory LRU
            if text:
                target_content_hash = compute_content_hash(text)
                mem_keys = set(
                    k for k, val in self._memory_cache.items()
                    if len(val) >= 2 and val[0] == owner_id and val[1] == target_content_hash
                    and (version is None or k in db_keys)
                )
            elif version is not None:
                mem_keys = set(
                    k for k, val in self._memory_cache.items()
                    if len(val) >= 1 and val[0] == owner_id and k in db_keys
                )
            else:
                mem_keys = set(
                    k for k, val in self._memory_cache.items()
                    if len(val) >= 1 and val[0] == owner_id
                )

            # Evict from memory LRU
            for k in mem_keys:
                self._memory_cache.pop(k, None)

            # 3. Deduplicate across tiers (Finding 6)
            distinct_invalidated = len(mem_keys | db_keys)
            if distinct_invalidated > 0:
                self._invalidations += distinct_invalidated
                self._tenant_invalidations[owner_id] += distinct_invalidated

            return distinct_invalidated

    def clear(self) -> None:
        """Clear memory LRU and all SQLite entries (primarily for test environments)."""
        with self._lock:
            self._memory_cache.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            self._expired_evictions = 0
            self._protection_drops = 0
            self._admission_denials = 0
            self._invalidations = 0
            self._tenant_hits.clear()
            self._tenant_misses.clear()
            self._tenant_evictions.clear()
            self._tenant_expired_evictions.clear()
            self._tenant_protection_drops.clear()
            self._tenant_admission_denials.clear()
            self._tenant_invalidations.clear()
            conn = self.db.get_connection()
            try:
                conn.execute("DELETE FROM embedding_cache;")
            finally:
                conn.close()

    def stats(
        self,
        owner_id: Any = _SENTINEL,
        *,
        allow_global: bool = False,
    ) -> Dict[str, Any]:
        """
        Return cache health and utilization statistics.
        If owner_id is specified, stats are strictly scoped to that tenant.

        Phase 8 additions to returned dict:
            - protection_drops: cross-tenant key-collision removals
            - admission_denials: admission rejected (cache full, no same-tenant LRU)
            - invalidations: entries removed by explicit invalidate() calls

        Observability contract (Finding 5):
            - cache.stats() -> global stats (backward-compatibility)
            - cache.stats(owner_id="") / cache.stats(owner_id="   ") -> raises KeyError
            - cache.stats(owner_id=None) -> raises KeyError unless allow_global=True
            - cache.stats(owner_id="t1") -> scoped tenant stats
        """
        if owner_id is _SENTINEL:
            is_global = True
            normalized_owner = None
        elif owner_id is None:
            if not allow_global:
                raise KeyError("A valid non-empty string owner_id is required unless allow_global=True.")
            is_global = True
            normalized_owner = None
        else:
            normalized_owner = _require_valid_owner_id(owner_id)
            is_global = False

        with self._lock:
            db_count = 0
            conn = self.db.get_connection()
            try:
                if not is_global:
                    cur = conn.execute(
                        "SELECT COUNT(*) as cnt FROM embedding_cache WHERE owner_id = ?;",
                        (normalized_owner,),
                    )
                else:
                    cur = conn.execute("SELECT COUNT(*) as cnt FROM embedding_cache;")
                row = cur.fetchone()
                db_count = row["cnt"] if row else 0
            except Exception:
                pass
            finally:
                conn.close()

            if not is_global:
                in_memory_count = sum(
                    1 for val in self._memory_cache.values()
                    if len(val) >= 1 and val[0] == normalized_owner
                )
                t_hits = self._tenant_hits.get(normalized_owner, 0)
                t_misses = self._tenant_misses.get(normalized_owner, 0)
                t_evictions = self._tenant_evictions.get(normalized_owner, 0)
                t_expired = self._tenant_expired_evictions.get(normalized_owner, 0)
                t_protection_drops = self._tenant_protection_drops.get(normalized_owner, 0)
                t_admission_denials = self._tenant_admission_denials.get(normalized_owner, 0)
                t_invalidations = self._tenant_invalidations.get(normalized_owner, 0)
                t_total = t_hits + t_misses
                t_hit_rate = (t_hits / t_total) if t_total > 0 else 0.0
                return {
                    "hits": t_hits,
                    "misses": t_misses,
                    "evictions": t_evictions,
                    "expired_evictions": t_expired,
                    "protection_drops": t_protection_drops,
                    "admission_denials": t_admission_denials,
                    "invalidations": t_invalidations,
                    "hit_rate": round(t_hit_rate, 4),
                    "in_memory_entries": in_memory_count,
                    "max_memory_entries": self.max_memory_entries,
                    "persistent_entries": db_count,
                    "max_persistent_entries": self.max_persistent_entries,
                }

            in_memory_count = len(self._memory_cache)
            total_reqs = self._hits + self._misses
            hit_rate = (self._hits / total_reqs) if total_reqs > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expired_evictions": self._expired_evictions,
                "protection_drops": self._protection_drops,
                "admission_denials": self._admission_denials,
                "invalidations": self._invalidations,
                "hit_rate": round(hit_rate, 4),
                "in_memory_entries": in_memory_count,
                "max_memory_entries": self.max_memory_entries,
                "persistent_entries": db_count,
                "max_persistent_entries": self.max_persistent_entries,
            }

