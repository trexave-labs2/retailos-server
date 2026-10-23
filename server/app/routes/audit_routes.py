from flask import Blueprint

from server.app.middleware.auth_middleware import require_session
from server.app.services.audit_service import AuditService


audit_bp = Blueprint("audit", __name__)
audit_service = AuditService()


@audit_bp.route("", methods=["GET"])
@require_session
def list_audit_logs():
    return audit_service.list_logs()
