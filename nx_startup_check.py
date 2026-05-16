#!/usr/bin/env python3
"""
nx_startup_check.py — Nexora Deployment Readiness Validator
════════════════════════════════════════════════════════════
Checks environment, dependencies, and config before server start.
Run this before gunicorn to catch misconfiguration early.

Usage:
    python nx_startup_check.py
    python nx_startup_check.py --strict   # exit 1 on any WARNING
"""
import os, sys, importlib.util, argparse

OK   = '\033[32m[OK]\033[0m  '
WARN = '\033[33m[!!]\033[0m  '
ERR  = '\033[31m[XX]\033[0m  '
INFO = '\033[36m[--]\033[0m  '

issues = []
warnings = []

def ok(msg):   print(f'  {OK}{msg}')
def warn(msg): print(f'  {WARN}{msg}'); warnings.append(msg)
def err(msg):  print(f'  {ERR}{msg}');  issues.append(msg)
def info(msg): print(f'  {INFO}{msg}')

print('\nNexora Deployment Readiness Check')
print('=' * 46)

# ── 1. Python version ────────────────────────────────────────────────────────
print('\n[Python]')
v = sys.version_info
if v >= (3, 10):
    ok(f'Python {v.major}.{v.minor}.{v.micro}')
else:
    err(f'Python {v.major}.{v.minor} — requires 3.10+')

# ── 2. Critical imports ──────────────────────────────────────────────────────
print('\n[Dependencies]')
REQUIRED = ['flask', 'jwt', 'bcrypt', 'dotenv', 'requests']
OPTIONAL = ['redis', 'eventlet', 'gevent']
for mod in REQUIRED:
    real = 'python_dotenv' if mod == 'dotenv' else ('PyJWT' if mod == 'jwt' else mod)
    found = importlib.util.find_spec(mod) is not None
    if found: ok(mod)
    else:     err(f'{mod} not installed — run: pip install {real}')

for mod in OPTIONAL:
    found = importlib.util.find_spec(mod) is not None
    if found: ok(f'{mod} (optional)')
    else:     info(f'{mod} not installed (optional — single-worker mode)')

# ── 3. Environment variables ──────────────────────────────────────────────────
print('\n[Environment]')
from dotenv import load_dotenv
load_dotenv()

REQUIRED_VARS = {
    'JWT_SECRET':       ('SECURITY',  'Set to a 64-char random hex string'),
    'OPENAI_API_KEY':   ('AI',        'At least one AI provider key required'),
}
OPTIONAL_VARS = {
    'SESSION_SECRET':    'Flask session security',
    'GOOGLE_CLIENT_ID':  'Google OAuth',
    'ANTHROPIC_API_KEY': 'Anthropic provider',
    'REDIS_URL':         'Multi-worker SSE (leave blank for single-worker)',
    'GEMINI_API_KEY':    'Gemini provider',
}
_DEFAULT_JWT = 'nexora_saas_secret_key_change_in_production'

for var, (group, hint) in REQUIRED_VARS.items():
    val = os.environ.get(var, '')
    if not val:
        err(f'{var} not set — {hint}')
    elif var == 'JWT_SECRET' and val == _DEFAULT_JWT:
        warn(f'{var} is using the default insecure value — set a real secret')
    else:
        ok(f'{var} = {"*" * min(len(val), 6)}…')

# Check at least one AI key
ai_keys = ['OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY','GROQ_API_KEY','DEEPSEEK_API_KEY']
has_ai = any(os.environ.get(k) for k in ai_keys)
if has_ai:
    active = [k for k in ai_keys if os.environ.get(k)]
    ok(f'AI keys: {", ".join(active)}')
else:
    err('No AI provider keys found — agent cannot function')

for var, hint in OPTIONAL_VARS.items():
    val = os.environ.get(var, '')
    if val:
        ok(f'{var} set ({hint})')
    else:
        info(f'{var} not set ({hint})')

# ── 4. Critical files ────────────────────────────────────────────────────────
print('\n[Files]')
CRITICAL_FILES = [
    'web_app.py', 'auth_system.py', 'gunicorn.conf.py',
    'templates/index.html', 'static/css/nx-shell.css',
    'static/js/nx-bus.js', 'static/js/nx-sse-runtime.js',
    'static/js/nx-diagnostics.js',
]
WARN_FILES = ['.env', 'requirements.txt']
for f in CRITICAL_FILES:
    if os.path.exists(f):
        size = os.path.getsize(f)
        ok(f'{f} ({size//1024}KB)')
    else:
        err(f'{f} MISSING')
for f in WARN_FILES:
    if os.path.exists(f): ok(f)
    else: warn(f'{f} not found')

# ── 5. Database writability ───────────────────────────────────────────────────
print('\n[Database]')
import sqlite3
try:
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE _test(x)')
    c.close()
    ok('SQLite available')
except Exception as e:
    err(f'SQLite error: {e}')

db_path = os.environ.get('DB_PATH', 'saas_platform.db')
if os.path.exists(db_path):
    ok(f'{db_path} exists ({os.path.getsize(db_path)//1024}KB)')
else:
    info(f'{db_path} will be created on first run')

# ── 6. Port availability ─────────────────────────────────────────────────────
print('\n[Network]')
import socket
port = int(os.environ.get('PORT', '5000'))
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    result = s.connect_ex(('127.0.0.1', port))
    s.close()
    if result == 0:
        err(f'Port {port} collision — port is already in use')
    else:
        ok(f'Port {port} available')
except Exception:
    info(f'Could not check port {port}')

# ── 7. External Infrastructure ─────────────────────────────────────────────────
print('\n[Infrastructure]')
redis_url = os.environ.get('REDIS_URL')
if redis_url:
    try:
        import redis
        r = redis.from_url(redis_url, socket_connect_timeout=2)
        r.ping()
        ok('Redis connection successful')
    except ImportError:
        err('REDIS_URL is set but "redis" package is not installed')
    except Exception as e:
        err(f'Redis connection failed: {e}')
else:
    info('No REDIS_URL configured — running in single-worker SQLite mode')

# ── SUMMARY ───────────────────────────────────────────────────────────────────
print('\n' + '=' * 46)
if issues:
    print(f'\n{ERR} {len(issues)} critical issue(s) found:')
    for i in issues: print(f'  • {i}')
    print('\nFix these before starting the server.\n')
    sys.exit(1)
elif warnings:
    print(f'\n{WARN} {len(warnings)} warning(s):')
    for w in warnings: print(f'  • {w}')
    print('\nServer can start, but review warnings.\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('--strict', action='store_true')
    args, _ = parser.parse_known_args()
    if args.strict: sys.exit(1)
else:
    print(f'\n{OK} All deployment preflight checks passed.\n')
    print('  Run: gunicorn -c gunicorn.conf.py web_app:app\n')
