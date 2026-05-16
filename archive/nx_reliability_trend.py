#!/usr/bin/env python3
"""
nx_reliability_trend.py — Nexora Controlled Beta Reliability Tracker
══════════════════════════════════════════════════════════════════════
Phase T: Evaluates regressions, workflow stability, and operator burden
trends over time across real beta sessions.
"""
import sqlite3, os, sys, json
from datetime import datetime, timedelta

DB = 'sessions.db'
if not os.path.exists(DB):
    print('No sessions.db found. Run a task first.')
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print('Nexora Reliability & Regression Tracker (Phase T)')
print('=' * 56)

# Fetch sessions ordered by time
sessions = conn.execute("""
    SELECT id, status, validation, error_category, retry_count, created_at 
    FROM sessions ORDER BY created_at ASC
""").fetchall()

if len(sessions) < 2:
    print('\nInsufficient session history for trend tracking. Run more benchmarks.')
    sys.exit(0)

# Split into halves (Historical vs Recent)
mid = len(sessions) // 2
hist_sess = sessions[:mid]
recent_sess = sessions[mid:]

def calc_metrics(sess_list):
    total = len(sess_list)
    success = sum(1 for s in sess_list if s['status'] == 'completed')
    solved = sum(1 for s in sess_list if s['status'] == 'completed' and 'fail' not in str(s['validation']).lower() and not s['error_category'])
    retries = sum(s['retry_count'] for s in sess_list if s['retry_count'])
    errors = sum(1 for s in sess_list if s['error_category'])
    
    return {
        'total': total,
        'success_rate': (success / total * 100) if total else 0,
        'solve_rate': (solved / total * 100) if total else 0,
        'avg_retries': (retries / total) if total else 0,
        'error_rate': (errors / total * 100) if total else 0
    }

hist = calc_metrics(hist_sess)
rec = calc_metrics(recent_sess)

def trend(h, r, lower_is_better=False):
    delta = r - h
    if delta == 0: return ' 0.0 (Stable)'
    
    better = (delta < 0) if lower_is_better else (delta > 0)
    color = '\033[32m' if better else '\033[31m'
    sign = '+' if delta > 0 else ''
    return f'{color}{sign}{delta:.1f}\033[0m'

print(f'\n[Regression Trend Analysis]')
print(f'  Comparing First {hist["total"]} sessions vs Last {rec["total"]} sessions:')
print(f'  --------------------------------------------------')
print(f'  Semantic Solve Rate: {hist["solve_rate"]:.1f}% -> {rec["solve_rate"]:.1f}%  [{trend(hist["solve_rate"], rec["solve_rate"])}]')
print(f'  Reported Success:    {hist["success_rate"]:.1f}% -> {rec["success_rate"]:.1f}%  [{trend(hist["success_rate"], rec["success_rate"])}]')
print(f'  Average Retries:     {hist["avg_retries"]:.1f}   -> {rec["avg_retries"]:.1f}   [{trend(hist["avg_retries"], rec["avg_retries"], True)}]')
print(f'  Terminal Error Rate: {hist["error_rate"]:.1f}% -> {rec["error_rate"]:.1f}%  [{trend(hist["error_rate"], rec["error_rate"], True)}]')

# Check operator burden trend
try:
    hitls = conn.execute("SELECT ts FROM hitl_requests ORDER BY ts ASC").fetchall()
    if hitls:
        h_hist = len([h for h in hitls[:len(hitls)//2]])
        h_rec = len([h for h in hitls[len(hitls)//2:]])
        print(f'  HITL Escalations:    {h_hist}   -> {h_rec}   [{trend(h_hist, h_rec, True)}]')
except Exception as e:
    pass

print('\n[Flaky Workflow Detection]')
try:
    cats = conn.execute("""
        SELECT error_category, COUNT(*) as n 
        FROM sessions 
        WHERE error_category IS NOT NULL 
        GROUP BY error_category 
        ORDER BY n DESC
    """).fetchall()
    if cats:
        for c in cats:
            count = c["n"]
            marker = "⚠️ CHRONIC" if count >= 3 else ""
            print(f'  - {c["error_category"]}: {count} {marker}')
    else:
        print('  No repeated systemic errors detected.')
except Exception as e:
    pass

print('\n[Beta Phase Conclusion]')
if rec['solve_rate'] < hist['solve_rate']:
    print('  [!] ALERT: Platform is degrading. Semantic solve rate has fallen.')
elif rec['avg_retries'] > hist['avg_retries'] + 2:
    print('  [!] ALERT: Platform is thrashing. Average retries have spiked.')
else:
    print('  [OK] PLATFORM STABLE: Metrics are holding or improving.')

print('\n' + '=' * 56)
conn.close()
