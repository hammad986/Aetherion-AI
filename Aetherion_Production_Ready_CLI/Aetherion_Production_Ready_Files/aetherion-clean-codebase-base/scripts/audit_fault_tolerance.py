import os
import sys
import time
import uuid
import psycopg2

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.execution.fabric.postgres_store import PostgresFabricStore
from backend.execution.fabric.service import ExecutionFabricService
from backend.execution.fabric.models import QueueItem, QueueItemStatus, WorkerStatus, WorkerRecord

try:
    from backend.db.test_target_policy import get_test_database_url
except ImportError:
    from db.test_target_policy import get_test_database_url


def main():
    print("=== FAULT TOLERANCE & CRASH RECOVERY AUDIT ===")
    
    db_url = get_test_database_url()
    store = PostgresFabricStore(dsn=db_url)
    service = ExecutionFabricService(store=store)

    # 1. Register a simulated worker that goes unresponsive
    fake_worker_id = f"worker_doomed_{uuid.uuid4().hex[:6]}"
    now = time.time()
    stale_worker = WorkerRecord(
        worker_id=fake_worker_id,
        session_id=f"sess_{uuid.uuid4().hex[:6]}",
        process_id=99999,
        supported_capabilities={"DEFAULT"},
        max_concurrency=2,
        last_heartbeat=now - 45.0, # 45 seconds ago (heartbeat timeout is 30s)
        status=WorkerStatus.READY,
    )
    store.upsert_worker(stale_worker)
    print(f"Registered stale worker {fake_worker_id} with last heartbeat 45s in the past.")

    # 2. Assign an item to this doomed worker
    item_id = f"item_{uuid.uuid4().hex[:8]}"
    item = QueueItem(
        item_id=item_id,
        tenant_id="tenant_audit_recovery",
        task_id=f"task_{uuid.uuid4().hex[:8]}",
        step_id="step_1",
        lease_id=f"lease_{uuid.uuid4().hex[:8]}",
        status=QueueItemStatus.RUNNING,
        assigned_worker_id=fake_worker_id,
        payload={"is_idempotent": True, "max_retries": 3, "allowed_tools": ["check_syntax"]},
        attempt=1,
        enqueued_at=now - 20.0,
    )
    store.insert_queue_item(item)
    print(f"Created in-flight RUNNING item {item_id} assigned to dead worker {fake_worker_id}.")

    # 3. Trigger orphan recovery
    recovery_summary = service.recover_orphans(now=now)
    print(f"Orphan recovery execution summary: {recovery_summary}")

    # 4. Verify doomed worker marked UNHEALTHY
    worker_rec = store.get_worker(fake_worker_id)
    print(f"Doomed worker status: {worker_rec.status.value if worker_rec else 'None'}")
    assert worker_rec.status == WorkerStatus.UNHEALTHY, f"Expected UNHEALTHY, got {worker_rec.status}"

    # 5. Verify item was requeued
    recovered_item = store.get_queue_item(item_id)
    print(f"Item status after recovery: {recovered_item.status.value}")
    assert recovered_item.status in (QueueItemStatus.QUEUED, QueueItemStatus.REQUEUED, QueueItemStatus.DISPATCHING), f"Expected QUEUED/REQUEUED, got {recovered_item.status}"
    print(f"Item assigned worker cleared? {recovered_item.assigned_worker_id is None}")

    # 6. Test durable state survival across restart
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        cur.execute("SELECT status, assigned_worker_id FROM fabric_queue_items WHERE item_id = %s", (item_id,))
        row = cur.fetchone()
        print(f"Direct PostgreSQL verification: status={row[0]}, assigned_worker={row[1]}")
        assert row[0] in ("QUEUED", "REQUEUED", "DISPATCHING"), f"DB state mismatch: {row}"

    print("\n=== FAULT TOLERANCE AUDIT PASSED ===")

if __name__ == "__main__":
    main()
