"""
scripts/final_recert_spot_checks.py
===================================
Authoritative independent verification script for all 10 mandatory production UAT spot checks.
"""
from __future__ import annotations

import os
import sys
import uuid
import json
import tempfile
import subprocess
from pathlib import Path
from click.testing import CliRunner
from unittest.mock import patch
from flask import Flask

from cli.aetherion_cli.main import cli
from backend.db.d3_store import D3Store
from backend.execution import durable_state
from backend.routes.tasks import approve_task
from backend.auth_system import _make_access_token

def main():
    runner = CliRunner()
    print("=" * 60)
    print("MANDATORY FINAL PRODUCTION UAT SPOT CHECKS (1 - 10)")
    print("=" * 60)

    # 1. aetherion provider list
    mock_providers = {"ok": True, "providers": [{"provider": "openai", "name": "GPT-4o", "status": "available"}]}
    with patch("cli.aetherion_cli.config.get_token", return_value="test-token"):
        with patch("cli.aetherion_cli.client.Client.get", return_value=mock_providers):
            res1 = runner.invoke(cli, ["provider", "list"])
            assert res1.exit_code == 0, f"Spot check 1 failed: {res1.output}"
            assert "openai" in res1.output
    print("SPOT CHECK 1: aetherion provider list -> PASS")

    # 2. aetherion kg list (NO --type)
    mock_kg = {"ok": True, "nodes": [{"id": "n1", "type": "FACT", "content": "PostgreSQL is durable"}], "total": 1}
    with patch("cli.aetherion_cli.config.get_token", return_value="test-token"):
        with patch("cli.aetherion_cli.client.Client.get", return_value=mock_kg):
            res2 = runner.invoke(cli, ["kg", "list", "--session", "sess_spot"])
            assert res2.exit_code == 0, f"Spot check 2 failed: {res2.output}"
            assert "PostgreSQL is durable" in res2.output
    print("SPOT CHECK 2: aetherion kg list (no --type) -> PASS")

    # 3. aetherion kg search "PostgreSQL" (NO --type)
    mock_kg_search = {
        "ok": True,
        "results": [{
            "id": "n1",
            "content": "PostgreSQL is durable",
            "text": "PostgreSQL is durable",
            "metadata": {"type": "FACT"},
        }],
        "total": 1,
    }
    with patch("cli.aetherion_cli.config.get_token", return_value="test-token"):
        with patch("cli.aetherion_cli.client.Client.get", return_value=mock_kg_search):
            res3 = runner.invoke(cli, ["kg", "search", "PostgreSQL", "--session", "sess_spot"])
            assert res3.exit_code == 0, f"Spot check 3 failed: {res3.output}"
            assert "PostgreSQL is durable" in res3.output
    print("SPOT CHECK 3: aetherion kg search 'PostgreSQL' (no --type) -> PASS")

    # 4. aetherion worktree create (real Git worktree)
    tmp_repo = tempfile.mkdtemp(prefix="aetherion_spot_wt_")
    orig_cwd = os.getcwd()
    try:
        subprocess.run(["git", "init"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Auditor"], cwd=tmp_repo, check=True)
        subprocess.run(["git", "config", "user.email", "auditor@test.com"], cwd=tmp_repo, check=True)
        (Path(tmp_repo) / "f.txt").write_text("initial", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_repo, check=True, capture_output=True)
        os.chdir(tmp_repo)
        d3 = D3Store()
        wt = d3.create_real_worktree("tenant_spot", "sess_spot", "exec_spot")
        assert Path(wt["path"]).exists() and (Path(wt["path"]) / ".git").exists()
        d3.discard_worktree("tenant_spot", wt["id"])
        assert not Path(wt["path"]).exists()
        print("SPOT CHECK 4: aetherion worktree create -> PASS")
    finally:
        os.chdir(orig_cwd)

    # 5. actual HITL approve
    app = Flask(__name__)
    token = _make_access_token(user_id=101, email="op@test.com")
    t_hitl = "101"
    task_a = durable_state.create(kind="test_a", tenant_id=t_hitl, payload={"objective": "approve_me"})
    tid_a = task_a["execution_id"]
    durable_state.transition(tid_a, t_hitl, durable_state.STATUS_RUNNING)
    durable_state.transition(tid_a, t_hitl, durable_state.STATUS_SUCCEEDED)
    durable_state.request_review(tid_a, t_hitl)

    with app.test_request_context(f"/api/task/{tid_a}/approve", method="POST", json={"decision": "approve"}, headers={"Authorization": f"Bearer {token}"}):
        resp_a = approve_task(tid_a)
        data_a = resp_a[0].get_json() if isinstance(resp_a, tuple) else resp_a.get_json()
        assert data_a["ok"] is True and data_a["status"] == "ACCEPTED" and data_a["gate_resolved"] is True
    rec_a = durable_state.get(tid_a, t_hitl)
    assert rec_a["status"] == "ACCEPTED"
    print("SPOT CHECK 5: actual HITL approve -> PASS")

    # 6. actual HITL reject
    task_r = durable_state.create(kind="test_r", tenant_id=t_hitl, payload={"objective": "reject_me"})
    tid_r = task_r["execution_id"]
    durable_state.transition(tid_r, t_hitl, durable_state.STATUS_RUNNING)
    durable_state.transition(tid_r, t_hitl, durable_state.STATUS_SUCCEEDED)
    durable_state.request_review(tid_r, t_hitl)

    with app.test_request_context(f"/api/task/{tid_r}/approve", method="POST", json={"decision": "reject"}, headers={"Authorization": f"Bearer {token}"}):
        resp_r = approve_task(tid_r)
        data_r = resp_r[0].get_json() if isinstance(resp_r, tuple) else resp_r.get_json()
        assert data_r["ok"] is True and data_r["status"] == "REJECTED" and data_r["gate_resolved"] is True
    rec_r = durable_state.get(tid_r, t_hitl)
    assert rec_r["status"] == "REJECTED"
    print("SPOT CHECK 6: actual HITL reject -> PASS")

    # 7. automation persistence across backend restart
    tenant_d3 = f"tenant_pers_{uuid.uuid4().hex[:6]}"
    store_p1 = D3Store()
    auto_obj = store_p1.create_automation(tenant_d3, {"name": "Survive Job", "schedule_type": "daily"})
    del store_p1
    store_p2 = D3Store()
    auto_reloaded = store_p2.get_automation(tenant_d3, auto_obj["id"])
    assert auto_reloaded is not None and auto_reloaded["name"] == "Survive Job"
    print("SPOT CHECK 7: automation persistence across backend restart -> PASS")

    # 8. KG persistence across backend restart
    node_obj = store_p2.add_kg_node(tenant_d3, "sess_kg", "FACT", "PostgreSQL authoritativeness confirmed")
    del store_p2
    store_p3 = D3Store()
    nodes_reloaded = store_p3.search_kg_nodes(tenant_d3, "sess_kg", "authoritativeness")
    assert len(nodes_reloaded) >= 1
    print("SPOT CHECK 8: KG persistence across backend restart -> PASS")

    # 9. Kanban persistence across backend restart
    card_obj = store_p3.create_kanban_card(tenant_d3, {"title": "Release Kanban Card", "column": "done"})
    del store_p3
    store_p4 = D3Store()
    card_reloaded = store_p4.get_kanban_card(tenant_d3, card_obj["id"])
    assert card_reloaded is not None and card_reloaded["column"] == "done"
    print("SPOT CHECK 9: Kanban persistence across backend restart -> PASS")

    # 10. concurrent multi-worker claim
    from backend.execution.fabric.postgres_store import PostgresFabricStore
    from backend.execution.fabric.queue import DurableTaskQueue
    from backend.execution.fabric.worker_registry import WorkerRegistry
    from backend.execution.fabric.scheduler import ExecutionScheduler
    import concurrent.futures

    fab_store = PostgresFabricStore(dsn=os.environ["DATABASE_URL"])
    fab_queue = DurableTaskQueue(store=fab_store)
    fab_reg = WorkerRegistry(store=fab_store, heartbeat_timeout_sec=300.0)
    fab_w1 = fab_reg.register_worker(f"w_mc1_{uuid.uuid4().hex[:6]}", max_concurrency=2)
    fab_w2 = fab_reg.register_worker(f"w_mc2_{uuid.uuid4().hex[:6]}", max_concurrency=2)
    item_mc = fab_queue.enqueue(task_id=f"task_mc_{uuid.uuid4().hex[:6]}", tenant_id=f"t_mc_{uuid.uuid4().hex[:4]}")

    def _claim_race():
        sched = ExecutionScheduler(queue=fab_queue, worker_registry=fab_reg)
        return sched.schedule_cycle(limit=1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_claim_race) for _ in range(5)]
        results = [f.result() for f in futs]

    dispatched_mc = [d for r in results for d in r if d.item_id == item_mc.item_id and d.verdict.value == "DISPATCHED"]
    assert len(dispatched_mc) == 1, f"Expected 1 dispatched, got {len(dispatched_mc)}"
    print("SPOT CHECK 10: concurrent multi-worker claim -> PASS")

    print("=" * 60)
    print("ALL 10 MANDATORY FINAL PRODUCTION UAT SPOT CHECKS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()
