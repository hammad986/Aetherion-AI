"""
execution/replay.py — Phase 7.3: agent replay as a projection over the ledger.

The event log is append-only, so replay is a **fold**, not a store. This module
adds no table, no snapshot file and no second history authority. Every answer it
gives is recomputed from ``ExecutionStore.get_events(execution_id)``, which means
a replay can never disagree with the evidence ledger — there is nothing to drift.

That constraint is the whole design, and it is why the pruned
``routes/time_travel.py`` is not revived here: it reads Z31 forensic snapshots,
a *second* history authority whose divergence from the ledger is unobservable.
``IMPLEMENTATION_BLUEPRINT.md`` §1 Tier 2 records the disposition as EXTEND over
``truth_kernel.EvidenceLedger``, and ``IMPLEMENTATION_ORDER.md`` 7.3 states the
constraint in four words: **no new persistence.**

What the fold produces:

  ``ReplayFrame``     one event plus the cumulative state *after* applying it
  ``ReplayTimeline``  the frame list, a summary, and the derived terminal status
  ``ReplayEngine``    timeline / seek / frame / diff / bundle

Three properties callers depend on:

  * **Determinism.** The same event list always folds to the same frames. Frames
    carry ``seq`` (a stable 0-based index), so a UI scrubber, a CLI ``--frame``
    and an exported bundle all address the same instant by the same integer.
    Timestamps are *not* used for addressing: ``store.get_events`` orders by
    ``timestamp ASC`` and two events written inside the same millisecond can tie,
    which would make a timestamp-addressed seek ambiguous. Seeking by time is
    still offered, and it resolves to the last frame at-or-before the instant.

  * **Monotonicity.** Cumulative counters never decrease across ``seq``. The fold
    only ever appends, so ``frames[i].state`` is a prefix-sum of the first ``i+1``
    events. ``diff(a, b)`` is therefore a subtraction, not a second fold.

  * **Redaction at the boundary.** Payloads reach an operator's screen, a CLI
    stdout and an exported file. Secret-shaped keys are masked on the way out —
    see ``_redact``. The ledger keeps whatever it was given; this module never
    writes back, so redaction here cannot destroy evidence.

Import safety: gunicorn runs with ``--preload``, so an import-time exception here
would kill the master rather than one worker. Every cross-subsystem import is
optional and degrades to ``None``, reported through ``integration_status()`` —
the same contract ``execution/kanban.py`` and ``execution/event_bus.py`` use.

Back-compat: ``ExecutionReplayEngine`` remains, with ``reconstruct_timeline()``
and ``seek_state()`` returning the shapes they returned before. Two live callers
depend on them — ``execution/export.py:15`` and ``execution/replay_consistency.py:17``
— and this commit is not the place to migrate them.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aetherion.execution.replay")

# ── Optional edges ───────────────────────────────────────────────────────────
# One import per block, deliberately. A combined try would send every name to
# None when only one dependency is missing, silently blanking fields that were
# fine.

try:
    from execution.store import ExecutionStore
except Exception as _exc:                                        # pragma: no cover
    ExecutionStore = None                                        # type: ignore[assignment]
    logger.warning("[replay] execution store unavailable: %s", _exc)

try:
    from execution.events import EventTypes
except Exception as _exc:                                        # pragma: no cover
    EventTypes = None                                            # type: ignore[assignment]
    logger.warning("[replay] typed events unavailable: %s", _exc)

try:
    from execution.truth_kernel import get_ledger
except Exception as _exc:                                        # pragma: no cover
    get_ledger = None                                            # type: ignore[assignment]
    logger.warning("[replay] evidence ledger unavailable: %s", _exc)


def integration_status() -> Dict[str, bool]:
    """
    Which read edges resolved at import. Surfaced by the routes layer so an empty
    timeline is diagnosable from the response itself rather than by correlating
    server logs.
    """
    return {
        "store": ExecutionStore is not None,
        "events": EventTypes is not None,
        "ledger": get_ledger is not None,
    }


# ── Event-type constants ─────────────────────────────────────────────────────
# Read off EventTypes when it imported, with literal fallbacks so the fold still
# classifies correctly in a degraded process. The literals are the wire names in
# execution/events.py; they are duplicated here only as a degradation path.

def _et(name: str, fallback: str) -> str:
    return getattr(EventTypes, name, fallback) if EventTypes is not None else fallback


_TASK_REQUESTED = _et("TASK_REQUESTED", "task.requested")
_TASK_QUEUED = _et("TASK_QUEUED", "task.queued")
_TASK_STARTED = _et("TASK_STARTED", "task.started")
_TASK_PROGRESS = _et("TASK_PROGRESS", "task.progress")
_TASK_WAITING = _et("TASK_WAITING", "task.waiting")
_TASK_COMPLETED = _et("TASK_COMPLETED", "task.completed")
_TASK_SUCCEEDED = _et("TASK_SUCCEEDED", "task.succeeded")
_TASK_FAILED = _et("TASK_FAILED", "task.failed")
_TASK_CANCELLED = _et("TASK_CANCELLED", "task.cancelled")
_TASK_DEGRADED = _et("TASK_DEGRADED", "task.degraded")
_TASK_DEAD_LETTERED = _et("TASK_DEAD_LETTERED", "task.dead_lettered")
_TASK_RECOVERED = _et("TASK_RECOVERED", "task.recovered")
_TASK_REVIEW_PENDING = _et("TASK_REVIEW_PENDING", "task.review_pending")
_TASK_ACCEPTED = _et("TASK_ACCEPTED", "task.accepted")
_TASK_REJECTED = _et("TASK_REJECTED", "task.rejected")

_TOOL_CALLED = _et("TOOL_CALLED", "tool.called")
_TOOL_COMPLETED = _et("TOOL_COMPLETED", "tool.completed")
_TOOL_FAILED = _et("TOOL_FAILED", "tool.failed")
_FILE_MODIFIED = _et("FILE_MODIFIED", "file.modified")
_CMD_RUN = _et("CMD_RUN", "command.run")

_VERIFIER_STARTED = _et("VERIFIER_STARTED", "verifier.started")
_VERIFIER_PASSED = _et("VERIFIER_PASSED", "verifier.passed")
_VERIFIER_FAILED = _et("VERIFIER_FAILED", "verifier.failed")

_PROVIDER_DECISION = _et("PROVIDER_DECISION", "provider.decision")
_STREAM_CHUNK = _et("STREAM_CHUNK", "stream.chunk")
_STREAM_CLOSED = _et("STREAM_CLOSED", "stream.closed")

_LIFECYCLE = frozenset({
    _TASK_REQUESTED, _TASK_QUEUED, _TASK_STARTED, _TASK_PROGRESS, _TASK_WAITING,
    _TASK_COMPLETED, _TASK_SUCCEEDED, _TASK_FAILED, _TASK_CANCELLED,
    _TASK_DEGRADED, _TASK_DEAD_LETTERED, _TASK_RECOVERED,
    _TASK_REVIEW_PENDING, _TASK_ACCEPTED, _TASK_REJECTED,
})
_TOOL_EVENTS = frozenset({_TOOL_CALLED, _TOOL_COMPLETED, _TOOL_FAILED, _CMD_RUN})
_VERIFIER_EVENTS = frozenset({_VERIFIER_STARTED, _VERIFIER_PASSED, _VERIFIER_FAILED})
_STREAM_EVENTS = frozenset({_STREAM_CHUNK, _STREAM_CLOSED})

# Statuses from which nothing further can happen. Mirrors truth_kernel's set;
# used only to label the timeline, never to gate a read.
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "dead_lettered", "completed", "accepted", "rejected"})

# A transcript is assembled from stream chunks and can reach megabytes for a long
# session. Bounded so one replay of a runaway execution cannot exhaust a worker's
# memory or a browser tab. Truncation is reported, never silent.
_MAX_TRANSCRIPT_CHARS = 200_000

# Bound on frames returned in one response. A caller that needs more pages.
_DEFAULT_FRAME_LIMIT = 1000


# ── Redaction ────────────────────────────────────────────────────────────────

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|authorization|"
    r"private[_-]?key|access[_-]?key|session[_-]?key|bearer)",
    re.IGNORECASE,
)
_MASK = "***"


def _redact(value: Any, _depth: int = 0) -> Any:
    """
    Mask secret-shaped keys anywhere in a payload.

    Applied on the way *out* of the fold, never on the way in: the ledger is the
    evidence record and this module is forbidden from writing to it. A replay is
    therefore a redacted view of durable truth, not a redacted truth.

    Recursion is depth-bounded because payloads arrive from tool output and
    third-party MCP servers, which are not obliged to be shallow.
    """
    if _depth > 8:
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _MASK
            else:
                out[k] = _redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(v, _depth + 1) for v in value]
    return value


def _classify(event_type: str) -> str:
    """The frame kind a UI groups and colours by."""
    if event_type in _LIFECYCLE:
        return "lifecycle"
    if event_type in _TOOL_EVENTS:
        return "tool"
    if event_type == _FILE_MODIFIED:
        return "filesystem"
    if event_type in _VERIFIER_EVENTS:
        return "verifier"
    if event_type in _STREAM_EVENTS:
        return "stream"
    if event_type == _PROVIDER_DECISION:
        return "provider"
    return "other"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── State ────────────────────────────────────────────────────────────────────


@dataclass
class ReplayState:
    """
    Cumulative execution state after folding events ``0..seq``.

    Every field is monotonic under the fold — counters only increment, lists only
    append. That is what lets ``ReplayEngine.diff`` subtract two states instead of
    re-folding the range.

    ``status`` is derived by the same precedence ``truth_kernel.derive_status``
    uses, so a replayed status at the final frame equals the ledger's status.
    ``ReplayEngine._summary`` asserts exactly that and reports the result as
    ``status_agrees``, which is how a divergence becomes visible instead of
    quietly wrong.
    """

    status: str = "unknown"
    tools_called: int = 0
    tools_completed: int = 0
    tools_failed: int = 0
    files_modified: int = 0
    commands_run: int = 0
    verifier_passed: int = 0
    verifier_failed: int = 0
    errors: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    active_tools: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    providers_used: List[str] = field(default_factory=list)
    transcript_chars: int = 0
    transcript_truncated: bool = False
    verifier_backed: bool = False
    terminal: bool = False

    def snapshot(self) -> Dict[str, Any]:
        """
        A JSON-safe copy. Lists are copied, not aliased — frames must not share
        mutable state with the accumulator that produced them, or every frame
        would show the final state.
        """
        return {
            "status": self.status,
            "tools_called": self.tools_called,
            "tools_completed": self.tools_completed,
            "tools_failed": self.tools_failed,
            "files_modified": self.files_modified,
            "commands_run": self.commands_run,
            "verifier_passed": self.verifier_passed,
            "verifier_failed": self.verifier_failed,
            "errors": self.errors,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "active_tools": list(self.active_tools),
            "files_touched": list(self.files_touched),
            "providers_used": list(self.providers_used),
            "transcript_chars": self.transcript_chars,
            "transcript_truncated": self.transcript_truncated,
            "verifier_backed": self.verifier_backed,
            "terminal": self.terminal,
        }


@dataclass
class ReplayFrame:
    """
    One event, plus the cumulative state *after* it was applied.

    ``seq`` is the address. It is the index into the ordered event list and is
    stable for a given execution because the log is append-only — a frame's
    address cannot be invalidated by later activity.
    """

    seq: int
    event_id: str
    event_type: str
    kind: str
    timestamp: float
    elapsed_ms: int
    session_id: str
    correlation_id: str
    payload: Dict[str, Any]
    state: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "elapsed_ms": self.elapsed_ms,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "state": self.state,
        }


@dataclass
class ReplayTimeline:
    """The full projection for one execution."""

    execution_id: str
    session_id: str
    frames: List[ReplayFrame]
    summary: Dict[str, Any]
    integration: Dict[str, bool]
    truncated: bool = False
    total_frames: int = 0

    def to_dict(self, include_frames: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "execution_id": self.execution_id,
            "session_id": self.session_id,
            "summary": self.summary,
            "integration": self.integration,
            "truncated": self.truncated,
            "total_frames": self.total_frames,
            "frame_count": len(self.frames),
        }
        if include_frames:
            data["frames"] = [f.to_dict() for f in self.frames]
        return data

    @property
    def terminal_status(self) -> str:
        """Convenience property returning the folded final execution status."""
        return str(self.summary.get("final_status") or "")


# ── The fold ─────────────────────────────────────────────────────────────────


def _apply(state: ReplayState, event: Dict[str, Any]) -> None:
    """
    Apply one event to the accumulator, in place.

    Status precedence deliberately mirrors ``truth_kernel.derive_status`` rather
    than inventing a second opinion: terminal states latch and are never
    overwritten by a later non-terminal event, so an out-of-order arrival cannot
    walk a finished execution backwards into "running".
    """
    etype = event.get("event_type") or ""
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    # ── Lifecycle → status ──────────────────────────────────────────────────
    if etype == _TASK_CANCELLED:
        state.status = "cancelled"
    elif etype == _TASK_DEAD_LETTERED:
        state.status = "dead_lettered"
    elif etype == _TASK_FAILED:
        state.status = "failed"
        state.errors += 1
    elif etype == _VERIFIER_STARTED:
        # A verifier began: while it is unresolved the task is "verifying".
        # It is NOT the default for an ordinary (unverified) completion.
        state.verifier_started = True
        if state.status not in _TERMINAL_STATUSES:
            state.status = "verifying"
    elif etype == _VERIFIER_FAILED:
        state.status = "failed"
        state.verifier_failed += 1
        state.errors += 1
    elif etype == _VERIFIER_PASSED:
        state.verifier_passed += 1
        state.verifier_backed = True
        # A pass promotes a completed/verifying task to succeeded; it cannot
        # resurrect a cancelled or dead-lettered one.
        if state.status in ("verifying", "completed"):
            state.status = "succeeded"
    elif etype in (_TASK_COMPLETED, _TASK_SUCCEEDED):
        if state.status not in _TERMINAL_STATUSES:
            # Phase 9: unverified completion is COMPLETED; verifier-backed is
            # SUCCEEDED; "verifying" only when a verifier actually started.
            if state.verifier_backed:
                state.status = "succeeded"
            elif getattr(state, "verifier_started", False):
                state.status = "verifying"
            else:
                state.status = "completed"
    elif etype == _TASK_RECOVERED:
        state.retries += 1
        if state.status not in _TERMINAL_STATUSES:
            state.status = "running"
    elif etype == _TASK_DEGRADED:
        if state.status not in _TERMINAL_STATUSES:
            state.status = "degraded"
    elif etype == _TASK_WAITING:
        if state.status not in _TERMINAL_STATUSES:
            state.status = "waiting"
    elif etype == _TASK_STARTED:
        if state.status not in _TERMINAL_STATUSES:
            state.status = "running"
    elif etype == _TASK_QUEUED:
        if state.status in ("unknown", "requested"):
            state.status = "queued"
    elif etype == _TASK_REQUESTED:
        if state.status == "unknown":
            state.status = "requested"
    elif etype == _TASK_REVIEW_PENDING:
        if state.status not in ("cancelled", "dead_lettered", "failed"):
            state.status = "review_pending"
    elif etype == _TASK_ACCEPTED:
        if state.status not in ("cancelled", "dead_lettered"):
            state.status = "accepted"
    elif etype == _TASK_REJECTED:
        if state.status not in ("cancelled", "dead_lettered"):
            state.status = "rejected"

    # ── Tools ───────────────────────────────────────────────────────────────
    elif etype == _TOOL_CALLED:
        state.tools_called += 1
        tool = payload.get("tool") or payload.get("tool_name") or ""
        if tool and str(tool) not in state.active_tools:
            state.active_tools.append(str(tool))
    elif etype == _TOOL_COMPLETED:
        state.tools_completed += 1
    elif etype == _TOOL_FAILED:
        state.tools_failed += 1
        state.errors += 1
    elif etype == _CMD_RUN:
        state.commands_run += 1

    # ── Filesystem ──────────────────────────────────────────────────────────
    elif etype == _FILE_MODIFIED:
        state.files_modified += 1
        path = payload.get("file_path") or payload.get("path") or ""
        if path and str(path) not in state.files_touched:
            state.files_touched.append(str(path))

    # ── Stream ──────────────────────────────────────────────────────────────
    elif etype == _STREAM_CHUNK:
        chunk = payload.get("content") or payload.get("chunk") or ""
        if isinstance(chunk, str) and chunk:
            if state.transcript_chars + len(chunk) > _MAX_TRANSCRIPT_CHARS:
                state.transcript_truncated = True
                state.transcript_chars = _MAX_TRANSCRIPT_CHARS
            else:
                state.transcript_chars += len(chunk)

    # ── Provider / cost ─────────────────────────────────────────────────────
    # Token and cost fields ride on several event types, so these are read
    # outside the elif chain rather than under a single branch.
    if etype == _PROVIDER_DECISION:
        provider = payload.get("provider") or payload.get("selected") or ""
        if provider and str(provider) not in state.providers_used:
            state.providers_used.append(str(provider))

    state.prompt_tokens += _as_int(payload.get("prompt_tokens"))
    state.completion_tokens += _as_int(payload.get("completion_tokens"))
    # ``cost_usd`` is written by execution_budgeting (7.1) at the registry rate.
    # Replay sums what was recorded; it does not re-price, because a later price
    # change must not silently rewrite the cost of a past execution.
    state.cost_usd += _as_float(payload.get("cost_usd"))

    state.terminal = state.status in _TERMINAL_STATUSES


def _fold(events: List[Dict[str, Any]]) -> Tuple[List[ReplayFrame], ReplayState, str]:
    """
    Fold the event list into frames. Returns ``(frames, final_state, transcript)``.

    The transcript is accumulated here rather than inside ``ReplayState`` so that
    a per-frame state snapshot stays small — copying a growing transcript into
    every frame would make the projection quadratic in stream-chunk count.
    """
    state = ReplayState()
    frames: List[ReplayFrame] = []
    transcript_parts: List[str] = []
    transcript_len = 0

    start_ts = _as_float(events[0].get("timestamp")) if events else 0.0

    for seq, event in enumerate(events):
        _apply(state, event)

        etype = event.get("event_type") or ""
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}

        if etype == _STREAM_CHUNK and transcript_len < _MAX_TRANSCRIPT_CHARS:
            chunk = payload.get("content") or payload.get("chunk") or ""
            if isinstance(chunk, str) and chunk:
                room = _MAX_TRANSCRIPT_CHARS - transcript_len
                transcript_parts.append(chunk[:room])
                transcript_len += min(len(chunk), room)

        ts = _as_float(event.get("timestamp"))
        frames.append(ReplayFrame(
            seq=seq,
            event_id=str(event.get("event_id") or ""),
            event_type=etype,
            kind=_classify(etype),
            timestamp=ts,
            elapsed_ms=int(max(0.0, ts - start_ts) * 1000),
            session_id=str(event.get("session_id") or ""),
            correlation_id=str(event.get("correlation_id") or ""),
            payload=_redact(payload),
            state=state.snapshot(),
        ))

    return frames, state, "".join(transcript_parts)


# ── Engine ───────────────────────────────────────────────────────────────────


class ReplayEngine:
    """
    Projection engine over the append-only event log.

    Stateless by construction — no cache, no memo, no snapshot. Each call re-reads
    and re-folds. That is affordable because ``event_log`` is indexed on
    ``execution_id`` (``execution/store.py``: ``idx_exec_id``), and it is what
    guarantees a replay can never serve a view the ledger has since contradicted.
    """

    def __init__(self, store: Any = None):
        if store is not None:
            self.store = store
        elif ExecutionStore is not None:
            self.store = ExecutionStore()
        else:                                                    # pragma: no cover
            self.store = None
            logger.error("[replay] no execution store; every projection will be empty")

    # ── Reads ───────────────────────────────────────────────────────────────

    def _events(self, execution_id: str) -> List[Dict[str, Any]]:
        if self.store is None or not execution_id:
            return []
        try:
            return self.store.get_events(execution_id) or []
        except Exception as exc:
            logger.warning("[replay] event read failed (exec=%s): %s", execution_id, exc)
            return []

    def timeline(
        self,
        execution_id: str,
        limit: int = _DEFAULT_FRAME_LIMIT,
        offset: int = 0,
        kinds: Optional[List[str]] = None,
    ) -> ReplayTimeline:
        """
        The full projection, optionally windowed and filtered.

        ``limit``/``offset`` page over frames. Filtering by ``kinds`` drops frames
        from the *output* only — the fold always runs over every event, so a
        filtered frame's ``state`` still accounts for the events hidden from view.
        A filter that changed the fold would report a state that never existed.
        """
        events = self._events(execution_id)
        frames, final, transcript = _fold(events)

        session_id = str(events[-1].get("session_id") or "") if events else ""

        total = len(frames)
        if kinds:
            wanted = {str(k).strip().lower() for k in kinds if k}
            frames = [f for f in frames if f.kind in wanted]

        filtered_total = len(frames)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), _DEFAULT_FRAME_LIMIT))
        window = frames[offset:offset + limit]

        summary = self._summary(execution_id, events, final, transcript, total)
        return ReplayTimeline(
            execution_id=execution_id,
            session_id=session_id,
            frames=window,
            summary=summary,
            integration=integration_status(),
            truncated=(offset + len(window)) < filtered_total,
            total_frames=filtered_total,
        )

    def _summary(
        self,
        execution_id: str,
        events: List[Dict[str, Any]],
        final: ReplayState,
        transcript: str,
        total_frames: int,
    ) -> Dict[str, Any]:
        """
        Headline numbers, plus the ledger cross-check.

        ``status_agrees`` compares the folded status against
        ``truth_kernel.derive_status``. Both read the same rows, so a mismatch
        means this module's precedence has drifted from the kernel's — a real
        defect, surfaced as data rather than raised, because a divergent replay is
        still more useful to an operator than a 500.
        """
        first_ts = _as_float(events[0].get("timestamp")) if events else 0.0
        last_ts = _as_float(events[-1].get("timestamp")) if events else 0.0

        ledger_status = ""
        verifier_backed = final.verifier_backed
        if get_ledger is not None and execution_id:
            try:
                truth = get_ledger().derive_status(execution_id)
                ledger_status = getattr(truth, "status", "") or ""
                verifier_backed = bool(getattr(truth, "verifier_backed", verifier_backed))
            except Exception as exc:
                logger.debug("[replay] derive_status unavailable (exec=%s): %s", execution_id, exc)

        return {
            "event_count": len(events),
            "frame_count": total_frames,
            "started_at": first_ts,
            "ended_at": last_ts,
            "duration_ms": int(max(0.0, last_ts - first_ts) * 1000),
            "final_status": final.status,
            "ledger_status": ledger_status,
            # No ledger reachable ⇒ nothing to disagree with, so this is True
            # rather than a false alarm about a check that never ran.
            "status_agrees": (ledger_status == final.status) if ledger_status else True,
            "terminal": final.terminal,
            "verifier_backed": verifier_backed,
            "has_errors": final.errors > 0,
            "tools_called": final.tools_called,
            "tools_completed": final.tools_completed,
            "tools_failed": final.tools_failed,
            "files_modified": final.files_modified,
            "commands_run": final.commands_run,
            "retries": final.retries,
            "prompt_tokens": final.prompt_tokens,
            "completion_tokens": final.completion_tokens,
            "cost_usd": round(final.cost_usd, 6),
            "providers_used": list(final.providers_used),
            "files_touched": list(final.files_touched),
            "transcript_chars": len(transcript),
            "transcript_truncated": final.transcript_truncated,
        }

    def frame(self, execution_id: str, seq: int) -> Optional[ReplayFrame]:
        """One frame by its stable index. ``None`` when out of range."""
        frames, _, _ = _fold(self._events(execution_id))
        if not frames:
            return None
        seq = int(seq)
        if seq < 0 or seq >= len(frames):
            return None
        return frames[seq]

    def seek(
        self,
        execution_id: str,
        seq: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Reconstruct state at an instant, addressed by ``seq`` or ``timestamp``.

        ``seq`` wins when both are given: it is exact, whereas a timestamp can tie
        across events written inside the same millisecond. A timestamp resolves to
        the last frame at-or-before it — the state an observer would have seen.
        """
        frames, _, _ = _fold(self._events(execution_id))
        empty = {
            "execution_id": execution_id,
            "found": False,
            "seq": -1,
            "state": ReplayState().snapshot(),
            "transcript": "",
            "integration": integration_status(),
        }
        if not frames:
            return empty

        target: Optional[ReplayFrame] = None
        if seq is not None:
            idx = max(0, min(int(seq), len(frames) - 1))
            target = frames[idx]
        elif timestamp is not None:
            ts = _as_float(timestamp)
            for f in frames:
                if f.timestamp <= ts:
                    target = f
                else:
                    break
            if target is None:
                # An instant before the first event is a valid question with an
                # empty answer, not an error.
                found_empty = dict(empty)
                found_empty["found"] = True
                return found_empty
        else:
            target = frames[-1]

        # Transcript up to the seek point, re-derived rather than sliced: chunk
        # boundaries do not align with character counts.
        partial: List[str] = []
        partial_len = 0
        for f in frames[:target.seq + 1]:
            if f.event_type == _STREAM_CHUNK and partial_len < _MAX_TRANSCRIPT_CHARS:
                fp = f.payload or {}
                chunk = fp.get("content") or fp.get("chunk") or ""
                if isinstance(chunk, str) and chunk:
                    room = _MAX_TRANSCRIPT_CHARS - partial_len
                    partial.append(chunk[:room])
                    partial_len += min(len(chunk), room)

        return {
            "execution_id": execution_id,
            "found": True,
            "seq": target.seq,
            "event_id": target.event_id,
            "event_type": target.event_type,
            "timestamp": target.timestamp,
            "elapsed_ms": target.elapsed_ms,
            "state": target.state,
            "transcript": "".join(partial),
            "integration": integration_status(),
        }

    def diff(self, execution_id: str, from_seq: int, to_seq: int) -> Dict[str, Any]:
        """
        What changed between two frames.

        A subtraction, not a second fold — legitimate because every counter in
        ``ReplayState`` is monotonic. A reversed pair is normalised rather than
        refused, because a scrubber dragged backwards is a normal interaction.
        """
        frames, _, _ = _fold(self._events(execution_id))
        if not frames:
            return {"execution_id": execution_id, "found": False, "changes": {},
                    "integration": integration_status()}

        lo, hi = sorted((int(from_seq), int(to_seq)))
        lo = max(0, min(lo, len(frames) - 1))
        hi = max(0, min(hi, len(frames) - 1))

        a = frames[lo].state
        b = frames[hi].state

        numeric = (
            "tools_called", "tools_completed", "tools_failed", "files_modified",
            "commands_run", "verifier_passed", "verifier_failed", "errors",
            "retries", "prompt_tokens", "completion_tokens", "transcript_chars",
        )
        changes: Dict[str, Any] = {}
        for key in numeric:
            delta = _as_int(b.get(key)) - _as_int(a.get(key))
            if delta:
                changes[key] = delta

        cost_delta = round(_as_float(b.get("cost_usd")) - _as_float(a.get("cost_usd")), 6)
        if cost_delta:
            changes["cost_usd"] = cost_delta

        before_files = set(a.get("files_touched") or [])
        after_files = set(b.get("files_touched") or [])
        before_tools = set(a.get("active_tools") or [])
        after_tools = set(b.get("active_tools") or [])

        return {
            "execution_id": execution_id,
            "found": True,
            "from_seq": lo,
            "to_seq": hi,
            "from_status": a.get("status"),
            "to_status": b.get("status"),
            "status_changed": a.get("status") != b.get("status"),
            "elapsed_ms": frames[hi].elapsed_ms - frames[lo].elapsed_ms,
            "events_between": hi - lo,
            "changes": changes,
            "files_added": sorted(after_files - before_files),
            "tools_added": sorted(after_tools - before_tools),
            "event_types": [f.event_type for f in frames[lo + 1:hi + 1]][:200],
            "integration": integration_status(),
        }

    def transcript(self, execution_id: str) -> Dict[str, Any]:
        """The assembled stream transcript, bounded and flagged when truncated."""
        _, final, text = _fold(self._events(execution_id))
        return {
            "execution_id": execution_id,
            "transcript": text,
            "chars": len(text),
            "truncated": final.transcript_truncated,
        }

    def bundle(self, execution_id: str, include_frames: bool = True) -> Dict[str, Any]:
        """
        A portable, self-describing export of one replay.

        Redacted by construction — every frame payload passed through ``_redact``
        during the fold. This is the shape the CLI writes to a file and the
        desktop app persists into its sandboxed workspace, both of which leave the
        server's trust boundary, which is why redaction is not optional here.
        """
        tl = self.timeline(execution_id, limit=_DEFAULT_FRAME_LIMIT)
        text = self.transcript(execution_id)
        return {
            "schema_version": "replay/v1",
            "kind": "aetherion.replay.bundle",
            "execution_id": execution_id,
            "session_id": tl.session_id,
            "exported_at": time.time(),
            "source": "execution.replay.ReplayEngine",
            "projection": "fold over ExecutionStore.event_log (append-only)",
            "summary": tl.summary,
            "integration": tl.integration,
            "frames": [f.to_dict() for f in tl.frames] if include_frames else [],
            "frame_count": len(tl.frames) if include_frames else 0,
            "total_frames": tl.total_frames,
            "truncated": tl.truncated,
            "transcript": text["transcript"],
            "transcript_truncated": text["truncated"],
        }

    def list_replayable(
        self,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Executions that have a replay, newest-touched first.

        Enumeration comes from the ``executions`` snapshot table because the event
        log alone cannot answer "which executions exist" cheaply. Status is *not*
        taken from that table — ``derive_status`` remains the truth — so each row
        is re-derived. Bounded, because this is an index for a picker, not a
        report.
        """
        if self.store is None:
            return []
        try:
            rows = self.store.list_executions(session_id=session_id, limit=int(limit)) or []
        except Exception as exc:
            logger.warning("[replay] execution enumeration failed: %s", exc)
            return []

        out: List[Dict[str, Any]] = []
        for row in rows:
            execution_id = str(row.get("execution_id") or "")
            if not execution_id:
                continue
            entry: Dict[str, Any] = {
                "execution_id": execution_id,
                "session_id": str(row.get("session_id") or ""),
                "started_at": _as_float(row.get("started_at")),
                "updated_at": _as_float(row.get("updated_at")),
                "snapshot_status": str(row.get("status") or ""),
                "status": str(row.get("status") or ""),
                "event_count": 0,
                "terminal": False,
            }
            if get_ledger is not None:
                try:
                    truth = get_ledger().derive_status(execution_id)
                    entry["status"] = getattr(truth, "status", "") or entry["snapshot_status"]
                    entry["event_count"] = _as_int(getattr(truth, "evidence_count", 0))
                    entry["terminal"] = bool(getattr(truth, "terminal", False))
                except Exception:
                    pass
            out.append(entry)
        return out


# ── Back-compat surface ──────────────────────────────────────────────────────


class ExecutionReplayEngine(ReplayEngine):
    """
    The pre-7.3 interface, preserved in shape.

    ``execution/export.py:15`` and ``execution/replay_consistency.py:17`` import
    this name and call these two methods. Both keep their original return shapes
    so 7.3 cannot regress them; new callers should use ``ReplayEngine``.
    """

    def reconstruct_timeline(self, execution_id: str) -> Dict[str, Any]:
        """
        Original shape: ``{execution_id, summary, timeline}``, or
        ``{execution_id, timeline: [], state: "unknown"}`` when there is nothing.

        ``export.py`` and ``replay_consistency.py`` both branch on an empty
        ``timeline``, so the empty case must stay exactly as it was.
        """
        events = self._events(execution_id)
        if not events:
            return {"execution_id": execution_id, "timeline": [], "state": "unknown"}

        frames, final, transcript = _fold(events)
        summary = self._summary(execution_id, events, final, transcript, len(frames))

        timeline: List[Dict[str, Any]] = []
        for f in frames:
            if f.kind == "lifecycle":
                timeline.append({"timestamp": f.timestamp, "type": "lifecycle",
                                 "event": f.event_type, "details": f.payload})
            elif f.event_type == _TOOL_CALLED:
                timeline.append({"timestamp": f.timestamp, "type": "tool",
                                 "tool": f.payload.get("tool"), "details": f.payload})
            elif f.event_type == _FILE_MODIFIED:
                timeline.append({"timestamp": f.timestamp, "type": "filesystem",
                                 "file": f.payload.get("file_path"), "details": f.payload})

        return {
            "execution_id": execution_id,
            "summary": {
                "total_tools_called": summary["tools_called"],
                "total_files_modified": summary["files_modified"],
                "has_errors": summary["has_errors"],
                "final_status": summary["final_status"],
                "duration_ms": summary["duration_ms"],
            },
            "timeline": timeline,
        }

    def seek_state(self, execution_id: str, target_timestamp: float) -> Dict[str, Any]:
        """Original shape: active tools, files so far, last status, transcript."""
        result = self.seek(execution_id, timestamp=target_timestamp)
        state = result.get("state") or {}
        status = state.get("status") or "queued"
        return {
            "active_tools": list(state.get("active_tools") or []),
            "files_modified_up_to_point": list(state.get("files_touched") or []),
            "last_known_status": "queued" if status == "unknown" else status,
            "stream_transcript": result.get("transcript") or "",
        }


# ── Singleton ────────────────────────────────────────────────────────────────

_engine: Optional[ReplayEngine] = None


def get_replay_engine() -> ReplayEngine:
    """
    Process-wide engine.

    Safe to share without a lock: the engine holds no mutable state beyond its
    store handle, and ``ExecutionStore`` opens a connection per call rather than
    keeping one open across threads.
    """
    global _engine
    if _engine is None:
        _engine = ReplayEngine()
    return _engine
