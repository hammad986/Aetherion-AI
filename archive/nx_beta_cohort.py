#!/usr/bin/env python3
"""
nx_beta_cohort.py — Nexora Beta Field Test Analytics
══════════════════════════════════════════════════════════════════════
Phase V: Analyzes resource economics, long-horizon session tracking,
operator trust rejection rates, and performance discipline across the cohort.
"""
import sqlite3, os, sys, json
from datetime import datetime

DB = 'sessions.db'
if not os.path.exists(DB):
    print('No sessions.db found. Run a task first.')
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print('Nexora Beta Cohort & Resource Profiler (Phase V)')
print('=' * 56)

# ── 1. RESOURCE ECONOMICS ──────────────────────────────────────────
try:
    print('\n[Resource Economics]')
    # In sessions table, usage_json holds the token usage
    usage_data = conn.execute("SELECT usage_json FROM sessions WHERE usage_json IS NOT NULL").fetchall()
    
    total_tokens = 0
    total_cost_est = 0.0 # Rough approx: $0.005 / 1k total tokens (blended input/output)
    
    for row in usage_data:
        try:
            u = json.loads(row['usage_json'])
            tokens = u.get('total_tokens', 0)
            total_tokens += tokens
        except: pass
        
    print(f'  Total Cohort Tokens:    {total_tokens:,}')
    print(f'  Estimated Cohort Cost:  ${(total_tokens / 1000) * 0.005:.4f}')
    
    # Calculate average duration
    durations = conn.execute("""
        SELECT (julianday(finished_at) - julianday(started_at)) * 86400 as sec
        FROM sessions WHERE finished_at IS NOT NULL AND started_at IS NOT NULL
    """).fetchall()
    
    valid_durations = [d['sec'] for d in durations if d['sec'] is not None]
    if valid_durations:
        avg_sec = sum(valid_durations) / len(valid_durations)
        print(f'  Avg Execution Time:     {avg_sec:.1f} seconds')
    else:
        print('  Avg Execution Time:     N/A (No finished sessions)')
except Exception as e: print('Error reading economics:', e)

# ── 2. LONG-HORIZON & BETA SESSION TRACKING ────────────────────────
try:
    print('\n[Session & Cohort Discipline]')
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    abandoned = conn.execute("SELECT COUNT(*) FROM sessions WHERE status NOT IN ('completed', 'failed')").fetchone()[0]
    
    print(f'  Total Active Sessions:  {total}')
    print(f'  Abandoned (Zombie):     {abandoned}')
    
    # Check max file json length (proxy for memory growth)
    max_mem = conn.execute("SELECT MAX(LENGTH(files_json)) FROM sessions").fetchone()[0] or 0
    print(f'  Max Session DB Size:    {max_mem / 1024:.1f} KB (Monaco State)')
except Exception as e: print('Error reading cohort tracking:', e)


# ── 3. HUMAN TRUST EVALUATION ──────────────────────────────────────
try:
    print('\n[Human Trust Evaluation]')
    hitls = conn.execute("SELECT COUNT(*) FROM hitl_requests").fetchone()[0]
    overrides = conn.execute("SELECT COUNT(*) FROM hitl_audit WHERE action='reject'").fetchone()[0]
    approvals = conn.execute("SELECT COUNT(*) FROM hitl_audit WHERE action='approve'").fetchone()[0]
    
    print(f'  Total Escalations:      {hitls}')
    print(f'  Operator Approvals:     {approvals}')
    print(f'  Operator Rejections:    {overrides}')
    
    if (approvals + overrides) > 0:
        trust_rate = approvals / (approvals + overrides) * 100
        print(f'  Trust Confidence Rate:  {trust_rate:.1f}%')
        if trust_rate < 50:
            print('  [!] ALERT: Trust decay detected. Operators are rejecting the agent more than approving.')
        else:
            print('  [OK] Positive trust vector.')
    else:
        print('  Trust Confidence Rate:  N/A (No HITL interactions)')
except Exception as e: print('Error reading trust:', e)

print('\n' + '=' * 56)
conn.close()
