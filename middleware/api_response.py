import time
from flask import jsonify, request, g

def json_response(data=None, error=None, meta=None, status=200):
    """
    Canonical API response formatter for Nexora AI.
    Follows a strict schema: { ok: bool, data: any, error: dict|null, meta: dict }
    """
    is_ok = status >= 200 and status < 300
    
    # Calculate execution time if request started tracking
    exec_ms = None
    if hasattr(g, 'start_time'):
        exec_ms = round((time.time() - g.start_time) * 1000, 2)
        
    response_meta = {
        "timestamp": time.time(),
        "path": request.path if request else None,
        "method": request.method if request else None,
        "exec_ms": exec_ms
    }
    
    if meta:
        response_meta.update(meta)
        
    payload = {
        "ok": is_ok,
        "data": data,
        "error": error,
        "meta": response_meta
    }
    
    return jsonify(payload), status

def success_response(data=None, meta=None, status=200):
    return json_response(data=data, meta=meta, status=status)

def error_response(message, code="INTERNAL_ERROR", details=None, status=500):
    error_obj = {
        "message": message,
        "code": code
    }
    if details:
        error_obj["details"] = details
        
    return json_response(error=error_obj, status=status)
