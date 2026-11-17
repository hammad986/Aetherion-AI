"""
Integration tests for Scheduler Atomicity and Concurrent Dispatching Race Remediation (P2-3).
Proves:
1. Genuine concurrent contenders competing for queue items produce exactly one DISPATCHED verdict per item.
2. Contradictory ownership decisions are physically impossible under CAS.
3. Stale worker fencing prevents unauthorized state mutations.
4. Works both with SqliteFabricStore and PostgresFabricStore (when DB available).
"""

import os
import time
import uuid
import concurrent.futures
import pytest

from backend.execution.fabric.models import (
    QueueItemStatus,
    SchedulingVerdict,
)
from backend.execution.fabric.store import SqliteFabricStore
from backend.execution.fabric.postgres_store import PostgresFabricStore
from backend.execution.fabric.queue import DurableTaskQueue
from backend.execution.fabric.worker_registry import WorkerRegistry
from backend.execution.fabric.scheduler import ExecutionScheduler


def _setup_sqlite_harness(tmp_path):
    db_file = tmp_path / "scheduler_atomicity.db"
    store = SqliteFabricStore(db_path=str(db_file))
    queue = DurableTaskQueue(store=store)
    registry = WorkerRegistry(store=store)
    scheduler = ExecutionScheduler(queue=queue, worker_registry=registry)
    return store, queue, registry, scheduler


def _register_workers(registry, num_workers=5):
    workers = []
    for i in range(num_workers):
        w = registry.register_worker(
            worker_id=f"worker_contender_{i}_{uuid.uuid4().hex[:6]}",
            session_id=f"session_{i}_{uuid.uuid4().hex[:4]}",
            process_id=1000 + i,
            max_concurrency=5,
            supported_capabilities={"python", "git"},
        )
        workers.append(w)
    return workers


def test_concurrent_scheduler_dispatch_single_item(tmp_path):
    """
    10 concurrent threads call schedule_cycle() on a queue containing 1 item.
    INVARIANT: Exactly ONE worker is assigned and exactly ONE DISPATCHED decision is recorded.
    """
    store, queue, registry, scheduler = _setup_sqlite_harness(tmp_path)
    workers = _register_workers(registry, num_workers=5)

    enqueued = queue.enqueue(
        task_id="task_contention_001",
        tenant_id="tenant_alpha",
        required_capabilities=["python"],
        priority=10,
    )

    # 10 concurrent scheduler threads competing
    num_contenders = 10
    results = []

    def _contender_run():
        return scheduler.schedule_cycle(limit=10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_contenders) as executor:
        futures = [executor.submit(_contender_run) for _ in range(num_contenders)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Collect all DISPATCHED decisions from return values
    all_dispatched_returned = [
        d for decisions in results for d in decisions if d.verdict == SchedulingVerdict.DISPATCHED
    ]
    assert len(all_dispatched_returned) == 1, (
        f"Expected exactly 1 DISPATCHED verdict in returns, got {len(all_dispatched_returned)}"
    )

    # Inspect authoritative store decisions
    stored_decisions = store.list_decisions(task_id="task_contention_001")
    dispatched_stored = [d for d in stored_decisions if d.verdict == SchedulingVerdict.DISPATCHED]
    assert len(dispatched_stored) == 1, (
        f"Expected exactly 1 stored DISPATCHED decision, got {len(dispatched_stored)}"
    )

    # Check queue item state
    final_item = queue.get_item(enqueued.item_id)
    assert final_item is not None
    assert final_item.status == QueueItemStatus.CLAIMED
    assert final_item.assigned_worker_id is not None
    assert final_item.lease_id is not None
    assert dispatched_stored[0].assigned_worker_id == final_item.assigned_worker_id
    assert final_item.assigned_worker_id in [w.worker_id for w in workers]


def test_concurrent_scheduler_dispatch_multiple_items(tmp_path):
    """
    Contention over 20 items by 15 concurrent scheduler calls.
    INVARIANT: Every item is dispatched at most once, no duplicate DISPATCHED decisions.
    """
    store, queue, registry, scheduler = _setup_sqlite_harness(tmp_path)
    _register_workers(registry, num_workers=8)

    num_items = 20
    item_ids = []
    for i in range(num_items):
        enqueued = queue.enqueue(
            task_id=f"task_multi_{i % 3}",
            tenant_id=f"tenant_{i % 2}",
            required_capabilities=["python"],
            priority=5,
        )
        item_ids.append(enqueued.item_id)

    num_contenders = 15

    def _contender_run():
        return scheduler.schedule_cycle(limit=10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_contenders) as executor:
        futures = [executor.submit(_contender_run) for _ in range(num_contenders)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify each item has at most ONE DISPATCHED record
    for item_id in item_ids:
        decisions = [
            d for d in store.list_decisions(item_id=item_id)
            if d.verdict == SchedulingVerdict.DISPATCHED
        ]
        assert len(decisions) <= 1, (
            f"Item {item_id} had duplicate DISPATCHED decisions: {len(decisions)}"
        )


def test_stale_worker_fencing(tmp_path):
    """
    Verify that a worker cannot transition status (mark_running, mark_completed, mark_failed)
    if it is not the assigned owner (stale worker fencing).
    """
    store, queue, registry, scheduler = _setup_sqlite_harness(tmp_path)
    workers = _register_workers(registry, num_workers=2)
    legit_worker = workers[0]
    stale_worker = workers[1]

    enqueued = queue.enqueue(
        task_id="task_fencing_001",
        tenant_id="tenant_alpha",
        required_capabilities=["python"],
    )

    # Claim for legit_worker
    queue.mark_dispatching(enqueued.item_id, legit_worker.worker_id)
    queue.mark_claimed(enqueued.item_id, legit_worker.worker_id, "lease_legit_001")

    # Stale worker tries to mark running -> must fail
    with pytest.raises(Exception) as exc_info:
        queue.mark_running(enqueued.item_id, worker_id=stale_worker.worker_id)
    assert (
        "not authorized" in str(exc_info.value)
        or "fencing" in str(exc_info.value)
        or "CLAIMED" in str(exc_info.value)
        or "authorized" in str(exc_info.value)
    )

    # Legit worker marks running -> succeeds
    running_item = queue.mark_running(enqueued.item_id, worker_id=legit_worker.worker_id)
    assert running_item.status == QueueItemStatus.RUNNING

    # Stale worker tries to mark completed -> must fail
    with pytest.raises(Exception) as exc_info:
        queue.mark_completed(enqueued.item_id, worker_id=stale_worker.worker_id)
    assert (
        "not authorized" in str(exc_info.value)
        or "RUNNING" in str(exc_info.value)
        or "authorized" in str(exc_info.value)
    )

    # Legit worker marks completed -> succeeds
    completed_item = queue.mark_completed(enqueued.item_id, worker_id=legit_worker.worker_id)
    assert completed_item.status == QueueItemStatus.COMPLETED


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL not configured in environment",
)
def test_postgres_concurrent_scheduler_dispatch():
    """
    PostgreSQL-backed contention test verifying atomic CAS and zero duplicate DISPATCHED decisions.
    """
    dsn = os.environ["DATABASE_URL"]
    store = PostgresFabricStore(dsn=dsn)
    queue = DurableTaskQueue(store=store)
    registry = WorkerRegistry(store=store)
    scheduler = ExecutionScheduler(queue=queue, worker_registry=registry)

    _register_workers(registry, num_workers=5)
    test_task = f"task_pg_race_{uuid.uuid4().hex[:8]}"

    enqueued = queue.enqueue(
        task_id=test_task,
        tenant_id="tenant_pg_concurrency",
        required_capabilities=["python"],
        priority=10,
    )

    num_contenders = 8
    results = []

    def _run_tick():
        return scheduler.schedule_cycle(limit=5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_contenders) as executor:
        futures = [executor.submit(_run_tick) for _ in range(num_contenders)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    stored_decisions = store.list_decisions(task_id=test_task)
    dispatched = [d for d in stored_decisions if d.verdict == SchedulingVerdict.DISPATCHED]
    assert len(dispatched) == 1, (
        f"PostgreSQL concurrency test: expected exactly 1 DISPATCHED record, got {len(dispatched)}"
    )

    final_item = queue.get_item(enqueued.item_id)
    assert final_item.status == QueueItemStatus.CLAIMED
    assert final_item.assigned_worker_id == dispatched[0].assigned_worker_id
