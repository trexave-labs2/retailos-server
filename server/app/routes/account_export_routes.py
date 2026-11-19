from flask import Blueprint

from server.app.middleware.auth_middleware import require_session
from server.app.services.account_export_service import AccountExportService


account_export_bp = Blueprint("account_export", __name__)
account_export = AccountExportService()


@account_export_bp.route("/export", methods=["GET"])
@require_session
def export_store_account():
    return account_export.export_store()
