from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.settings_service import SettingsService
from server.app.extensions import limiter

settings_bp = Blueprint("settings", __name__)
settings = SettingsService()


@settings_bp.route("/store", methods=["PATCH"])
@require_session
def update_store():
    return settings.update_store_name()


@settings_bp.route("/store/delete", methods=["POST"])
@require_session
@limiter.limit("5 per hour")
def delete_store():
    return settings.request_store_deletion()


@settings_bp.route("/store/cancel-deletion", methods=["POST"])
@require_session
@limiter.limit("5 per hour")
def cancel_store_deletion():
    return settings.cancel_store_deletion()
