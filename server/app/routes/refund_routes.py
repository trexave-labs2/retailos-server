from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.refund_service import RefundService

refund_service = RefundService()
refund_bp = Blueprint("refunds", __name__)


@refund_bp.route("", methods=["POST"])
@require_session
def create_refund():
    return refund_service.create_refund()


@refund_bp.route("", methods=["GET"])
@require_session
def list_refunds():
    return refund_service.list_refunds()


@refund_bp.route("/receipt", methods=["GET"])
@require_session
def refund_receipt():
    return refund_service.refund_receipt()
