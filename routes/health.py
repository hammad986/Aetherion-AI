from flask import Blueprint
from middleware.api_response import success_response
from middleware.guards import require_auth

health_bp = Blueprint('health', __name__, url_prefix='/api/v2')

@health_bp.route('/health', methods=['GET'])
def check_health():
    """
    Extracted health check demonstrating the canonical response contract.
    """
    return success_response(data={"status": "ok", "version": "2.0.0"})

@health_bp.route('/health/secure', methods=['GET'])
@require_auth
def check_secure_health():
    """
    Demonstrates auth guard and canonical response contract.
    """
    return success_response(data={"status": "secure_ok", "auth": True})
