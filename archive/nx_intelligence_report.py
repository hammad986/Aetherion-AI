#!/usr/bin/env python3
"""
nx_intelligence_report.py — Nexora Beta Readiness Audit V2
═══════════════════════════════════════════════════════════
Phase R: Operational intelligence reporting.
Analyzes sessions.db to generate real execution quality metrics.
"""
import sqlite3, os, sys, json

DB = 'sessions.db'
if not os.path.exists(DB):
    print('No sessions.db found. Run a task first.')
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print('Nexora Beta Readiness Audit V2')
print('=' * 46)

# 1. Operational Metrics
try:
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='completed'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='failed'").fetchone()[0]
    print(f'\n[Operational Metrics]')
    print(f'  Total Sessions: {total}')
    print(f'  Success Rate:   {round((success/total)*100,1)}%' if total else '  Success Rate:   N/A')
    print(f'  Failed:         {failed}')
except Exception as e: print('Error reading sessions:', e)

# 2. Execution Quality Metrics
try:
    retries = conn.execute("SELECT SUM(retry_count) FROM sessions").fetchone()[0] or 0
    print(f'\n[Execution Quality]')
    print(f'  Total Retries:  {retries}')
    if total:
        print(f'  Avg Retries:    {round(retries/total, 1)}')
    
    cats = conn.execute("SELECT error_category, COUNT(*) as n FROM sessions WHERE error_category IS NOT NULL GROUP BY error_category").fetchall()
    if cats:
        print('  Failure Causes:')
        for c in cats: print(f'    - {c["error_category"]}: {c["n"]}')
except Exception as e: print('Error reading quality metrics:', e)

# 3. Validation Gap Reports
try:
    val_fails = conn.execute("SELECT COUNT(*) FROM sessions WHERE validation LIKE '%fail%'").fetchone()[0]
    print(f'\n[Validation Reliability]')
    print(f'  Sessions with validation failures: {val_fails}')
except Exception as e: print('Error reading validation:', e)

print('\n[Honest Weaknesses]')
print('  1. Session memory (analytics, trust) is local-first, lost on hard refresh if not exported.')
print('  2. Execution confidence is heuristic, not strictly semantic.')
print('  3. No cross-session failure learning (agent repeats mistakes on new sessions).')
print('  4. Analytics are not aggregated centrally.')

print('\n' + '=' * 46)
conn.close()
