from flask import Blueprint
from flask_limiter.util import get_remote_address
from server.app.services.auth_service import AuthService
from server.app.middleware.auth_middleware import require_session
from server.app.extensions import limiter
from server.app.services.password_reset_service import PasswordResetService

auth = AuthService()
password_reset = PasswordResetService()
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["POST"])
def register():
    return auth.register_user()


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("20 per minute", key_func=get_remote_address)
@limiter.limit("5 per minute")
def login():
    return auth.login_user()


@auth_bp.route("/me", methods=["GET"])
@require_session
def me():
    return auth.current_user()


@auth_bp.route("/change-password", methods=["POST"])
@require_session
def change_password():
    return auth.change_password()


@auth_bp.route("/logout", methods=["POST"])
@require_session
def logout():
    return auth.logout_user()


@auth_bp.route("/delete-account", methods=["POST"])
@require_session
@limiter.limit("5 per hour")
def delete_account():
    return auth.request_account_deletion()


@auth_bp.route("/cancel-account-deletion", methods=["POST"])
@limiter.limit("5 per hour", key_func=get_remote_address)
def cancel_account_deletion():
    return auth.cancel_account_deletion()


@auth_bp.route("/password-reset/request", methods=["POST"])
@limiter.limit("5 per hour", key_func=get_remote_address)
def request_password_reset():
    return password_reset.request_reset()


@auth_bp.route("/password-reset/complete", methods=["POST"])
@limiter.limit("10 per hour", key_func=get_remote_address)
def complete_password_reset():
    return password_reset.complete_reset()
