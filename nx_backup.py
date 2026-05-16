#!/usr/bin/env python3
"""
nx_backup.py — Nexora Automated Backup Utility
══════════════════════════════════════════════════════════════════════
Phase V: Creates timestamped backups of operational SQLite databases.
Safe to run while server is active (uses sqlite3 backup API).
"""
import sqlite3, os, sys, time, shutil
from datetime import datetime

BACKUP_DIR = 'backups'
DATABASES = ['saas_platform.db', 'sessions.db']

silent = '--silent' in sys.argv

if not silent:
    print('Nexora Operational Backup Utility')
    print('=' * 40)

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

ts = datetime.now().strftime('%Y%m%d_%H%M%S')
success = 0

for db in DATABASES:
    if not os.path.exists(db):
        if not silent: print(f'  [--] Skipping {db} (not found)')
        continue
    
    backup_file = os.path.join(BACKUP_DIR, f"{db.replace('.db', '')}_{ts}.db")
    
    try:
        # Use sqlite3 native backup for atomic snapshot while active
        src = sqlite3.connect(db)
        dst = sqlite3.connect(backup_file)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        
        size_kb = os.path.getsize(backup_file) // 1024
        if not silent: print(f'  [OK] Backed up {db} -> {backup_file} ({size_kb} KB)')
        success += 1
    except Exception as e:
        if not silent: print(f'  [XX] Failed to back up {db}: {e}')

if not silent:
    print('-' * 40)
    print(f'Backup complete: {success} files saved to /{BACKUP_DIR}')
