import os
import sys
import uuid
import time
import subprocess
import psycopg2
import json

try:
    from backend.db.test_target_policy import get_test_database_url
except ImportError:
    from db.test_target_policy import get_test_database_url


def main():
    db_url = get_test_database_url()
    print("Starting HITL Durable Approval / Rejection Verification...")
    
    # Connect to PostgreSQL
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    
    tenant_id = "6" # devuser@example.com is user_id 6
    
    # -------------------------------------------------------------------
    # TEST 1: HITL APPROVAL
    # -------------------------------------------------------------------
    try:
        from execution import durable_state
    except ImportError:
        from backend.execution import durable_state
    
    # Ensure durable_state uses the validated test database target
    durable_state.configure_test_store(db_url)
    
    rec1 = durable_state.create(
        kind="agent_task",
        tenant_id=tenant_id,
        payload={"prompt": "Generate database migration script", "workspace_id": "ws_hitl_test"},
    )

    task_id_1 = rec1["execution_id"]
    # Valid transition chain: PENDING -> RUNNING -> SUCCEEDED -> REVIEW_PENDING
    durable_state.transition(task_id_1, tenant_id, durable_state.STATUS_RUNNING)
    durable_state.transition(task_id_1, tenant_id, durable_state.STATUS_SUCCEEDED)
    durable_state.transition(task_id_1, tenant_id, durable_state.STATUS_REVIEW_PENDING)
    
    st_init = durable_state.get(task_id_1, tenant_id)
    print(f"Task 1 created: id={task_id_1}, initial status={st_init['status']}")
    assert st_init["status"] == durable_state.STATUS_REVIEW_PENDING, f"Expected {durable_state.STATUS_REVIEW_PENDING}, got {st_init['status']}"
    
    # Execute CLI command: task approve <task_id_1>
    cmd_approve = [
        sys.executable, "-m", "cli.aetherion_cli.main", "task", "approve", task_id_1,
        "--comment", "Approved by Independent Production Auditor", "--json"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = ".;backend;cli"
    r1 = subprocess.run(cmd_approve, capture_output=True, text=True, env=env)
    print(f"CLI approve output: {r1.stdout.strip()}")
    assert r1.returncode == 0, f"CLI approve failed: {r1.stderr}"
    
    raw_app = json.loads(r1.stdout)
    app_data = raw_app.get("data") or raw_app
    assert app_data.get("ok") is True, f"Approve failed in response: {app_data}"
    assert app_data.get("gate_resolved") is True, f"gate_resolved is not true: {app_data}"
    assert app_data.get("status") == durable_state.STATUS_ACCEPTED, f"Status not ACCEPTED: {app_data}"
    print("CLI approve response: gate_resolved=True, status=ACCEPTED: PASS")
    
    # Verify in PostgreSQL directly
    st_post = durable_state.get(task_id_1, tenant_id)
    print(f"PostgreSQL durable state for {task_id_1}: status={st_post['status']}")
    assert st_post["status"] == durable_state.STATUS_ACCEPTED, f"Expected status ACCEPTED in DB, got {st_post['status']}"
    
    res_obj = json.loads(st_post.get("result_json") or "{}")
    review_dec = res_obj.get("review_decision", {})
    assert review_dec.get("decision") == "accept", f"Decision not accept: {review_dec}"
    assert review_dec.get("comment") == "Approved by Independent Production Auditor", f"Comment not match: {review_dec}"
    assert review_dec.get("approver") == "6", f"Approver not user 6: {review_dec}"
    print("Durable PostgreSQL Approval Evidence: VERIFIED (decision=accept, approver=6, comment persisted)")

    # -------------------------------------------------------------------
    # TEST 2: HITL REJECTION
    # -------------------------------------------------------------------
    rec2 = durable_state.create(
        kind="agent_task",
        tenant_id=tenant_id,
        payload={"prompt": "Execute drop table production", "workspace_id": "ws_hitl_test"},
    )
    task_id_2 = rec2["execution_id"]
    durable_state.transition(task_id_2, tenant_id, durable_state.STATUS_RUNNING)
    durable_state.transition(task_id_2, tenant_id, durable_state.STATUS_SUCCEEDED)
    durable_state.transition(task_id_2, tenant_id, durable_state.STATUS_REVIEW_PENDING)
    
    st_init_2 = durable_state.get(task_id_2, tenant_id)
    assert st_init_2["status"] == durable_state.STATUS_REVIEW_PENDING
    
    cmd_reject = [
        sys.executable, "-m", "cli.aetherion_cli.main", "task", "approve", task_id_2,
        "--reject", "--comment", "Destructive schema modification rejected", "--json"
    ]
    r2 = subprocess.run(cmd_reject, capture_output=True, text=True, env=env)
    print(f"CLI reject output: {r2.stdout.strip()}")
    assert r2.returncode == 0, f"CLI reject failed: {r2.stderr}"
    
    raw_rej = json.loads(r2.stdout)
    rej_data = raw_rej.get("data") or raw_rej
    assert rej_data.get("ok") is True, f"Reject failed in response: {rej_data}"
    assert rej_data.get("gate_resolved") is True, f"gate_resolved is not true: {rej_data}"
    assert rej_data.get("status") == durable_state.STATUS_REJECTED, f"Status not REJECTED: {rej_data}"
    print("CLI reject response: gate_resolved=True, status=REJECTED: PASS")
    
    st_post_2 = durable_state.get(task_id_2, tenant_id)
    print(f"PostgreSQL durable state for {task_id_2}: status={st_post_2['status']}")
    assert st_post_2["status"] == durable_state.STATUS_REJECTED, f"Expected status REJECTED in DB, got {st_post_2['status']}"
    
    res_obj_2 = json.loads(st_post_2.get("result_json") or "{}")
    review_dec_2 = res_obj_2.get("review_decision", {})
    assert review_dec_2.get("decision") == "reject", f"Decision not reject: {review_dec_2}"
    assert review_dec_2.get("comment") == "Destructive schema modification rejected", f"Comment not match: {review_dec_2}"
    assert review_dec_2.get("reviewer") == "6", f"Reviewer not user 6: {review_dec_2}"
    print("Durable PostgreSQL Rejection Evidence: VERIFIED (decision=reject, reviewer=6, comment persisted)")

    conn.close()
    print("\n*** HITL DURABLE APPROVAL / REJECTION FULLY CERTIFIED ***")

if __name__ == "__main__":
    main()
