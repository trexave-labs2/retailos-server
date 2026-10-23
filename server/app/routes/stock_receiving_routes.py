from flask import Blueprint

from server.app.middleware.auth_middleware import require_session
from server.app.services.stock_receiving_service import StockReceivingService


stock_receiving_bp = Blueprint("stock_receiving", __name__)
service = StockReceivingService()


@stock_receiving_bp.route("", methods=["POST"])
@require_session
def receive_stock():
    return service.receive_stock()


@stock_receiving_bp.route("", methods=["GET"])
@require_session
def list_receivings():
    return service.list_receivings()
