"""Self-service profile and session security for platform administrators."""

from flask import g, jsonify, make_response, request

from server.app.extensions import db
from server.app.middleware.auth_middleware import clear_admin_session
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.utils.response import Response
from server.app.utils.time import now_utc
from server.app.utils.validators import validate_required_string


class PlatformAdminSettingsService:
    @staticmethod
    def _current_admin():
        return g.platform_admin

    @staticmethod
    def get_profile():
        user = PlatformAdminSettingsService._current_admin()
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401
        return Response.success_response({
            "profile": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "platform_role": user.platform_role,
            }
        }, "PLATFORM_ADMIN_PROFILE_RETRIEVED"), 200

    @staticmethod
    def update_profile():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "A JSON object is required",
                {"payload": "Payload must be a JSON object"},
            ), 400
        if set(data) != {"name"}:
            return Response.error_response(
                "VALIDATION_ERROR", "Only the name field can be updated",
                {"fields": "Exactly one field, name, is required"},
            ), 400
        error = validate_required_string(data.get("name"), "Name", 2)
        if error:
            return Response.error_response(
                "VALIDATION_ERROR", "Invalid profile data", {"name": error}
            ), 400

        user = PlatformAdminSettingsService._current_admin()
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401

        new_name = data["name"].strip()
        previous_name = user.name
        if new_name == previous_name:
            return Response.success_response({
                "profile": {
                    "id": user.id, "name": user.name, "email": user.email,
                    "platform_role": user.platform_role,
                },
                "changed": False,
            }, "PLATFORM_ADMIN_PROFILE_UNCHANGED"), 200

        user.name = new_name
        db.session.add(PlatformAuditLog(
            actor_user_id=user.id,
            action_type="platform_admin.profile_updated",
            target_type="user",
            target_id=str(user.id),
            details={"previous_name": previous_name, "new_name": new_name},
            created_at=now_utc(),
        ))
        db.session.commit()
        return Response.success_response({
            "profile": {
                "id": user.id, "name": user.name, "email": user.email,
                "platform_role": user.platform_role,
            },
            "changed": True,
        }, "PLATFORM_ADMIN_PROFILE_UPDATED"), 200

    @staticmethod
    def revoke_all_sessions():
        # This application uses versioned signed cookies, not persisted device
        # sessions. Incrementing the account version invalidates every cookie.
        if request.get_data(cache=True):
            return Response.error_response(
                "VALIDATION_ERROR", "This endpoint does not accept a request body", {}
            ), 400

        user = PlatformAdminSettingsService._current_admin()
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401

        user.admin_session_version += 1
        db.session.add(PlatformAuditLog(
            actor_user_id=user.id,
            action_type="platform_admin.sessions_revoked",
            target_type="user",
            target_id=str(user.id),
            details={"revoked_session_version": user.admin_session_version - 1,
                     "new_session_version": user.admin_session_version},
            created_at=now_utc(),
        ))
        db.session.commit()
        response = make_response(jsonify(Response.success_response({
            "revoked": True,
            "sessions_invalidated": "all",
        }, "PLATFORM_ADMIN_SESSIONS_REVOKED")), 200)
        return clear_admin_session(response)
