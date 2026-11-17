"""
backend/execution/memory/embeddings.py — Production Embedding Provider Infrastructure for Phase 6.
Includes Circuit Breaker, Exponential Backoff, Rate Limiting, Health Checks, and Secret Redaction.
"""
from __future__ import annotations

import abc
import enum
import hashlib
import ipaddress
import json
import logging
import math
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aetherion.memory.embeddings")

DEFAULT_EMBEDDING_DIM = 128


def validate_safe_destination_url(url: str) -> None:
    """
    Validate that an external embedding URL uses a safe protocol and does not target
    internal loopback, RFC1918 private, link-local, or cloud metadata endpoints (SSRF defense).
    """
    if not url or not isinstance(url, str):
        raise ValueError("[SECURITY] Destination URL must be a non-empty string.")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"[SECURITY] Invalid protocol '{parsed.scheme}'. Only https:// and http:// endpoints are permitted."
        )

    # In production and staging environments, plaintext HTTP is strictly forbidden to prevent
    # bearer token exfiltration over unencrypted public networks.
    is_prod_or_staging = os.environ.get("AETHERION_ENV", "").lower() in ("production", "staging")
    allow_insecure_http = os.environ.get("AETHERION_ALLOW_INSECURE_HTTP_EMBEDDINGS") == "1"
    if parsed.scheme == "http" and is_prod_or_staging and not allow_insecure_http:
        raise ValueError(
            "[SECURITY] Plaintext HTTP transport is strictly forbidden in production and staging environments. "
            "HTTPS is mandatory to protect credentials in transit. To allow HTTP in testing or dev, set "
            "AETHERION_ALLOW_INSECURE_HTTP_EMBEDDINGS=1."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"[SECURITY] Invalid destination URL '{url}': Missing hostname.")

    port = parsed.port

    # Disallow cloud metadata endpoints immediately
    blocked_hostnames = {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.internal",
        "instance-data",
        "100.100.100.200",
    }
    if hostname.lower() in blocked_hostnames:
        raise ValueError(
            f"[SECURITY] SSRF blocked: Access to cloud metadata endpoint '{hostname}' is strictly prohibited."
        )

    allow_local = os.environ.get("AETHERION_ALLOW_LOCAL_EMBEDDING_ENDPOINT") == "1"
    if allow_local:
        return

    # Check for direct localhost string
    if hostname.lower() in ("localhost", "ip6-localhost", "ip6-loopback"):
        raise ValueError(
            f"[SECURITY] SSRF blocked: Loopback destination '{hostname}' is not permitted in production."
        )

    try:
        addr_info = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        try:
            ip = ipaddress.ip_address(hostname)
            addr_info = [(None, None, None, None, (str(ip), 0))]
        except ValueError:
            raise ValueError(f"[SECURITY] Unable to resolve destination host '{hostname}': {exc}") from exc

    for item in addr_info:
        sockaddr = item[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(
                    f"[SECURITY] SSRF blocked: Destination '{hostname}' resolves to restricted IP '{ip_str}'."
                )
        except ValueError as err:
            if "[SECURITY]" in str(err):
                raise
            pass


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevents HTTP 3xx redirects from hopping to private or cloud metadata IPs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_safe_destination_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)



class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(RuntimeError):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""
    pass


class CircuitBreaker:
    """
    Thread-safe circuit breaker preventing cascading failures to remote embedding providers.
    States:
      - CLOSED: normal operation; consecutive failures >= failure_threshold trips circuit to OPEN.
      - OPEN: requests immediately fail fast with CircuitBreakerOpenException.
      - HALF_OPEN: after recovery_timeout, one probe request is permitted to test recovery.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.1, recovery_timeout)
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._last_failure_reason = ""

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and self._opened_at is not None:
                if (time.time() - self._opened_at) >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def can_execute(self) -> bool:
        """Check if request is allowed through."""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._last_failure_reason = ""

    def record_failure(self, reason: str = "") -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_reason = reason
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            cur_state = self.state.value
            return {
                "state": cur_state,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_reason": self._last_failure_reason,
            }


class EmbeddingProvider(abc.ABC):
    """Abstract base class for memory vector embedding providers."""

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        """Name/identifier of the embedding model."""
        ...

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the generated vector embeddings."""
        ...

    @abc.abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Compute normalized vector embeddings for a batch of text strings."""
        ...

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Compute normalized vector embeddings with batch chunking."""
        if not texts:
            return []
        results: List[List[float]] = []
        bs = max(1, batch_size)
        for i in range(0, len(texts), bs):
            chunk = texts[i : i + bs]
            chunk_embeddings = self.embed(chunk)
            results.extend(chunk_embeddings)
        return results

    def embed_query(self, text: str) -> List[float]:
        """Compute normalized vector embedding for a single search query."""
        results = self.embed([text])
        if results and len(results[0]) == self.dimension:
            return results[0]
        return [0.0] * self.dimension

    def health_check(self) -> Dict[str, Any]:
        """Verify provider health and capability."""
        try:
            vec = self.embed_query("health check probe")
            valid = (len(vec) == self.dimension)
            return {
                "status": "HEALTHY" if valid else "DEGRADED",
                "model": self.model_name,
                "dimension": self.dimension,
                "healthy": valid,
            }
        except Exception as exc:
            return {
                "status": "UNHEALTHY",
                "model": self.model_name,
                "dimension": self.dimension,
                "healthy": False,
                "error": str(exc),
            }


class DeterministicLocalEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic local embedding generator for local dev, offline environments, and CI tests.
    Uses term-hashing and character n-gram projections with L2-normalization.
    Guarantees 100% reproducible float vectors across all platforms with zero network calls.
    """

    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIM, dim: Optional[int] = None):
        if dim is not None:
            dimension = dim
        self._dimension = max(16, dimension)
        self._model_name = f"deterministic-local-v1-{self._dimension}d"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: str | List[str]) -> Any:
        """Generate deterministic unit-length vector for a single text or list of texts."""
        if isinstance(texts, str):
            return self._vectorize_text(texts)
        embeddings: List[List[float]] = []
        for text in texts:
            vec = self._vectorize_text(text)
            embeddings.append(vec)
        return embeddings

    def _vectorize_text(self, text: str) -> List[float]:
        if not text or not isinstance(text, str) or not text.strip():
            # Return zero vector if text is empty
            return [0.0] * self._dimension

        vec = [0.0] * self._dimension
        tokens = re.findall(r"\b\w+\b", text.lower())
        if not tokens:
            tokens = [text.lower().strip()]

        for token in tokens:
            # Word-level hash projection
            h_word = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
            idx_word = h_word % self._dimension
            sign_word = 1.0 if ((h_word >> 16) & 1) == 0 else -1.0
            vec[idx_word] += sign_word * 1.5

            # 3-gram subtoken projections to capture morphological similarity
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    ngram = token[i : i + 3]
                    h_ng = int(hashlib.md5(ngram.encode("utf-8")).hexdigest()[:8], 16)
                    idx_ng = h_ng % self._dimension
                    sign_ng = 1.0 if ((h_ng >> 16) & 1) == 0 else -1.0
                    vec[idx_ng] += sign_ng * 0.5

        # L2-normalize the vector so cosine similarity equals the dot product
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-9:
            return [v / norm for v in vec]
        return [0.0] * self._dimension


class RemoteAPIEmbeddingProvider(EmbeddingProvider):
    """
    Production embedding provider for remote AI APIs (OpenAI, Anthropic, or compatible).
    Includes circuit breaker, retries with exponential backoff, rate limiting, and secret sanitization.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: Optional[str] = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        self._model_name = model_name
        self._dimension = dimension
        self._base_url = base_url or os.environ.get("EMBEDDINGS_BASE_URL", "https://api.openai.com/v1")
        self._timeout = max(1.0, timeout)
        self._max_retries = max(0, max_retries)
        self._backoff_factor = max(1.0, backoff_factor)
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        if not self._api_key:
            raise ValueError(
                "[SECURITY] RemoteAPIEmbeddingProvider requires an API key. "
                "Set OPENAI_API_KEY or configure AETHERION_EMBEDDING_PROVIDER=local."
            )

        # Validate destination URL to prevent SSRF at initialization
        validate_safe_destination_url(self._base_url)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _sanitize_error(self, err_msg: str) -> str:
        """Scrub secret credentials from exception messages."""
        if self._api_key and self._api_key in err_msg:
            return err_msg.replace(self._api_key, "***REDACTED***")
        return err_msg

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Call external embedding endpoint with batching, retries, and circuit breaker."""
        if not texts:
            return []

        # Circuit breaker fast-fail
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenException(
                "Embedding circuit breaker is OPEN due to repeated upstream failures. "
                "Failing fast to protect system resources."
            )

        endpoint = f"{self._base_url.rstrip('/')}/embeddings"
        validate_safe_destination_url(endpoint)

        payload = {
            "input": texts,
            "model": self._model_name,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        last_exc: Optional[Exception] = None
        opener = urllib.request.build_opener(SafeRedirectHandler())
        for attempt in range(self._max_retries + 1):
            try:
                with opener.open(req, timeout=self._timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    items = result.get("data", [])
                    items.sort(key=lambda x: x.get("index", 0))
                    embeddings = [item["embedding"] for item in items]

                    # Validate dimensions
                    for emb in embeddings:
                        if len(emb) != self._dimension:
                            raise ValueError(
                                f"Remote provider returned invalid vector dimensions: expected {self._dimension}, got {len(emb)}"
                            )

                    self.circuit_breaker.record_success()
                    return embeddings

            except Exception as exc:
                last_exc = exc
                sanitized_msg = self._sanitize_error(str(exc))
                logger.warning(
                    "Remote embedding attempt %d/%d failed: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    sanitized_msg,
                )
                if attempt < self._max_retries:
                    # Exponential backoff with jitter
                    sleep_time = (self._backoff_factor ** attempt) * 0.2 + random.uniform(0.01, 0.05)
                    time.sleep(sleep_time)

        # All retries exhausted -> record failure in circuit breaker
        sanitized_err = self._sanitize_error(str(last_exc))
        self.circuit_breaker.record_failure(reason=sanitized_err)
        raise RuntimeError(f"Remote embedding request failed after retries: {sanitized_err}") from last_exc


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two float vectors. Vectors should ideally be L2-normalized."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 < 1e-9 or norm2 < 1e-9:
        return 0.0
    sim = dot / (norm1 * norm2)
    # Clamp to [-1.0, 1.0] to guard against floating-point inaccuracy
    return max(-1.0, min(1.0, sim))


def get_default_embedding_provider() -> EmbeddingProvider:
    """
    Factory resolving the active embedding provider based on environment variables.
    Defaults to DeterministicLocalEmbeddingProvider for local dev / tests.
    """
    provider_type = os.environ.get("AETHERION_EMBEDDING_PROVIDER", "local").strip().lower()

    if provider_type in ("openai", "remote"):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key:
            return RemoteAPIEmbeddingProvider(api_key=api_key)
        logger.warning("OPENAI_API_KEY not set; falling back to DeterministicLocalEmbeddingProvider")
        return DeterministicLocalEmbeddingProvider()

    return DeterministicLocalEmbeddingProvider()
