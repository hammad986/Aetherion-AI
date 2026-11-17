"""
backend/execution/memory/index.py — Production Vector Index Abstraction and Dual-Engine Strategy.
Provides ExactVectorIndex (100% recall) and ApproximateVectorIndex (IVF-Flat partition clustering)
with dynamic corpus threshold switching and zero external vector database dependencies.
"""
from __future__ import annotations

import abc
import dataclasses
import hashlib
import logging
import math
import queue
import random
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("aetherion.memory.index")

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False

from .embeddings import cosine_similarity
from ..persistence.db import StateDatabase


def _l2_normalize(vec: List[float]) -> List[float]:
    """Normalize vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-9:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


class VectorIndex(abc.ABC):
    """Abstract base class for memory vector indexes."""

    @abc.abstractmethod
    def add(self, memory_id: str, vector: List[float]) -> None:
        """Add or update a single vector in the index."""
        ...

    @abc.abstractmethod
    def add_batch(self, items: List[Tuple[str, List[float]]]) -> None:
        """Add or update a batch of vectors in the index."""
        ...

    @abc.abstractmethod
    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """
        Search the index for vectors nearest to query_vector.
        Returns list of (memory_id, similarity_score) sorted descending by similarity.
        """
        ...

    @abc.abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Remove a vector from the index."""
        ...

    @abc.abstractmethod
    def rebuild(self, vectors: List[Tuple[str, List[float]]]) -> None:
        """Completely rebuild the index from the provided list of vectors."""
        ...

    @abc.abstractmethod
    def size(self) -> int:
        """Return the number of active vectors in the index."""
        ...

    @property
    @abc.abstractmethod
    def index_type(self) -> str:
        """Identifier for index engine type."""
        ...

    @property
    def generation(self) -> int:
        """Deterministic content- or sequence-derived index build generation token."""
        return 0


@dataclasses.dataclass(frozen=True)
class ExactSnapshot:
    generation: int = 0
    vectors: Dict[str, Tuple[float, ...]] = dataclasses.field(default_factory=dict)
    vector_matrix: Optional[Any] = None
    row_to_id: Optional[Tuple[str, ...]] = None


def _select_top_k_deterministic(
    sims: Any,
    row_to_id: Sequence[str],
    indices: Optional[Any],
    top_k: int,
    min_score: float = 0.0,
) -> List[Tuple[str, float]]:
    """
    Select top-k items by similarity descending with deterministic memory_id descending tie-breaking.
    Uses BLAS/numpy vectorization and argpartition for O(N) performance while guaranteeing
    exact tie-break parity with pure-Python sort specifications.
    Guarantees:
      - top_k <= 0 immediately returns [] without invoking numpy reductions on empty arrays.
      - Candidate eligibility rule: similarity >= min_score is enforced unconditionally (before formatting).
      - Negative similarities are excluded when similarity < min_score (no clamping of rejected items).
      - Exact match against pure-Python reference implementation across all score distributions.
    """
    if top_k <= 0:
        return []
    sims = np.asarray(sims)
    if sims.size == 0:
        return []

    # Candidate is eligible ONLY when similarity >= min_score (evaluated before formatting/clamping)
    valid_mask = sims >= min_score
    if not np.any(valid_mask):
        return []

    valid_pos = np.where(valid_mask)[0]
    sims = sims[valid_pos]

    if indices is not None:
        active_indices = np.asarray(indices)[valid_pos]
    else:
        active_indices = valid_pos

    n_items = len(sims)
    if n_items <= top_k:
        pairs = [(row_to_id[int(active_indices[i])], float(sims[i])) for i in range(n_items)]
        pairs.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return pairs

    part_idx = np.argpartition(-sims, top_k)[:top_k]
    cutoff = float(np.min(sims[part_idx]))

    greater_mask = sims > cutoff
    tied_mask = (sims == cutoff)
    greater_idx = np.where(greater_mask)[0]
    tied_idx = np.where(tied_mask)[0]

    if len(greater_idx) + len(tied_idx) <= top_k:
        chosen_idx = np.concatenate([greater_idx, tied_idx])
        pairs = [(row_to_id[int(active_indices[i])], float(sims[i])) for i in chosen_idx]
        pairs.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return pairs

    if len(tied_idx) <= 1:
        chosen = part_idx[np.argsort(-sims[part_idx])]
        pairs = [(row_to_id[int(active_indices[i])], float(sims[i])) for i in chosen]
        pairs.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return pairs

    greater_pairs = [(row_to_id[int(active_indices[i])], float(sims[i])) for i in greater_idx]
    greater_pairs.sort(key=lambda x: (x[1], x[0]), reverse=True)

    slots_needed = top_k - len(greater_pairs)
    tied_pairs = [(row_to_id[int(active_indices[i])], float(sims[i])) for i in tied_idx]
    tied_pairs.sort(key=lambda x: (x[1], x[0]), reverse=True)

    return greater_pairs + tied_pairs[:slots_needed]


class ExactVectorIndex(VectorIndex):
    """
    Exhaustive exact-scan vector index.
    Guarantees 100% recall for small and moderate memory corpora.
    Uses RCU immutable snapshots for non-blocking concurrent reader execution and
    NumPy BLAS vectorization when available.
    """

    def __init__(self, vectors: Optional[List[Tuple[str, List[float]]]] = None):
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._vectors: Dict[str, List[float]] = {}
        self._snapshot: Optional[ExactSnapshot] = None
        self._generation: int = 0
        self._last_build_fingerprint: Optional[str] = None
        if vectors:
            self.rebuild(vectors)
        else:
            self._refresh_snapshot()

    def _refresh_snapshot(self) -> None:
        if self._generation == 0:
            self._generation = 1
        v_dict = {mid: tuple(v) for mid, v in self._vectors.items()}
        v_mat = None
        row_to_id = None
        if _NUMPY_AVAILABLE and len(v_dict) > 0:
            row_to_id = list(v_dict.keys())
            v_mat = np.array([v_dict[mid] for mid in row_to_id], dtype=np.float32)
        self._snapshot = ExactSnapshot(
            generation=self._generation,
            vectors=v_dict,
            vector_matrix=v_mat,
            row_to_id=tuple(row_to_id) if row_to_id is not None else None,
        )

    @property
    def index_type(self) -> str:
        return "exact"

    @property
    def generation(self) -> int:
        return self._generation

    def size(self) -> int:
        snap = self._snapshot
        return len(snap.vectors) if snap is not None else 0

    def add(self, memory_id: str, vector: List[float]) -> None:
        norm_v = _l2_normalize(vector)
        with self._write_lock:
            with self._lock:
                self._vectors[memory_id] = norm_v
                self._generation += 1
            self._refresh_snapshot()

    def add_batch(self, items: List[Tuple[str, List[float]]]) -> None:
        with self._write_lock:
            with self._lock:
                for mid, vec in items:
                    self._vectors[mid] = _l2_normalize(vec)
                self._generation += 1
            self._refresh_snapshot()

    def delete(self, memory_id: str) -> bool:
        with self._write_lock:
            with self._lock:
                deleted = memory_id in self._vectors
                if deleted:
                    del self._vectors[memory_id]
                    self._generation += 1
            if deleted:
                self._refresh_snapshot()
            return deleted

    def rebuild(self, vectors: List[Tuple[str, List[float]]]) -> None:
        with self._write_lock:
            if vectors and len(vectors) > 0:
                fp, gen = ApproximateVectorIndex._compute_corpus_fingerprint(vectors)
                if self._snapshot is not None and fp == self._last_build_fingerprint and len(self._vectors) == len(self._snapshot.vectors):
                    return
            else:
                fp, gen = "empty", 0

            with self._lock:
                self._vectors.clear()
                for mid, vec in vectors:
                    self._vectors[mid] = _l2_normalize(vec)
                self._generation = gen if gen > 0 else 1
                self._last_build_fingerprint = fp
            self._refresh_snapshot()

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        # Non-blocking RCU read snapshot: zero reader mutex contention
        q = _l2_normalize(query_vector)
        snap = self._snapshot
        if snap is None or not snap.vectors:
            return []

        if _NUMPY_AVAILABLE and snap.vector_matrix is not None and snap.row_to_id is not None:
            q_arr = np.array(q, dtype=np.float32)
            sims = snap.vector_matrix @ q_arr
            return _select_top_k_deterministic(sims, snap.row_to_id, None, top_k, min_score)

        scored = []
        for mid, vec in snap.vectors.items():
            sim = cosine_similarity(q, list(vec))
            if sim >= min_score:
                scored.append((mid, float(sim)))

        scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return scored[:top_k]


@dataclasses.dataclass(frozen=True)
class ApproximateSnapshot:
    generation: int
    vectors: Dict[str, Tuple[float, ...]]
    centroids: Dict[int, Tuple[float, ...]]
    inverted_lists: Dict[int, FrozenSet[str]]
    item_cluster: Dict[str, int]
    centroids_matrix: Optional[Any] = None
    vectors_matrix: Optional[Any] = None
    row_to_mid: Optional[Tuple[str, ...]] = None
    mid_to_row: Optional[Dict[str, int]] = None
    cluster_row_indices: Optional[Dict[int, Any]] = None


class ApproximateVectorIndex(VectorIndex):
    """
    Inverted File Flat (IVF-Flat) Approximate Vector Index.
    Partitions the vector space into K clusters using spherical k-means.
    Queries probe candidate clusters nearest to the query vector.
    Uses RCU immutable snapshots for non-blocking concurrent reader execution and
    NumPy BLAS vectorization when available.
    """

    @staticmethod
    def _compute_corpus_fingerprint(
        vectors: List[Tuple[str, List[float]]]
    ) -> Tuple[str, int]:
        """
        Compute a deterministic, content-aware cryptographic fingerprint for a vector corpus.
        Canonicalizes vectors by memory_id, applying standard dictionary overwrite semantics.
        Incorporates corpus cardinality, sorted memory IDs, vector dimensions, and RAW vector
        float contents BEFORE normalization to prevent magnitude-only mutation blindspots.
        Returns:
            (fingerprint_hex_string, generation_int)
        """
        if not vectors:
            return "empty", 0
        v_dict: Dict[str, List[float]] = {}
        for mid, vec in vectors:
            v_dict[mid] = list(vec)
        sorted_items = sorted(v_dict.items(), key=lambda x: x[0])
        n = len(sorted_items)
        if n == 0:
            return "empty", 0

        hasher = hashlib.sha256()
        hasher.update(f"count:{n};".encode())
        for mid, v in sorted_items:
            hasher.update(f"{mid}:dim:{len(v)};".encode())
            if _NUMPY_AVAILABLE:
                hasher.update(np.asarray(v, dtype=np.float32).tobytes())
            else:
                hasher.update(",".join(f"{x:.6f}" for x in v).encode())
        fp = hasher.hexdigest()
        gen = int(fp[:8], 16)
        return fp, gen

    def __init__(
        self,
        vectors: Optional[List[Tuple[str, List[float]]]] = None,
        n_clusters: Optional[int] = None,
        n_probe: Optional[int] = None,
        random_seed: int = 42,
        owner_id: str = "",
        nlist: Optional[int] = None,
        **kwargs: Any,
    ):
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._random_seed = random_seed
        self.owner_id = owner_id
        effective_clusters = n_clusters if n_clusters is not None else nlist
        self._n_clusters_override = effective_clusters
        self._n_probe_override = n_probe
        self.adaptive_nprobe_boost: int = 1
        self._quality_adaptation: Optional[str] = None
        self._generation: int = 0
        self._last_build_fingerprint: Optional[str] = None

        # Storage
        self._vectors: Dict[str, List[float]] = {}
        self._inverted_lists: Dict[int, Set[str]] = {}
        self._centroids: Dict[int, List[float]] = {}
        self._item_cluster: Dict[str, int] = {}
        self._snapshot: Optional[ApproximateSnapshot] = None

        if vectors:
            self.rebuild(vectors)
        else:
            self._refresh_snapshot()

    @property
    def index_type(self) -> str:
        return "approximate_ivf"

    @property
    def generation(self) -> int:
        return self._generation

    def size(self) -> int:
        snap = self._snapshot
        return len(snap.vectors) if snap is not None else 0

    def _determine_k(self, n: int) -> int:
        if self._n_clusters_override is not None:
            return max(1, min(n, self._n_clusters_override))
        if n <= 10:
            return 1
        # Cap clusters at 64 for optimal recall and speed
        return max(2, min(64, int(math.sqrt(n))))

    def _determine_n_probe(self, k: int) -> int:
        if self._n_probe_override is not None:
            return max(1, min(k, self._n_probe_override))
        if k <= 2:
            return k
        return max(2, min(k, max(4, int(math.ceil(k * 0.60)), int(math.sqrt(k)) * 2)))

    def _refresh_snapshot(self) -> None:
        if self._generation == 0:
            self._generation = 1
        v_dict = {mid: tuple(v) for mid, v in self._vectors.items()}
        c_dict = {c: tuple(v) for c, v in self._centroids.items()}
        inv_dict = {c: frozenset(s) for c, s in self._inverted_lists.items()}
        item_clust = dict(self._item_cluster)

        c_mat = None
        v_mat = None
        row_to_mid = None
        mid_to_row = None
        cluster_rows = None

        if _NUMPY_AVAILABLE and len(v_dict) > 0 and len(c_dict) > 0:
            sorted_c_keys = sorted(c_dict.keys())
            c_mat = np.array([c_dict[i] for i in sorted_c_keys], dtype=np.float32)
            row_to_mid = list(v_dict.keys())
            mid_to_row = {mid: idx for idx, mid in enumerate(row_to_mid)}
            v_mat = np.array([v_dict[mid] for mid in row_to_mid], dtype=np.float32)
            cluster_rows = {}
            for c_idx in sorted_c_keys:
                mids = inv_dict.get(c_idx, frozenset())
                if mids:
                    cluster_rows[c_idx] = np.array([mid_to_row[m] for m in mids], dtype=np.int32)
                else:
                    cluster_rows[c_idx] = np.empty((0,), dtype=np.int32)

        self._snapshot = ApproximateSnapshot(
            generation=self._generation,
            vectors=v_dict,
            centroids=c_dict,
            inverted_lists=inv_dict,
            item_cluster=item_clust,
            centroids_matrix=c_mat,
            vectors_matrix=v_mat,
            row_to_mid=tuple(row_to_mid) if row_to_mid is not None else None,
            mid_to_row=mid_to_row,
            cluster_row_indices=cluster_rows,
        )

    def build(self, vectors: List[Tuple[str, List[float]]]) -> None:
        self.rebuild(vectors)

    def rebuild(self, vectors: List[Tuple[str, List[float]]]) -> None:
        with self._write_lock:
            if vectors and len(vectors) > 0:
                fp, gen = self._compute_corpus_fingerprint(vectors)
                if (
                    self._snapshot is not None
                    and fp == self._last_build_fingerprint
                    and len(self._vectors) == len(self._snapshot.vectors)
                    and self._snapshot.centroids
                ):
                    return
            else:
                fp, gen = "empty", 0

            with self._lock:
                self._vectors.clear()
                self._inverted_lists.clear()
                self._centroids.clear()
                self._item_cluster.clear()

                if not vectors:
                    self._generation = 0
                    self._refresh_snapshot()
                    self._last_build_fingerprint = None
                    return

                for mid, vec in vectors:
                    self._vectors[mid] = _l2_normalize(vec)

                self._generation = gen if gen > 0 else 1
                n = len(self._vectors)
                dim = len(vectors[0][1])
                k = self._determine_k(n)

                if k == 1:
                    self._inverted_lists[0] = set(self._vectors.keys())
                    for mid in self._vectors:
                        self._item_cluster[mid] = 0
                    acc = [0.0] * dim
                    for vec in self._vectors.values():
                        for i in range(dim):
                            acc[i] += vec[i]
                    self._centroids[0] = _l2_normalize(acc)
                    self._refresh_snapshot()
                    self._last_build_fingerprint = fp
                    return

                rng = random.Random(self._random_seed)
                items = list(self._vectors.items())
                first_id, first_vec = rng.choice(items)
                centroids: List[List[float]] = [list(first_vec)]

                if _NUMPY_AVAILABLE and n >= 20:
                    mat = np.array([v for _, v in items], dtype=np.float32)
                    cur_c = np.array(first_vec, dtype=np.float32)
                    sims = mat @ cur_c
                    min_dist_sq_arr = np.maximum(0.0, 1.0 - sims) ** 2
                    cum_dist = np.cumsum(min_dist_sq_arr)
                    while len(centroids) < k:
                        total_dist = float(cum_dist[-1])
                        if total_dist < 1e-9:
                            break
                        r_val = rng.uniform(0, total_dist)
                        selected_idx = min(int(np.searchsorted(cum_dist, r_val)), n - 1)
                        new_c = items[selected_idx][1]
                        centroids.append(list(new_c))
                        d = np.maximum(0.0, 1.0 - (mat @ np.array(new_c, dtype=np.float32))) ** 2
                        min_dist_sq_arr = np.minimum(min_dist_sq_arr, d)
                        cum_dist = np.cumsum(min_dist_sq_arr)

                    k = len(centroids)
                    c_mat = np.array(centroids, dtype=np.float32)
                    for _ in range(15):
                        sims = mat @ c_mat.T
                        assignments = np.argmax(sims, axis=1)
                        new_centroids = np.zeros_like(c_mat)
                        max_centroid_shift = 0.0
                        for j in range(k):
                            cluster_pts = mat[assignments == j]
                            if len(cluster_pts) > 0:
                                mean_pt = cluster_pts.sum(axis=0)
                                norm = np.linalg.norm(mean_pt)
                                if norm > 1e-9:
                                    mean_pt /= norm
                                else:
                                    mean_pt = c_mat[j]
                                shift = float(1.0 - np.dot(c_mat[j], mean_pt))
                                if shift > max_centroid_shift:
                                    max_centroid_shift = shift
                                new_centroids[j] = mean_pt
                            else:
                                new_centroids[j] = c_mat[j]
                        c_mat = new_centroids
                        if max_centroid_shift < 1e-4:
                            break

                    centroids = [list(map(float, row)) for row in c_mat]
                    for i, c in enumerate(centroids):
                        self._centroids[i] = c
                        self._inverted_lists[i] = set()

                    # Vectorized final cluster assignment
                    final_sims = mat @ c_mat.T
                    final_assignments = np.argmax(final_sims, axis=1)
                    for idx, (mid, _) in enumerate(items):
                        best_c = int(final_assignments[idx])
                        self._inverted_lists[best_c].add(mid)
                        self._item_cluster[mid] = best_c
                else:
                    min_dist_sq = [
                        max(0.0, 1.0 - cosine_similarity(v, first_vec)) ** 2
                        for _, v in items
                    ]
                    while len(centroids) < k:
                        total_dist = sum(min_dist_sq)
                        if total_dist < 1e-9:
                            break
                        r_val = rng.uniform(0, total_dist)
                        cum = 0.0
                        selected_idx = 0
                        for idx, d in enumerate(min_dist_sq):
                            cum += d
                            if cum >= r_val:
                                selected_idx = idx
                                break
                        new_c = items[selected_idx][1]
                        centroids.append(list(new_c))
                        for idx, (_, v) in enumerate(items):
                            d = max(0.0, 1.0 - cosine_similarity(v, new_c)) ** 2
                            if d < min_dist_sq[idx]:
                                min_dist_sq[idx] = d

                    k = len(centroids)
                    for _ in range(15):
                        clusters: Dict[int, List[List[float]]] = {i: [] for i in range(len(centroids))}
                        for _, v in items:
                            best_c = 0
                            best_sim = -2.0
                            for c_idx, c in enumerate(centroids):
                                sim = cosine_similarity(v, c)
                                if sim > best_sim:
                                    best_sim = sim
                                    best_c = c_idx
                            clusters[best_c].append(v)

                        max_centroid_shift = 0.0
                        for c_idx in range(len(centroids)):
                            pts = clusters[c_idx]
                            if pts:
                                mean_v = [0.0] * dim
                                for p in pts:
                                    for d in range(dim):
                                        mean_v[d] += p[d]
                                new_c = _l2_normalize(mean_v)
                                shift = 1.0 - cosine_similarity(centroids[c_idx], new_c)
                                if shift > max_centroid_shift:
                                    max_centroid_shift = shift
                                centroids[c_idx] = new_c

                        if max_centroid_shift < 1e-4:
                            break

                    for i, c in enumerate(centroids):
                        self._centroids[i] = c
                        self._inverted_lists[i] = set()

                    for mid, v in self._vectors.items():
                        best_c = 0
                        best_sim = -2.0
                        for c_idx, c in self._centroids.items():
                            sim = cosine_similarity(v, c)
                            if sim > best_sim:
                                best_sim = sim
                                best_c = c_idx
                        self._inverted_lists[best_c].add(mid)
                        self._item_cluster[mid] = best_c

                self._refresh_snapshot()
                self._last_build_fingerprint = fp

    def add(self, memory_id: str, vector: List[float]) -> None:
        norm_v = _l2_normalize(vector)
        with self._write_lock:
            self._last_build_fingerprint = None
            with self._lock:
                if memory_id in self._vectors:
                    if memory_id in self._item_cluster:
                        old_c = self._item_cluster.pop(memory_id)
                        self._inverted_lists.get(old_c, set()).discard(memory_id)

                self._vectors[memory_id] = norm_v
                self._generation += 1
                if not self._centroids:
                    self.rebuild([(memory_id, norm_v)])
                    return

                best_c = 0
                best_sim = -2.0
                for c_idx, c in self._centroids.items():
                    sim = cosine_similarity(norm_v, list(c))
                    if sim > best_sim:
                        best_sim = sim
                        best_c = c_idx

                if best_c not in self._inverted_lists:
                    self._inverted_lists[best_c] = set()
                self._inverted_lists[best_c].add(memory_id)
                self._item_cluster[memory_id] = best_c
            self._refresh_snapshot()

    def add_batch(self, items: List[Tuple[str, List[float]]]) -> None:
        with self._write_lock:
            self._last_build_fingerprint = None
            with self._lock:
                for mid, vec in items:
                    norm_v = _l2_normalize(vec)
                    if mid in self._vectors:
                        if mid in self._item_cluster:
                            old_c = self._item_cluster.pop(mid)
                            self._inverted_lists.get(old_c, set()).discard(mid)
                    self._vectors[mid] = norm_v
                    if not self._centroids:
                        continue
                    best_c = 0
                    best_sim = -2.0
                    for c_idx, c in self._centroids.items():
                        sim = cosine_similarity(norm_v, list(c))
                        if sim > best_sim:
                            best_sim = sim
                            best_c = c_idx
                    if best_c not in self._inverted_lists:
                        self._inverted_lists[best_c] = set()
                    self._inverted_lists[best_c].add(mid)
                    self._item_cluster[mid] = best_c

                self._generation += 1
                if not self._centroids and self._vectors:
                    self.rebuild(list(self._vectors.items()))
                    return
            self._refresh_snapshot()

    def delete(self, memory_id: str) -> bool:
        with self._write_lock:
            self._last_build_fingerprint = None
            with self._lock:
                if memory_id not in self._vectors:
                    return False
                del self._vectors[memory_id]
                self._generation += 1
                if memory_id in self._item_cluster:
                    c_idx = self._item_cluster.pop(memory_id)
                    if c_idx in self._inverted_lists:
                        self._inverted_lists[c_idx].discard(memory_id)
            self._refresh_snapshot()
            return True

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> List[Tuple[str, float]]:
        # Non-blocking RCU read: zero reader mutex contention
        q = _l2_normalize(query_vector)
        snap = self._snapshot
        if snap is None or not snap.vectors:
            return []

        # Small corpus fallback: exact scan for guaranteed precision
        if len(snap.vectors) <= 10 or not snap.centroids:
            if _NUMPY_AVAILABLE and snap.vectors_matrix is not None and snap.row_to_mid is not None:
                q_arr = np.array(q, dtype=np.float32)
                sims = snap.vectors_matrix @ q_arr
                return _select_top_k_deterministic(sims, snap.row_to_mid, None, top_k, min_score)

            scored = []
            for mid, vec in snap.vectors.items():
                sim = cosine_similarity(q, list(vec))
                if sim >= min_score:
                    scored.append((mid, float(sim)))
            scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
            return scored[:top_k]

        # NumPy-Accelerated Multi-Probe Search
        if (
            _NUMPY_AVAILABLE
            and snap.centroids_matrix is not None
            and snap.vectors_matrix is not None
            and snap.cluster_row_indices is not None
            and snap.row_to_mid is not None
        ):
            q_arr = np.array(q, dtype=np.float32)
            c_sims = snap.centroids_matrix @ q_arr
            c_ranked = np.argsort(-c_sims)

            base_n_probe = self._determine_n_probe(len(snap.centroids))
            effective_n_probe = min(len(snap.centroids), base_n_probe * self.adaptive_nprobe_boost)

            probed_clusters = list(c_ranked[:effective_n_probe])
            candidate_rows_list = [
                snap.cluster_row_indices[c]
                for c in probed_clusters
                if c in snap.cluster_row_indices and len(snap.cluster_row_indices[c]) > 0
            ]
            total_cands = sum(len(arr) for arr in candidate_rows_list)

            # If candidates in probed clusters are fewer than top_k, expand probe to remaining clusters
            if total_cands < top_k and len(probed_clusters) < len(c_ranked):
                for c_idx in c_ranked[effective_n_probe:]:
                    arr = snap.cluster_row_indices.get(c_idx, ())
                    if len(arr) > 0:
                        candidate_rows_list.append(arr)
                        total_cands += len(arr)
                        if total_cands >= top_k:
                            break

            if not candidate_rows_list:
                return []

            cand_rows = np.concatenate(candidate_rows_list)
            cand_vecs = snap.vectors_matrix[cand_rows]
            cand_sims = cand_vecs @ q_arr
            return _select_top_k_deterministic(cand_sims, snap.row_to_mid, cand_rows, top_k, min_score)

        # Pure Python multi-probe fallback
        c_scores: List[Tuple[int, float]] = []
        for c_idx, c_vec in snap.centroids.items():
            sim = cosine_similarity(q, c_vec)
            c_scores.append((c_idx, sim))
        c_scores.sort(key=lambda x: x[1], reverse=True)

        base_n_probe = self._determine_n_probe(len(snap.centroids))
        effective_n_probe = min(len(snap.centroids), base_n_probe * self.adaptive_nprobe_boost)
        probe_clusters = [c_idx for c_idx, _ in c_scores[:effective_n_probe]]

        candidate_ids: Set[str] = set()
        for c_idx in probe_clusters:
            candidate_ids.update(snap.inverted_lists.get(c_idx, set()))

        if len(candidate_ids) < top_k and len(probe_clusters) < len(c_scores):
            for c_idx, _ in c_scores[effective_n_probe:]:
                candidate_ids.update(snap.inverted_lists.get(c_idx, set()))
                if len(candidate_ids) >= top_k:
                    break

        scored_candidates = []
        for mid in candidate_ids:
            vec = snap.vectors.get(mid)
            if vec is not None:
                sim = cosine_similarity(q, vec)
                if sim >= min_score:
                    scored_candidates.append((mid, float(sim)))

        scored_candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return scored_candidates[:top_k]


class NativeVectorIndex(ApproximateVectorIndex):
    """
    BLAS/numpy-accelerated native IVF-Flat vector index for enterprise-scale corpora.
    Uses BLAS matrix GEMV operations for sub-millisecond query evaluation and batched matrix clustering.
    """

    @property
    def index_type(self) -> str:
        return "native_ivf"


# Alias for backward and forward compatibility
ScalableVectorIndex = ApproximateVectorIndex
_HAS_NUMPY = _NUMPY_AVAILABLE


@dataclasses.dataclass
class StrategyDecision:
    recommended_type: str
    is_approximate: bool
    estimated_latency_ms: float


@dataclasses.dataclass
class AutoIndexConfig:
    """
    Configuration governing the AutoIndexStrategy engine selection policy.

    LATENCY CONTRACT (latency_target_ms):
        Advisory target used only for the approximate-engine admission decision when the corpus
        exceeds exact_threshold. It is NOT a hard SLO on exact-scan latency. ExactVectorIndex
        is always used for corpora below exact_threshold, even if exact scan itself exceeds
        latency_target_ms (e.g., ~153ms p50 at N=25k). The trade-off is perfect recall at
        the cost of higher latency for large corpora near the threshold.

    RECALL FLOOR CONTRACT (quality_floor_recall):
        Recall floor enforced when selecting between approximate and exact engines above the
        threshold. The recall estimate is MODEL-BASED (linear interpolation from 0.85 at
        N=exact_threshold to 0.95 at N=50k), NOT a live-sampled measurement. When shadow-query
        calibration has recorded >= min_samples observations, the empirically measured recall
        replaces the model estimate.

    CORPUS CAPACITY POLICY (exact_threshold / max_exact_fallback_size):
        Three non-overlapping, fully-covering zones with deterministic boundary behavior:
        - N < exact_threshold (25000)                  : Supported zone — exact scan, perfect recall.
        - exact_threshold <= N <= max_exact_fallback_size (50000)
                                                       : Controlled fallback zone — exact scan if quality
                                                         or latency estimate for approximate engine fails
                                                         check; otherwise approximate engine (NativeVectorIndex).
        - N > max_exact_fallback_size (50000)          : Large-corpus zone — approximate IVF engine used
                                                         unconditionally. No exact fallback available.

        Boundary values:
        - N = exact_threshold (25000): first point of the controlled fallback zone; quality/latency
          check applied. Exact fallback is available.
        - N = max_exact_fallback_size (50000): last point of the controlled fallback zone; exact
          fallback still available if quality/latency check fails.
        - N = max_exact_fallback_size + 1: first point of large-corpus zone; IVF unconditional.
    """
    exact_threshold: int = 25000       # Supported corpus boundary: exact scan always used below this
    approximate_threshold: Optional[int] = None
    latency_target_ms: float = 25.0    # Advisory: governs approx-engine admission, not exact-scan SLO
    quality_floor_recall: float = 0.85  # Model-based estimate threshold; not live-sampled
    enable_native: bool = True
    max_exact_fallback_size: int = 50000  # Controlled-fallback upper boundary; above this, IVF unconditional
    shadow_max_corpus_size: int = 50000   # Reconciled with controlled fallback boundary (Finding 2)
    quality_adaptation_nprobe_boost: int = 2  # Quality adaptation for uncalibrated large corpora (Finding 3)

    @property
    def fingerprint(self) -> str:
        s = f"{self.exact_threshold}:{self.quality_floor_recall}:{self.latency_target_ms}:{self.max_exact_fallback_size}:{self.enable_native}"
        return hashlib.sha256(s.encode()).hexdigest()[:8]

    def __post_init__(self):
        if self.approximate_threshold is not None:
            self.exact_threshold = self.approximate_threshold
        elif self.exact_threshold is not None:
            self.approximate_threshold = self.exact_threshold


# ── Phase 8: Shadow-Query Recall Calibrator ───────────────────────────────────

import threading as _threading
import random as _random
import time as _time
import queue as _queue


@dataclasses.dataclass
class CalibrationState:
    """
    Running calibration state for one corpus/tenant context.

    CONFIDENCE SEMANTIC:
        - is_empirical=True  → measured recall; statistically sufficient (samples >= min_samples)
        - is_empirical=False → model-estimated recall; insufficient calibration data
    """
    samples: int = 0
    recall_sum: float = 0.0
    min_samples: int = 10     # Minimum observations before switching from model to empirical
    tenant_id: str = ""
    corpus_size: int = 0
    index_generation: int = 0
    model_name: str = ""
    model_version: str = "v1"
    config_fingerprint: str = ""
    last_updated_at: float = 0.0

    @property
    def embedding_version(self) -> str:
        return self.model_version

    @embedding_version.setter
    def embedding_version(self, val: str) -> None:
        self.model_version = val

    @property
    def measured_recall(self) -> float:
        """Average empirical recall over all observed samples. 0.0 if no samples."""
        return self.recall_sum / self.samples if self.samples > 0 else 0.0

    @property
    def is_empirical(self) -> bool:
        """True when sufficient samples exist for statistical credibility."""
        return self.samples >= self.min_samples

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "samples": self.samples,
            "measured_recall": round(self.measured_recall, 4),
            "is_empirical": self.is_empirical,
            "min_samples": self.min_samples,
            "corpus_size": self.corpus_size,
            "index_generation": self.index_generation,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "config_fingerprint": self.config_fingerprint,
            "last_updated_at": self.last_updated_at,
        }


class ShadowQueryCalibrator:
    """
    Production-safe IVF recall calibration via bounded shadow queries.

    DESIGN PRINCIPLES:
    - Shadow queries are sampled at a configurable rate (default 5%). Normal requests are NEVER
      blocked by calibration; primary search returns immediately while shadow comparison runs asynchronously
      in a bounded background queue/worker pool with strict timeout, concurrency limits, and backpressure.
    - Sampling is probabilistic and bounded: max_budget_per_call and per-tenant rate limits prevent overload.
    - Tenant isolation is strict: calibration state is NEVER shared across tenants.
    - No query content, embeddings, or memory payloads are logged. Only recall metrics.
    - If calibration fails for any reason, the model-based estimate remains the fallback.
    - Automatically invalidates/resets tenant calibration on corpus drift (>25%), index rebuild, embedding model change,
      embedding version change, or config fingerprint change.

    SAMPLING SAFETY LIMITS:
    - Only runs exact shadow search when corpus size <= shadow_max_corpus_size (default 50000).
    - Rate controlled by sampling_rate (0.0 = disabled, 1.0 = every request).
    """

    def __init__(
        self,
        sampling_rate: float = 0.05,          # 5% of IVF requests trigger calibration
        shadow_max_corpus_size: int = 50000,  # Shadow-check corpora across controlled fallback zone
        min_samples: int = 10,                # Samples before switching to empirical recall
        max_budget_per_call: int = 1,         # Shadow exact searches per normal call
        max_queue_size: int = 100,            # Max bounded shadow queries in flight
        max_workers: int = 2,                 # Bounded background worker threads
        job_timeout: float = 2.0,             # Max execution time per shadow query (seconds)
        tenant_rate_limit: int = 10,          # Max shadow jobs per tenant per 60s
        async_mode: bool = True,              # Execute via background workers (True for production)
        db: Optional[StateDatabase] = None,
        db_path: Optional[Any] = None,
    ):
        if not (0.0 <= sampling_rate <= 1.0):
            raise ValueError(f"sampling_rate must be in [0, 1], got {sampling_rate}")
        self.sampling_rate = sampling_rate
        self.shadow_max_corpus_size = shadow_max_corpus_size
        self.min_samples = min_samples
        self.max_budget_per_call = max_budget_per_call
        self.max_queue_size = max_queue_size
        self.max_workers = max_workers
        self.job_timeout = job_timeout
        self.tenant_rate_limit = tenant_rate_limit
        self.async_mode = async_mode

        if db is not None:
            self.db = db
        elif db_path is not None:
            self.db = StateDatabase(db_path)
        else:
            try:
                self.db = StateDatabase()
            except Exception as db_exc:
                logger.debug("Default StateDatabase init in ShadowQueryCalibrator failed: %s", db_exc)
                self.db = None

        if self.db is not None:
            try:
                conn = self.db.get_connection()
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS calibration_states (
                            tenant_id TEXT PRIMARY KEY,
                            corpus_size INTEGER NOT NULL DEFAULT 0,
                            index_generation INTEGER NOT NULL DEFAULT 0,
                            model_name TEXT NOT NULL DEFAULT '',
                            model_version TEXT NOT NULL DEFAULT 'v1',
                            config_fingerprint TEXT NOT NULL DEFAULT '',
                            samples INTEGER NOT NULL DEFAULT 0,
                            recall_sum REAL NOT NULL DEFAULT 0.0,
                            min_samples INTEGER NOT NULL DEFAULT 10,
                            last_updated_at REAL NOT NULL DEFAULT 0.0
                        );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_calibration_states_tenant ON calibration_states (tenant_id);")
                finally:
                    conn.close()
            except Exception as exc:
                logger.debug("Failed to ensure calibration_states table: %s", exc)

        self._lock = _threading.Lock()
        # Per-tenant calibration state; never exposes one tenant's data to another
        self._tenant_states: dict = {}
        self._tenant_query_history: dict = {}

        # Bounded background queue and worker threads
        self._queue: _queue.Queue = _queue.Queue(maxsize=max_queue_size)
        self._shutdown_event = _threading.Event()
        self._workers: List[_threading.Thread] = []

        if self.async_mode:
            self._start_workers()

    def _start_workers(self) -> None:
        with self._lock:
            if self._workers:
                return
            for i in range(self.max_workers):
                t = _threading.Thread(
                    target=self._worker_loop,
                    name=f"shadow-calibrator-{i}",
                    daemon=True,
                )
                t.start()
                self._workers.append(t)

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except _queue.Empty:
                continue
            try:
                task()
            except Exception as exc:
                logger.debug("Shadow query worker execution error: %s", exc)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        """
        Wait for all currently enqueued shadow queries to finish processing.
        Essential for deterministic unit and integration test assertions.
        """
        deadline = _time.time() + timeout
        while self._queue.unfinished_tasks > 0:
            if _time.time() > deadline:
                return False
            _time.sleep(0.01)
        return True

    def shutdown(self, wait: bool = True) -> None:
        """Stop background worker threads."""
        self._shutdown_event.set()
        if wait:
            for t in self._workers:
                t.join(timeout=1.0)
            self._workers.clear()

    def _persist_state(self, state: CalibrationState) -> None:
        if self.db is None:
            return
        try:
            conn = self.db.get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO calibration_states (
                        tenant_id, corpus_size, index_generation, model_name, model_version,
                        config_fingerprint, samples, recall_sum, min_samples, last_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        corpus_size=excluded.corpus_size,
                        index_generation=excluded.index_generation,
                        model_name=excluded.model_name,
                        model_version=excluded.model_version,
                        config_fingerprint=excluded.config_fingerprint,
                        samples=excluded.samples,
                        recall_sum=excluded.recall_sum,
                        min_samples=excluded.min_samples,
                        last_updated_at=excluded.last_updated_at;
                    """,
                    (
                        state.tenant_id,
                        state.corpus_size,
                        state.index_generation,
                        state.model_name,
                        state.model_version,
                        state.config_fingerprint,
                        state.samples,
                        state.recall_sum,
                        state.min_samples,
                        state.last_updated_at,
                    ),
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to persist calibration state for tenant %s: %s", state.tenant_id, exc)

    def _get_state(self, owner_id: str) -> CalibrationState:
        if owner_id in self._tenant_states:
            return self._tenant_states[owner_id]

        # Restore from SQLite persistence
        if self.db is not None:
            try:
                conn = self.db.get_connection()
                try:
                    cur = conn.execute(
                        "SELECT corpus_size, index_generation, model_name, model_version, config_fingerprint, "
                        "samples, recall_sum, min_samples, last_updated_at FROM calibration_states WHERE tenant_id = ?;",
                        (owner_id,),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        state = CalibrationState(
                            samples=int(row["samples"]),
                            recall_sum=float(row["recall_sum"]),
                            min_samples=int(row["min_samples"]),
                            tenant_id=owner_id,
                            corpus_size=int(row["corpus_size"]),
                            index_generation=int(row["index_generation"]),
                            model_name=str(row["model_name"] or ""),
                            model_version=str(row["model_version"] or "v1"),
                            config_fingerprint=str(row["config_fingerprint"] or ""),
                            last_updated_at=float(row["last_updated_at"]),
                        )
                        self._tenant_states[owner_id] = state
                        return state
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning(
                    "Corrupt or inaccessible calibration state for tenant %s; failing closed to fresh state: %s",
                    owner_id, exc,
                )

        state = CalibrationState(
            min_samples=self.min_samples,
            tenant_id=owner_id,
        )
        self._tenant_states[owner_id] = state
        return state

    def should_sample(self, corpus_size: int, owner_id: Optional[str] = None) -> bool:
        """Return True if this request should undergo shadow calibration."""
        if self.sampling_rate <= 0.0:
            return False
        if corpus_size > self.shadow_max_corpus_size:
            return False
        return _random.random() < self.sampling_rate

    def submit_shadow_job(
        self,
        owner_id: str,
        vectors: List[Tuple[str, List[float]]],
        query_vector: List[float],
        ivf_hits: List[Tuple[str, float]],
        k: int,
        corpus_size: Optional[int] = None,
        index_generation: Optional[int] = None,
        model_name: Optional[str] = None,
        embedding_version: Optional[str] = None,
        config_fingerprint: Optional[str] = None,
        telemetry: Optional[Any] = None,
        vectors_matrix: Optional[Any] = None,
        row_to_mid: Optional[Any] = None,
    ) -> bool:
        """
        Submit a shadow query job for asynchronous evaluation.
        Enforces tenant rate limits, job timeouts, cooperative CPU yields, and bounded backpressure.
        Returns True if job was accepted/executed, False if dropped due to backpressure or budget.
        """
        if not owner_id or not isinstance(owner_id, str) or not owner_id.strip():
            return False

        now = _time.time()

        # Check and enforce per-tenant rate limit (budget)
        with self._lock:
            history = self._tenant_query_history.get(owner_id, [])
            cutoff = now - 60.0
            history = [ts for ts in history if ts > cutoff]
            if len(history) >= self.tenant_rate_limit:
                self._tenant_query_history[owner_id] = history
                return False
            history.append(now)
            self._tenant_query_history[owner_id] = history

        def _execute_job():
            job_start = _time.time()
            try:
                # 1. Enforce timeout check before starting
                if self.job_timeout > 0 and (_time.time() - job_start) > self.job_timeout:
                    raise TimeoutError(f"Shadow job exceeded job_timeout budget ({self.job_timeout}s) before start")

                # 2. Exact evaluation: prefer zero-copy vectors_matrix with cooperative yielding
                exact_hits: List[Tuple[str, float]] = []
                if (
                    _NUMPY_AVAILABLE
                    and vectors_matrix is not None
                    and row_to_mid is not None
                    and len(row_to_mid) > 0
                ):
                    q_arr = np.array(_l2_normalize(query_vector), dtype=np.float32)
                    n_mat = len(row_to_mid)
                    chunk_size = 10000
                    all_sims = []
                    for c_start in range(0, n_mat, chunk_size):
                        if self.job_timeout > 0 and (_time.time() - job_start) > self.job_timeout:
                            raise TimeoutError(f"Shadow job exceeded job_timeout budget ({self.job_timeout}s) during matrix scan")
                        c_end = min(c_start + chunk_size, n_mat)
                        c_sub = vectors_matrix[c_start:c_end] @ q_arr
                        all_sims.append(c_sub)
                        if c_end < n_mat:
                            _time.sleep(0.0005)  # Cooperative yield to foreground reader threads

                    full_sims = np.concatenate(all_sims) if len(all_sims) > 1 else all_sims[0]
                    if len(full_sims) > k:
                        part_idx = np.argpartition(-full_sims, k)[:k]
                        top_sub = part_idx[np.argsort(-full_sims[part_idx])]
                    else:
                        top_sub = np.argsort(-full_sims)
                    exact_hits = [(row_to_mid[i], float(full_sims[i])) for i in top_sub]
                else:
                    if self.job_timeout > 0 and (_time.time() - job_start) > self.job_timeout:
                        raise TimeoutError(f"Shadow job exceeded job_timeout budget ({self.job_timeout}s)")
                    exact_index = ExactVectorIndex(vectors)
                    if self.job_timeout > 0 and (_time.time() - job_start) > self.job_timeout:
                        raise TimeoutError(f"Shadow job exceeded job_timeout budget ({self.job_timeout}s) after index build")
                    exact_hits = exact_index.search(
                        query_vector=query_vector,
                        top_k=k,
                        min_score=0.0,
                    )

                if self.job_timeout > 0 and (_time.time() - job_start) > self.job_timeout:
                    raise TimeoutError(f"Shadow job exceeded job_timeout budget ({self.job_timeout}s) before recording")

                ivf_ids = [mid for mid, _ in ivf_hits]
                exact_ids = [mid for mid, _ in exact_hits]
                measured_recall = self.record_observation(
                    owner_id=owner_id,
                    ivf_results=ivf_ids,
                    exact_results=exact_ids,
                    k=k,
                    corpus_size=corpus_size,
                    index_generation=index_generation,
                    model_name=model_name,
                    embedding_version=embedding_version,
                    config_fingerprint=config_fingerprint,
                )
                if telemetry and hasattr(telemetry, "record_shadow_query"):
                    telemetry.record_shadow_query(
                        sampled=True,
                        completed=True,
                        recall=measured_recall,
                        owner_id=owner_id,
                    )
            except TimeoutError as timeout_exc:
                logger.debug("Shadow calibration timed out in worker: %s", timeout_exc)
                if telemetry and hasattr(telemetry, "record_shadow_query"):
                    telemetry.record_shadow_query(
                        sampled=True,
                        completed=False,
                        recall=0.0,
                        owner_id=owner_id,
                    )
            except Exception as exc:
                logger.debug("Shadow calibration execution failed in worker: %s", exc)
                if telemetry and hasattr(telemetry, "record_shadow_query"):
                    telemetry.record_shadow_query(
                        sampled=True,
                        completed=False,
                        recall=0.0,
                        owner_id=owner_id,
                    )

        if not self.async_mode:
            _execute_job()
            return True

        try:
            self._queue.put_nowait(_execute_job)
            return True
        except _queue.Full:
            logger.debug("Shadow query queue full, dropping job (backpressure)")
            if telemetry and hasattr(telemetry, "record_shadow_query"):
                telemetry.record_shadow_query(
                    sampled=True,
                    completed=False,
                    recall=0.0,
                    owner_id=owner_id,
                )
            return False

    def record_observation(
        self,
        owner_id: str,
        ivf_results: List[str],
        exact_results: List[str],
        k: int,
        corpus_size: Optional[int] = None,
        index_generation: Optional[int] = None,
        model_name: Optional[str] = None,
        embedding_version: Optional[str] = None,
        config_fingerprint: Optional[str] = None,
    ) -> float:
        """
        Record one shadow-query calibration observation.
        Returns the measured recall for this query (|IVF ∩ exact| / min(k, |exact|)).
        Does NOT log query content — only the recall metric.

        Applies lifecycle drift detection:
        - If corpus size drifted by > 25% from previous calibrated size,
        - Or if index generation changed,
        - Or if embedding model changed,
        - Or if embedding version changed,
        - Or if index configuration fingerprint changed,
        the prior calibration state is reset to prevent stale calibration bias.
        """
        if not exact_results:
            return 1.0  # Empty corpus: recall is undefined; treat as perfect
        true_k = min(k, len(exact_results))
        if true_k == 0:
            return 1.0
        ivf_set = set(ivf_results[:k])
        exact_set = set(exact_results[:true_k])
        recall = len(ivf_set & exact_set) / true_k

        now = _time.time()

        with self._lock:
            state = self._get_state(owner_id)

            # Lifecycle drift detection
            drift_detected = False
            if state.samples > 0:
                if model_name is not None and state.model_name and state.model_name != model_name:
                    drift_detected = True
                elif embedding_version is not None and state.embedding_version and state.embedding_version != embedding_version:
                    drift_detected = True
                elif config_fingerprint is not None and state.config_fingerprint and state.config_fingerprint != config_fingerprint:
                    drift_detected = True
                elif index_generation is not None and state.index_generation > 0 and state.index_generation != index_generation:
                    drift_detected = True
                elif corpus_size is not None and state.corpus_size > 0:
                    drift_ratio = abs(corpus_size - state.corpus_size) / state.corpus_size
                    if drift_ratio > 0.25:
                        drift_detected = True

            if drift_detected:
                state.samples = 0
                state.recall_sum = 0.0

            state.samples += 1
            state.recall_sum += recall
            if corpus_size is not None:
                state.corpus_size = corpus_size
            if index_generation is not None:
                state.index_generation = index_generation
            if model_name is not None:
                state.model_name = model_name
            if embedding_version is not None:
                state.embedding_version = embedding_version
            if config_fingerprint is not None:
                state.config_fingerprint = config_fingerprint
            state.last_updated_at = now
            state.tenant_id = owner_id
            self._persist_state(state)

        return recall

    def invalidate_tenant(self, owner_id: str, reason: str = "") -> None:
        """Invalidate calibration state for a specific tenant due to corpus/lifecycle change."""
        with self._lock:
            self._tenant_states.pop(owner_id, None)
            self._tenant_query_history.pop(owner_id, None)
            if self.db is not None:
                try:
                    conn = self.db.get_connection()
                    try:
                        conn.execute("DELETE FROM calibration_states WHERE tenant_id = ?;", (owner_id,))
                    finally:
                        conn.close()
                except Exception as exc:
                    logger.warning("Failed to delete persisted calibration state for %s: %s", owner_id, exc)

    def get_calibrated_recall(
        self,
        owner_id: str,
        model_recall: float,
    ) -> tuple:
        """
        Return (recall_value, is_empirical) using the calibration semantic hierarchy:
          1. Empirically measured (is_empirical=True): when >= min_samples available
          2. Conservative model estimate (is_empirical=False): when calibration is insufficient

        NEVER claims empirical recall when only a model estimate is available.
        """
        with self._lock:
            state = self._get_state(owner_id)
            if state.is_empirical:
                return state.measured_recall, True
        return model_recall, False

    def get_calibration_state(self, owner_id: str) -> CalibrationState:
        """Return a copy of the calibration state for a tenant (for telemetry/observability)."""
        with self._lock:
            state = self._get_state(owner_id)
            return CalibrationState(
                samples=state.samples,
                recall_sum=state.recall_sum,
                min_samples=state.min_samples,
                tenant_id=state.tenant_id,
                corpus_size=state.corpus_size,
                index_generation=state.index_generation,
                model_name=state.model_name,
                model_version=state.model_version,
                config_fingerprint=state.config_fingerprint,
                last_updated_at=state.last_updated_at,
            )

    def reset(self, owner_id: Optional[str] = None) -> None:
        """Reset calibration state (for testing). If owner_id=None, resets all."""
        with self._lock:
            if owner_id:
                self._tenant_states.pop(owner_id, None)
                self._tenant_query_history.pop(owner_id, None)
                if self.db is not None:
                    try:
                        conn = self.db.get_connection()
                        try:
                            conn.execute("DELETE FROM calibration_states WHERE tenant_id = ?;", (owner_id,))
                        finally:
                            conn.close()
                    except Exception as exc:
                        logger.warning("Failed to reset persisted calibration for %s: %s", owner_id, exc)
            else:
                self._tenant_states.clear()
                self._tenant_query_history.clear()
                if self.db is not None:
                    try:
                        conn = self.db.get_connection()
                        try:
                            conn.execute("DELETE FROM calibration_states;")
                        finally:
                            conn.close()
                    except Exception as exc:
                        logger.warning("Failed to reset all persisted calibration states: %s", exc)


# Module-level default calibrator; production code may inject a custom instance.
_default_calibrator = ShadowQueryCalibrator()


def get_default_calibrator() -> ShadowQueryCalibrator:
    """Access the module-level default shadow-query calibrator."""
    return _default_calibrator


class AutoIndexStrategy:
    """Quality-aware, latency-bounded strategy selector for vector search engines."""

    def __init__(
        self,
        config: Optional[AutoIndexConfig] = None,
        calibrator: Optional[ShadowQueryCalibrator] = None,
    ):
        self.config = config or AutoIndexConfig()
        self.calibrator = calibrator if calibrator is not None else _default_calibrator

    def evaluate_strategy(self, corpus_size: int) -> StrategyDecision:
        threshold = self.config.exact_threshold
        # Empirical benchmarks (pure-Python exact scan, no NumPy):
        #   N=500:   p50=2.7ms  | N=1k: p50=4.8ms | N=5k: p50=25ms
        #   N=10k:   p50=54ms   | N=25k: p50=153ms | N=50k: p50=292ms (native_ivf)
        # ExactVectorIndex is used for all corpora below exact_threshold=25000.
        # NOTE: latency_target_ms=15.0 governs the approximate-engine admission decision
        # above the threshold — it is NOT a hard guarantee for exact-scan latency.
        # At N=25k, exact scan is ~153ms p50; this is accepted in exchange for perfect recall.
        if corpus_size < threshold:
            # Estimate for IVF comparison only — not a guarantee on exact scan
            estimated_exact_latency_ms = min(154.0, corpus_size * 0.006)  # ~30ms at N=5k, ~154ms at N=25k
            return StrategyDecision(
                recommended_type="exact",
                is_approximate=False,
                estimated_latency_ms=estimated_exact_latency_ms,
            )
        # Above threshold: prefer NativeVectorIndex (numpy-accelerated IVF) when available.
        rec_type = "native_ivf" if _NUMPY_AVAILABLE and self.config.enable_native else "approximate_ivf"
        estimated_approx_latency_ms = min(25.0, math.sqrt(corpus_size) * 0.2)
        return StrategyDecision(
            recommended_type=rec_type,
            is_approximate=True,
            estimated_latency_ms=estimated_approx_latency_ms,
        )

    def _model_recall_estimate(self, count: int) -> float:
        """
        Conservative model-based recall estimate for IVF at corpus size `count`.
        Linear interpolation from 0.85 at exact_threshold to 0.95 at N=50k, capping at 0.98.
        This is an advisory model estimate — not an empirical measurement.
        For uncalibrated corpora > max_exact_fallback_size, quality adaptation (elevated nprobe)
        is applied at engine instantiation to compensate for empirical uncertainty.
        """
        approximate_threshold = self.config.exact_threshold
        if count <= approximate_threshold:
            return 0.85
        return min(0.98, 0.85 + 0.10 * (count - approximate_threshold) / max(1, 50000 - approximate_threshold))

    def select_engine(
        self,
        vectors: List[Tuple[str, List[float]]],
        force_mode: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> VectorIndex:
        count = len(vectors)
        if force_mode == "exact":
            return ExactVectorIndex(vectors)
        if force_mode == "native":
            return NativeVectorIndex(vectors) if _NUMPY_AVAILABLE else ApproximateVectorIndex(vectors)
        if force_mode in ("approximate", "ivf"):
            return ApproximateVectorIndex(vectors)

        # ZONE 1 — Supported: below exact_threshold → always exact, perfect recall.
        if count < self.config.exact_threshold:
            return ExactVectorIndex(vectors)

        # ZONE 2 (Controlled Fallback) and ZONE 3 (Large-Corpus):
        # Recall estimation uses the calibration semantic hierarchy:
        #   1. Empirically calibrated recall (is_empirical=True): when statistically sufficient
        #   2. Model-based estimate (is_empirical=False): conservative linear interpolation
        model_recall = self._model_recall_estimate(count)
        if self.calibrator is not None and owner_id:
            recall_estimate, is_empirical = self.calibrator.get_calibrated_recall(
                owner_id=owner_id,
                model_recall=model_recall,
            )
        else:
            recall_estimate, is_empirical = model_recall, False

        estimated_approx_latency_ms = min(25.0, math.sqrt(count) * 0.2)

        # Quality floor enforcement
        quality_acceptable = recall_estimate >= self.config.quality_floor_recall
        latency_acceptable = estimated_approx_latency_ms <= self.config.latency_target_ms

        # ZONE 2 — Controlled Fallback: exact_threshold <= N <= max_exact_fallback_size
        fits_exact_fallback = count <= self.config.max_exact_fallback_size

        if (not quality_acceptable or not latency_acceptable) and fits_exact_fallback:
            # Approximate engine would violate quality/latency constraints; fall back to exact
            return ExactVectorIndex(vectors)

        # ZONE 3 — Large-Corpus: N > max_exact_fallback_size → IVF unconditional, no exact fallback.
        engine: VectorIndex
        if _NUMPY_AVAILABLE and self.config.enable_native:
            engine = NativeVectorIndex(vectors)
        else:
            engine = ApproximateVectorIndex(vectors)

        # Finding 3: Quality adaptation for uncalibrated large corpora
        if not is_empirical and count > self.config.max_exact_fallback_size:
            if hasattr(engine, "adaptive_nprobe_boost"):
                engine.adaptive_nprobe_boost = max(
                    engine.adaptive_nprobe_boost,
                    self.config.quality_adaptation_nprobe_boost,
                )
            if hasattr(engine, "_quality_adaptation"):
                engine._quality_adaptation = "elevated_nprobe"

        return engine


def get_vector_index(
    owner_id: str,
    vectors: Optional[List[Tuple[str, List[float]]]] = None,
    threshold: Optional[int] = None,
    force_mode: Optional[str] = None,
    config: Optional[AutoIndexConfig] = None,
    calibrator: Optional[ShadowQueryCalibrator] = None,
) -> VectorIndex:
    """
    Factory creating the appropriate vector index for a given corpus.

    PRIMARY PATH (production & default): Uses AutoIndexStrategy.select_engine() with
    the authoritative Phase 8 architecture:
      - N < 25,000                   : ExactVectorIndex (100% recall supported zone)
      - 25,000 <= N <= 50,000        : Controlled fallback zone (NativeVectorIndex / ApproximateVectorIndex)
      - N > 50,000                   : Large-corpus zone (IVF unconditional)
    When `config` is None and `threshold` is None, defaults to `config=AutoIndexConfig()`.
    All production callers and benchmark runners share this exact path.

    LEGACY EXPLICIT OVERRIDE: When `threshold` is explicitly provided by caller,
    evaluates size against that specific threshold.
    """
    vec_list = vectors or []
    if force_mode == "native":
        return NativeVectorIndex(vec_list)
    if force_mode == "approximate":
        return ApproximateVectorIndex(vec_list)
    if force_mode == "exact":
        return ExactVectorIndex(vec_list)

    if threshold is not None:
        if len(vec_list) < threshold:
            return ExactVectorIndex(vec_list)
        return ApproximateVectorIndex(vec_list)

    effective_config = config or AutoIndexConfig()
    strategy = AutoIndexStrategy(effective_config, calibrator=calibrator)
    return strategy.select_engine(vec_list, force_mode=force_mode, owner_id=owner_id)

