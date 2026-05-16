#!/usr/bin/env python3
"""
nx_failure_taxonomy.py — Nexora Real Failure Taxonomy
══════════════════════════════════════════════════════════════════════
Phase U: Clusters failures into Deployment, Runtime, Operator, and
Mission categories to identify operational weak points.
"""
import sqlite3, os, sys, json
from collections import defaultdict

DB = 'sessions.db'
if not os.path.exists(DB):
    print('No sessions.db found. Run a task first.')
    sys.exit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print('Nexora Real Failure Taxonomy (Phase U)')
print('=' * 56)

# Categories
taxonomy = {
    'Deployment/Infra': defaultdict(int),
    'Runtime/Agent': defaultdict(int),
    'Operator/Workflow': defaultdict(int),
    'Mission/Semantic': defaultdict(int)
}

# 1. Mission / Semantic Failures
sessions = conn.execute("SELECT error_category, validation FROM sessions").fetchall()
for s in sessions:
    err = s['error_category']
    val = s['validation']
    if err:
        if 'connection' in err.lower() or 'timeout' in err.lower() or 'redis' in err.lower():
            taxonomy['Deployment/Infra'][err] += 1
        elif 'auth' in err.lower() or 'permission' in err.lower():
            taxonomy['Operator/Workflow'][err] += 1
        elif 'syntax' in err.lower() or 'not found' in err.lower():
            taxonomy['Mission/Semantic'][err] += 1
        else:
            taxonomy['Runtime/Agent'][err] += 1
    if val and 'fail' in val.lower():
        taxonomy['Mission/Semantic']['Validation Failed'] += 1

# 2. Operator / Workflow Failures
try:
    overrides = conn.execute("SELECT action FROM hitl_audit WHERE action='reject'").fetchall()
    if overrides:
        taxonomy['Operator/Workflow']['HITL Reject (Trust Override)'] += len(overrides)
except:
    pass

# Print Report
total_issues = 0
for category, items in taxonomy.items():
    print(f'\n[{category}]')
    if items:
        for k, v in sorted(items.items(), key=lambda x: -x[1]):
            print(f'  - {k}: {v}')
            total_issues += v
    else:
        print('  No recorded failures.')

print('\n[Infrastructure Bottleneck Detection]')
if taxonomy['Deployment/Infra']:
    print('  [!] Deployment failures detected. Check reverse proxy timeouts, network, or Redis config.')
else:
    print('  [OK] No major infrastructure bottlenecks detected in execution logs.')

print('\n' + '=' * 56)
conn.close()
