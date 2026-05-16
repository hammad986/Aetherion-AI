import time
import logging
import uuid
from flask import request, g, current_app

logger = logging.getLogger("nexora.observability")

def setup_observability(app):
    """
    Hooks into the Flask app lifecycle to add request tracing,
    timing, and structured logging.
    """
    
    @app.before_request
    def start_timer():
        g.start_time = time.time()
        g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        
        # Log incoming request
        if not request.path.startswith("/static/"):
            logger.info(f"[REQ] {request.method} {request.path} | req_id={g.request_id}")

    @app.after_request
    def log_response(response):
        if not request.path.startswith("/static/"):
            exec_ms = round((time.time() - getattr(g, 'start_time', time.time())) * 1000, 2)
            status = response.status_code
            
            # Log response timing and status
            log_msg = f"[RES] {request.method} {request.path} | status={status} | ms={exec_ms} | req_id={getattr(g, 'request_id', 'unknown')}"
            
            if status >= 500:
                logger.error(log_msg)
            elif status >= 400:
                logger.warning(log_msg)
            else:
                logger.info(log_msg)
                
            # Inject tracing headers
            response.headers["X-Request-Id"] = getattr(g, 'request_id', 'unknown')
            response.headers["X-Execution-Time-Ms"] = str(exec_ms)
            
        return response

    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        logger.exception(f"[ERROR] Unhandled exception in {request.path}: {str(e)}")
        # If we have the api_response module loaded, we could return a formatted error here.
        # But to avoid circular imports, we just let Flask handle it or import locally.
        try:
            from middleware.api_response import error_response
            return error_response(
                message="An unexpected internal error occurred.",
                code="INTERNAL_SERVER_ERROR",
                status=500
            )
        except ImportError:
            return {"error": "Internal Server Error"}, 500
