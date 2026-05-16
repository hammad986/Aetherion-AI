"""
routes/session_routes.py — Session Domain Blueprint
Phase Z3 Modularization | Architecture LOCKED

INTENTIONAL DESIGN NOTE (Phase Z5 — Import Discipline Audit):
─────────────────────────────────────────────────────────────
The `from runtime.state import *` and `globals().update(vars(web_app))` patterns
below are DELIBERATE architectural decisions from Phase Z3, not accidental wildcards.

Rationale:
  1. `from runtime.state import *` — imports all shared locks, queues, and registries
     (queue_lock, pending_queue, _db_lock, _hitl_state, etc.) into this blueprint's
     scope without requiring each handler to re-import individually.

  2. `globals().update(vars(web_app))` — injects the full web_app.py namespace
     (helper functions, SSE infrastructure, constants) so extracted route handlers
     function identically to when they resided in web_app.py.

DO NOT refactor to explicit imports without a full Phase Z6 dependency trace.
All wildcard imports are documented in docs/IMPORT_GRAPH_AUDIT.md.
"""
import os
import json
import time
import sqlite3
from flask import Blueprint, jsonify, request, Response, stream_with_context, g

# Intentional wildcard — see module docstring above
from runtime.state import *
import web_app
globals().update({k: v for k, v in vars(web_app).items() if not k.startswith('__')})

session_bp = Blueprint('session_routes', __name__)


