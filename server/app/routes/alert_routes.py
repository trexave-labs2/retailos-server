from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.alert_service import AlertService

alerts = AlertService()
alert_bp = Blueprint("alerts", __name__)


@alert_bp.route("", methods=["GET"])
@require_session
def list_alerts():
    return alerts.list_alerts()


@alert_bp.route("/read", methods=["PATCH", "POST"])
@require_session
def mark_alert_read():
    return alerts.mark_alert_read()


@alert_bp.route("/dismiss", methods=["POST", "PATCH"])
@require_session
def dismiss_alert():
    return alerts.dismiss_alert()


@alert_bp.route("/resolve", methods=["POST"])
@require_session
def resolve_alert():
    return alerts.resolve_alert()
