"""Super-admin-only management of platform administrator privileges."""
from flask import request, session

from server.app.extensions import db
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE, User
from server.app.utils.time import now_utc
from server.app.utils.response import Response


class PlatformAdminRolesService:
    @staticmethod
    def list_administrators():
        users = User.query.filter(User.platform_role.in_((PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE))).order_by(User.id.asc()).all()
        return Response.success_response({
            "items": [{
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "platform_role": user.platform_role,
                "created_at": user.created_at.isoformat(),
            } for user in users]
        }, "PLATFORM_ADMINISTRATORS_RETRIEVED"), 200

    @staticmethod
    def set_admin_role(user_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {"platform_role"}:
            return Response.error_response("VALIDATION_ERROR", "Provide exactly the platform_role field", {"platform_role": "Use admin to grant access or null to revoke it"}), 400
        requested_role = data.get("platform_role")
        if requested_role not in (PLATFORM_ADMIN_ROLE, None):
            return Response.error_response("VALIDATION_ERROR", "Only admin or null is supported", {"platform_role": "The super_admin role cannot be assigned through this endpoint"}), 400

        actor_id = session.get("user_id")
        if actor_id == user_id:
            return Response.error_response("SELF_ROLE_CHANGE_FORBIDDEN", "The super administrator cannot change their own role", {}), 400
        target = db.session.get(User, user_id)
        if not target:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 404
        if target.platform_role == SUPER_ADMIN_ROLE:
            return Response.error_response("SUPER_ADMIN_PROTECTED", "The bootstrap super administrator cannot be changed through administrator management", {}), 403
        previous_role = target.platform_role
        if previous_role == requested_role:
            return Response.success_response({"user_id": target.id, "platform_role": target.platform_role, "changed": False}, "ADMIN_ROLE_UNCHANGED"), 200

        target.platform_role = requested_role
        action = "platform_admin.role_granted" if requested_role == PLATFORM_ADMIN_ROLE else "platform_admin.role_revoked"
        db.session.add(PlatformAuditLog(
            actor_user_id=actor_id,
            action_type=action,
            target_type="user",
            target_id=str(target.id),
            details={"previous_role": previous_role, "new_role": requested_role},
            created_at=now_utc(),
        ))
        db.session.commit()
        return Response.success_response({
            "user_id": target.id,
            "platform_role": target.platform_role,
            "changed": True,
        }, "ADMIN_ROLE_UPDATED"), 200
