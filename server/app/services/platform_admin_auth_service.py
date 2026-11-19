from flask import g, jsonify, make_response, request

from server.app.extensions import db
from server.app.middleware.auth_middleware import admin_session_response, clear_admin_session
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLES, User
from server.app.utils.response import Response
from server.app.utils.security import hash_password, verify_password
from server.app.utils.time import now_utc
from server.app.utils.validators import validate_email, validate_password


class PlatformAdminAuthService:
    @staticmethod
    def login():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(Response.error_response("VALIDATION_ERROR", "Invalid JSON object", {})), 400
        email, password = data.get("email"), data.get("password")
        fields = {}
        if validate_email(email):
            fields["email"] = "Enter a valid email address"
        if validate_password(password, minimum_length=1):
            fields["password"] = "Password is required"
        if fields:
            return jsonify(Response.error_response("VALIDATION_ERROR", "Invalid input data", fields)), 400

        user = User.query.filter(db.func.lower(User.email) == email.strip().lower()).first()
        if (not user or user.platform_role not in PLATFORM_ADMIN_ROLES
                or user.account_status != "active" or not user.admin_password_hash
                or not verify_password(password, user.admin_password_hash)):
            return jsonify(Response.error_response("AUTH_INVALID_CREDENTIALS", "Invalid email or password", {})), 401

        response = make_response(jsonify(Response.success_response({
            "profile": {
                "id": user.id, "name": user.name, "email": user.email,
                "platform_role": user.platform_role,
            }
        }, "PLATFORM_ADMIN_LOGIN_SUCCESSFUL")), 200)
        return admin_session_response(response, user)

    @staticmethod
    def current_admin():
        user = g.platform_admin
        return Response.success_response({"profile": {
            "id": user.id, "name": user.name, "email": user.email,
            "platform_role": user.platform_role,
        }}, "PLATFORM_ADMIN_SESSION_RETRIEVED"), 200

    @staticmethod
    def logout():
        response = make_response(jsonify(Response.success_response({}, "PLATFORM_ADMIN_LOGOUT_SUCCESSFUL")), 200)
        return clear_admin_session(response)

    @staticmethod
    def change_password():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        fields = {}
        if validate_password(current_password, minimum_length=1):
            fields["current_password"] = "Current password is required"
        error = validate_password(new_password, minimum_length=12)
        if error:
            fields["new_password"] = error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = g.platform_admin
        if not verify_password(current_password, user.admin_password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current administrator password is incorrect", {"current_password": "Current password is incorrect"}), 401
        if verify_password(new_password, user.admin_password_hash):
            return Response.error_response("PASSWORD_UNCHANGED", "New password must differ from the current administrator password", {"new_password": "Choose a different password"}), 400

        user.admin_password_hash = hash_password(new_password)
        user.admin_session_version += 1
        db.session.add(PlatformAuditLog(
            actor_user_id=user.id,
            action_type="platform_admin.password_changed",
            target_type="user",
            target_id=str(user.id),
            details={"admin_sessions_invalidated": True},
            created_at=now_utc(),
        ))
        db.session.commit()
        response = make_response(jsonify(Response.success_response({}, "PLATFORM_ADMIN_PASSWORD_CHANGED_LOGIN_AGAIN")), 200)
        return clear_admin_session(response)
