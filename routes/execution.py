from flask import Blueprint, request, Response, stream_with_context
from middleware.api_response import success_response, error_response
from middleware.guards import require_auth, require_plan
from routes.workspace import require_workspace_owner
import time
import json

execution_bp = Blueprint('execution', __name__, url_prefix='/api/v2/execute')

@execution_bp.route('/<workspace_id>/start', methods=['POST'])
@require_auth
@require_workspace_owner
@require_plan("basic")
def start_execution(workspace_id):
    """
    Initiates an execution run in the specified workspace.
    Requires workspace ownership and at least a basic plan.
    """
    payload = request.get_json() or {}
    task = payload.get("task")
    
    if not task:
        return error_response("Task description is required", code="MISSING_TASK", status=400)
        
    # Simulated execution kickoff
    execution_id = f"exec_{int(time.time() * 1000)}"
    
    # Here we would interface with an orchestration queue or background worker.
    # We return the execution_id so the frontend can connect to the SSE stream.
    return success_response(
        data={"execution_id": execution_id, "status": "queued"},
        meta={"workspace_id": workspace_id}
    )

@execution_bp.route('/<workspace_id>/stream/<execution_id>', methods=['GET'])
@require_auth
@require_workspace_owner
def stream_execution(workspace_id, execution_id):
    """
    Attaches to the SSE stream for a specific execution.
    """
    # Import the expanded SSEManager
    from streaming.sse_manager import SSEManager
    
    def generate():
        client = SSEManager.register_client(session_id=execution_id)
        try:
            # Send initial connection success
            yield SSEManager.format_event("status", {"state": "connected"})
            
            # Yield events as they arrive from the execution queue
            while client.connected:
                try:
                    # Wait for an event with a timeout to allow heartbeat checks
                    event = client.queue.get(timeout=15.0)
                    yield event.encode()
                except Exception as e: # Queue Empty timeout
                    # Send Heartbeat
                    yield SSEManager.format_event("ping", {"timestamp": time.time()})
                    
        finally:
            SSEManager.remove_client(client.client_id)

    return Response(
        stream_with_context(generate()), 
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no" # Disable Nginx buffering
        }
    )

@execution_bp.route('/<workspace_id>/stop/<execution_id>', methods=['POST'])
@require_auth
@require_workspace_owner
def stop_execution(workspace_id, execution_id):
    """
    Signals cancellation to an active execution.
    """
    # In a full extraction, this would terminate the subprocess or set a kill flag.
    # For now, we simulate success and notify the SSE stream.
    from streaming.sse_manager import SSEManager
    SSEManager.broadcast_to_session(
        execution_id, 
        event_type="error", 
        payload={"msg": "Execution cancelled by user."}
    )
    SSEManager.broadcast_to_session(
        execution_id,
        event_type="done",
        payload={"status": "cancelled"}
    )
    
    return success_response(data={"cancelled": True, "execution_id": execution_id})
