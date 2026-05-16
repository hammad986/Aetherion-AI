import os
import json
import time
import sqlite3
from flask import Blueprint, jsonify, request, Response, stream_with_context, g

from runtime.state import *
import web_app
globals().update({k: v for k, v in vars(web_app).items() if not k.startswith('__')})

memory_bp = Blueprint('memory_routes', __name__)

@memory_bp.route("/api/memory")
def api_memory():
    info = {"learnings": [], "tasks": [], "snippets": [], "kv": {}}
    try:
        c = sqlite3.connect(os.path.join(BASE_DIR, "memory.db"))
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT category,insight,created_at FROM learnings "
                           "ORDER BY id DESC LIMIT 30"):
            info["learnings"].append(dict(r))
        for r in c.execute("SELECT task,status,api_used,created_at FROM tasks "
                           "ORDER BY id DESC LIMIT 20"):
            info["tasks"].append(dict(r))
        for r in c.execute("SELECT name,lang,used_count FROM snippets "
                           "ORDER BY used_count DESC LIMIT 20"):
            info["snippets"].append(dict(r))
        c.close()
    except Exception as e:
        info["memory_db_error"] = str(e)
    try:
        with open(os.path.join(BASE_DIR, "memory.json"), "r", encoding="utf-8") as f:
            jdata = json.load(f)
        kv = jdata.get("kv") or {}
        info["kv"] = {k: (str(v)[:200]) for k, v in list(kv.items())[:20]}
        info["message_count"] = len(jdata.get("messages") or [])
    except Exception:
        pass
    return jsonify(info)

@memory_bp.route("/api/memory/recent")
def api_memory_recent():
    """Return recent task memory: per-session short-term + LTM recent tasks."""
    sid   = request.args.get("sid", "")
    limit = min(int(request.args.get("limit", 20)), 50)

    stm_records  = p10_get_session_memory(sid) if sid else []
    ltm_records  = []
    insights     = []

    try:
        from long_term_memory import get_ltm
        ltm = get_ltm()
        ltm_records = ltm.search("task_result", k=limit)
    except Exception:
        pass

    try:
        c = sqlite3.connect(os.path.join(BASE_DIR, "memory.db"))
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT task,status,api_used,created_at FROM tasks "
                           "ORDER BY id DESC LIMIT ?", (limit,)):
            ltm_records.append(dict(r))
        c.close()
    except Exception:
        pass

    return jsonify({
        "ok":         True,
        "session_stm": stm_records,
        "recent":     ltm_records[:limit],
    })

@memory_bp.route("/api/memory/insights")
def api_memory_insights():
    """Return learned patterns and insights from long-term memory."""
    limit   = min(int(request.args.get("limit", 10)), 30)
    learnings = []
    patterns  = []

    try:
        c = sqlite3.connect(os.path.join(BASE_DIR, "memory.db"))
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT category,insight,created_at FROM learnings "
                           "ORDER BY id DESC LIMIT ?", (limit,)):
            learnings.append(dict(r))
        c.close()
    except Exception:
        pass

    try:
        from long_term_memory import get_ltm
        ltm  = get_ltm()
        hits = ltm.recall_patterns("code task", k=limit)
        patterns = [{"pattern": h.get("task",""), "content": h.get("content","")[:120]}
                    for h in hits]
    except Exception:
        pass

    # Synthetic insights from STM across all sessions
    synthetic = []
    all_records = []
    for deq in _P10_STM.values():
        all_records.extend(deq)
    failures = [r for r in all_records if r["status"] != "success"]
    if failures:
        error_types = {}
        for r in failures:
            err = (r.get("error") or "")[:60]
            error_types[err] = error_types.get(err, 0) + 1
        top_err = max(error_types, key=error_types.get)
        synthetic.append({
            "category": "frequent_failure",
            "insight":  f"Most common error pattern: {top_err} ({error_types[top_err]} times)"
        })

    return jsonify({
        "ok":        True,
        "learnings": learnings,
        "patterns":  patterns,
        "synthetic": synthetic,
    })