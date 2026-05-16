#!/usr/bin/env python3
"""
nx_semantic_eval.py — Nexora Adaptive Reliability & Semantic Eval
════════════════════════════════════════════════════════════════
Phase S: Evaluates real execution quality and semantic outcomes.
Analyzes sessions.db and cross-references validation accuracy.
"""
import sqlite3, os, sys, json

DB = 'sessions.db'
if not os.path.exists(DB):
    print('No sessions.db found. Run a task first.')
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print('Nexora Semantic Evaluation & Calibration (Phase S)')
print('=' * 56)

# ── 1. SEMANTIC SUCCESS EVALUATION ─────────────────────────────────
try:
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    success_status = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='completed'").fetchone()[0]
    
    # Heuristic: Genuinely Solved = completed + NO validation fail + NO final error
    solved = conn.execute("""
        SELECT COUNT(*) FROM sessions 
        WHERE status='completed' 
        AND (validation NOT LIKE '%fail%' OR validation IS NULL)
        AND (error_category IS NULL)
    """).fetchone()[0]
    
    print(f'\n[Semantic Success]')
    print(f'  Tasks Executed:       {total}')
    print(f'  Reported "Completed": {success_status} ({(success_status/total*100) if total else 0:.1f}%)')
    print(f'  Genuinely Solved:     {solved} ({(solved/total*100) if total else 0:.1f}%)')
    if success_status > solved:
        print(f'  !! Hallucinated Success Delta: {success_status - solved} task(s)')
except Exception as e: print('Error reading sessions:', e)


# ── 2. CONFIDENCE CALIBRATION ──────────────────────────────────────
try:
    retries = conn.execute("SELECT SUM(retry_count) FROM sessions").fetchone()[0] or 0
    hitls = conn.execute("SELECT COUNT(*) FROM hitl_requests").fetchone()[0]
    overrides = conn.execute("SELECT COUNT(*) FROM hitl_audit WHERE action='reject'").fetchone()[0]
    
    print(f'\n[Confidence & Trust Calibration]')
    print(f'  Total Retries:        {retries}')
    print(f'  HITL Escalations:     {hitls}')
    print(f'  Operator Overrides:   {overrides}')
    
    # Calibration logic
    if overrides > 0:
        print('  Calibration: UNDER-TRUSTED (Operator routinely overrides agent decisions)')
    elif hitls > 0 and overrides == 0:
        print('  Calibration: WELL-CALIBRATED (Escalations occur and are approved)')
    elif hitls == 0 and success_status > solved:
        print('  Calibration: OVERCONFIDENT (Agent fails silently without escalating)')
    else:
        print('  Calibration: INSUFFICIENT DATA')
except Exception as e: print('Error reading calibration metrics:', e)


# ── 3. CROSS-SESSION RELIABILITY ───────────────────────────────────
try:
    cats = conn.execute("SELECT error_category, COUNT(*) as n FROM sessions WHERE error_category IS NOT NULL GROUP BY error_category ORDER BY n DESC").fetchall()
    print(f'\n[Regression & Failure Memory]')
    if cats:
        for c in cats: 
            count = c["n"]
            marker = "⚠️ FLAKY" if count >= 3 else ""
            print(f'  - {c["error_category"]}: {count} {marker}')
    else:
        print('  No repeated systemic errors detected.')
except Exception as e: print('Error reading failures:', e)

print('\n' + '=' * 56)
conn.close()
