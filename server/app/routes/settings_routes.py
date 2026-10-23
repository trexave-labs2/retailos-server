from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.settings_service import SettingsService

settings_bp = Blueprint("settings", __name__)
settings = SettingsService()

@settings_bp.route("/store", methods=["PATCH"])
@require_session
def update_store():
    return settings.update_store_name()
