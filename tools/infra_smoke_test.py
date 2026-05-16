"""infra_smoke_test.py — run: python infra_smoke_test.py"""
import sys, os, time, threading

def main():
    print("=== PHASE 1: Module Import ===")
    from infra.db_adapter import get_db
    from infra.event_bus import get_event_bus
    from infra.tenant import global_tenant_registry, get_tenant, TenantTier, QuotaExceededError
    from infra.telemetry import get_telemetry
    from infra.resilience import global_degraded_mode, global_recovery_playbook
    print("[OK] All infra modules imported")

    print("\n=== PHASE 2: Database Adapter ===")
    db = get_db("test_smoke.db")
    db.execute("CREATE TABLE IF NOT EXISTS _smoke (k TEXT, v TEXT)")
    db.execute("INSERT OR REPLACE INTO _smoke VALUES (?, ?)", ("test_key", "test_val"))
    row = db.fetchone("SELECT v FROM _smoke WHERE k=?", ("test_key",))
    assert row and row.get("v") == "test_val", f"Got: {row}"
    hc = db.health_check()
    print(f"[OK] DB adapter: backend={db.backend()} ok={hc['ok']}")

    print("\n=== PHASE 3: Advisory Lock (SQLite) ===")
    results = []
    def _locker(n):
        with db.advisory_lock(99901, timeout_ms=3000):
            results.append(n)
            time.sleep(0.02)
    threads = [threading.Thread(target=_locker, args=(i,)) for i in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 3
    print(f"[OK] Advisory locks serialized: order={results}")

    print("\n=== PHASE 4: Event Bus ===")
    bus = get_event_bus()
    received = []
    def _on_event(ch, et, pl):
        received.append((ch, et))
    bus.subscribe("nexora:smoketest", _on_event)
    eid = bus.publish("nexora:smoketest", "smoke_test", {"hello": "world"})
    time.sleep(0.05)
    assert any(ch == "nexora:smoketest" for ch, _ in received), f"Got: {received}"
    h = bus.health()
    print(f"[OK] Event bus: backend={h['backend']} event_id={eid}")

    print("\n=== PHASE 5: Tenant Isolation ===")
    ctx = get_tenant("sess_smoke_001", tenant_id="tenant_smoke", tier=TenantTier.FREE)
    ws = ctx.ensure_workspace()
    print(f"[OK] Tenant: id={ctx.tenant_id} tier={ctx.tier} workspace={ws}")

    try:
        bad = ctx.workspace_path("../../etc/passwd")
        print("[FAIL] Path traversal NOT blocked — critical bug!")
        sys.exit(1)
    except PermissionError as e:
        print(f"[OK] Path traversal blocked")

    ctx2 = get_tenant("sess_quota_01", tenant_id="tenant_quota", tier=TenantTier.FREE)
    try:
        ctx2.check_and_charge("tokens_per_day", 49999)
        ctx2.check_and_charge("tokens_per_day", 2)
        print("[FAIL] Quota should have been enforced!")
        sys.exit(1)
    except QuotaExceededError as e:
        print(f"[OK] Quota enforced: {str(e)[:60]}")

    print("\n=== PHASE 6: Telemetry ===")
    tel = get_telemetry()
    tel.record_tokens(1000, session_id="sess_smoke_001")
    tel.record_retry_storm("sess_smoke_001", role="coding")
    tel.record_hitl("sess_smoke_001", "clarification")
    snap = tel.snapshot()
    assert snap["counters"]["tokens_total"] >= 1000
    assert snap["counters"]["retry_storms"] >= 1
    assert snap["counters"]["hitl_triggers"] >= 1
    print(f"[OK] Telemetry counters correct")
    prom = tel.export_prometheus()
    assert "nexora_tokens_total" in prom
    print(f"[OK] Prometheus export: {len(prom)} chars")

    print("\n=== PHASE 7: Degraded Mode & Resilience ===")
    global_degraded_mode.mark_degraded("redis", "Smoke test")
    assert global_degraded_mode.is_degraded("redis")
    limits = global_degraded_mode.effective_limits()
    print(f"[OK] Degraded mode active: max_browsers={limits['max_browsers']}")
    global_degraded_mode.mark_recovered("redis")
    assert not global_degraded_mode.is_degraded("redis")
    print("[OK] Recovery registered")
    released = global_recovery_playbook.handle_stale_lock_orphan("dead_sess_xyz")
    print(f"[OK] Stale lock cleanup: released={released}")

    print("\n=== PHASE 8: Cross-session contamination prevention ===")
    global_tenant_registry.register_session("tenant_A", "session_AA1")
    global_tenant_registry.register_session("tenant_B", "session_BB1")
    try:
        global_tenant_registry.assert_session_isolation("session_AA1", "session_BB1")
        print("[FAIL] Cross-tenant access should have been blocked!")
        sys.exit(1)
    except PermissionError:
        print("[OK] Cross-tenant isolation enforced")

    print("\n" + "=" * 50)
    print("ALL INFRASTRUCTURE SMOKE TESTS PASSED")
    print("=" * 50)

if __name__ == "__main__":
    main()
