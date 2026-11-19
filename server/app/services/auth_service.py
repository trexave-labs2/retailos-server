from datetime import timedelta

from flask import session, request
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.user import User, PLATFORM_ADMIN_ROLES
from server.app.models.store import Store
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.services.audit_service import AuditService
from server.app.utils.response import Response
from server.app.utils.security import hash_password, verify_password
from server.app.utils.validators import validate_email, validate_required_string, validate_password
from server.app.utils.time import now_utc, ensure_utc


ACCOUNT_DELETION_RECOVERY_DAYS = 30
ACCOUNT_DELETION_CONFIRMATION = "DELETE MY ACCOUNT"


class AuthService:
    def __init__(self):
        pass

    def _user_data(self, user):
        store = Store.query.filter_by(user_id=user.id, status="active").first()
        return {
            "id": user.id,
            "username": user.name,
            "email": user.email,
            "store_id": store.id if store else None,
            "store_name": store.name if store else None,
            "created_at": user.created_at.isoformat(),
        }

    def register_user(self):
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
            username, email, password = data.get("username"), data.get("email"), data.get("password")
            fields = {}
            error = validate_required_string(username, "Username", 2)
            if error:
                fields["username"] = error
            error = validate_email(email)
            if error:
                fields["email"] = error
            error = validate_password(password)
            if error:
                fields["password"] = error
            if fields:
                return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400
            email = email.strip().lower()
            if User.query.filter_by(email=email).first():
                return Response.error_response("EMAIL_ALREADY_EXISTS", "Email already exists", {"email": "Email is already registered"}), 400
            user = User(name=username.strip(), email=email, password_hash=hash_password(password))
            db.session.add(user)
            db.session.flush()
            store = Store(user_id=user.id, name=f"{user.name}'s Store")
            db.session.add(store)
            db.session.commit()
            return Response.success_response({"user": self._user_data(user)}, "REGISTRATION_SUCCESSFUL"), 201
        except IntegrityError:
            db.session.rollback()
            return Response.error_response("CONFLICT_ERROR", "Database constraint violation", {}), 409

    def login_user(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid JSON object", {}), 400
        email, password = data.get("email"), data.get("password")
        fields = {}
        error = validate_email(email)
        if error:
            fields["email"] = error
        error = validate_password(password)
        if error:
            fields["password"] = error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400
        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user or not verify_password(password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CREDENTIALS", "Invalid email or password", {}), 401
        if user.account_status == "pending_deletion":
            return Response.error_response(
                "ACCOUNT_CLOSURE_PENDING",
                "Account deletion is pending. Cancel the request before logging in.",
                {"recovery_deadline": user.deletion_scheduled_for.isoformat() if user.deletion_scheduled_for else None},
            ), 403
        if user.account_status != "active":
            return Response.error_response("AUTH_INVALID_CREDENTIALS", "Invalid email or password", {}), 401
        session.clear()
        session["user_id"] = user.id
        session["session_version"] = user.session_version
        return Response.success_response({"user": self._user_data(user)}, "LOGIN_SUCCESSFUL"), 200

    def current_user(self):
        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401
        return Response.success_response({"user": self._user_data(user)}, "CURRENT_USER_RETRIEVED"), 200

    def change_password(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        fields = {}
        if validate_password(current_password, minimum_length=1):
            fields["current_password"] = "Current password is required or exceeds the supported password length"
        error = validate_password(new_password)
        if error:
            fields["new_password"] = error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user:
            session.clear()
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401
        if not verify_password(current_password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current password is incorrect", {"current_password": "Current password is incorrect"}), 401
        if verify_password(new_password, user.password_hash):
            return Response.error_response("PASSWORD_UNCHANGED", "New password must differ from the current password", {"new_password": "Choose a different password"}), 400

        user.password_hash = hash_password(new_password)
        user.session_version += 1
        if user.platform_role in PLATFORM_ADMIN_ROLES:
            db.session.add(PlatformAuditLog(
                actor_user_id=user.id,
                action_type="platform_admin.password_changed",
                target_type="user",
                target_id=str(user.id),
                details={"sessions_invalidated": True},
                created_at=now_utc(),
            ))
        db.session.commit()
        session.clear()
        return Response.success_response({}, "PASSWORD_CHANGED_LOGIN_AGAIN"), 200

    def logout_user(self):
        user = User.query.filter_by(id=session.get("user_id")).first()
        session.clear()
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 401
        return Response.success_response({"user": {"id": user.id, "username": user.name, "email": user.email}}, "LOGOUT_SUCCESSFUL"), 200

    def request_account_deletion(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        current_password = data.get("current_password")
        confirmation = data.get("confirmation")
        fields = {}
        if validate_password(current_password, minimum_length=1):
            fields["current_password"] = "Current password is required"
        if confirmation != ACCOUNT_DELETION_CONFIRMATION:
            fields["confirmation"] = f'Type "{ACCOUNT_DELETION_CONFIRMATION}" to confirm'
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user or user.account_status != "active":
            return Response.error_response("ACCOUNT_NOT_ACTIVE", "This account cannot start a deletion request", {}), 409
        if user.platform_role in PLATFORM_ADMIN_ROLES:
            return Response.error_response("ACCOUNT_DELETION_RESTRICTED", "A platform administrator account cannot be deleted through self-service", {}), 403
        if not verify_password(current_password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current password is incorrect", {"current_password": "Current password is incorrect"}), 401

        requested_at = now_utc()
        scheduled_for = requested_at + timedelta(days=ACCOUNT_DELETION_RECOVERY_DAYS)
        owned_stores = Store.query.filter_by(user_id=user.id).all()
        stores = [store for store in owned_stores if store.status == "active"]
        for store in stores:
            AuditService.record(
                store.id,
                "account_deletion_requested",
                "account",
                user.id,
                details={"recovery_deadline": scheduled_for.isoformat()},
            )
            store.status = "pending_deletion"
            store.deletion_requested_at = requested_at
            store.deletion_scheduled_for = scheduled_for

        user.account_status = "pending_deletion"
        user.deletion_requested_at = requested_at
        user.deletion_scheduled_for = scheduled_for
        user.session_version += 1
        db.session.add(PlatformAuditLog(
            actor_user_id=user.id,
            action_type="account.deletion_requested",
            target_type="user",
            target_id=str(user.id),
            details={"store_count": len(owned_stores), "recovery_days": ACCOUNT_DELETION_RECOVERY_DAYS},
            created_at=requested_at,
        ))
        db.session.commit()
        session.clear()
        return Response.success_response({
            "account_status": user.account_status,
            "store_count": len(owned_stores),
            "newly_deactivated_store_count": len(stores),
            "requested_at": requested_at.isoformat(),
            "recovery_deadline": scheduled_for.isoformat(),
            "can_cancel_until": scheduled_for.isoformat(),
        }, "ACCOUNT_DELETION_SCHEDULED"), 202

    def cancel_account_deletion(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        email, password = data.get("email"), data.get("password")
        fields = {}
        error = validate_email(email)
        if error:
            fields["email"] = error
        error = validate_password(password)
        if error:
            fields["password"] = error
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user or not verify_password(password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CREDENTIALS", "Invalid email or password", {}), 401
        if user.account_status != "pending_deletion" or not user.deletion_scheduled_for:
            return Response.error_response("NO_PENDING_DELETION", "No cancellable account deletion request exists", {}), 409
        if ensure_utc(user.deletion_scheduled_for) <= now_utc():
            return Response.error_response("RECOVERY_PERIOD_EXPIRED", "The account recovery period has expired", {}), 410

        stores = Store.query.filter_by(
            user_id=user.id,
            status="pending_deletion",
            deletion_requested_at=user.deletion_requested_at,
        ).all()
        for store in stores:
            store.status = "active"
            store.deletion_requested_at = None
            store.deletion_scheduled_for = None
            AuditService.record(store.id, "account_deletion_cancelled", "account", user.id)
        user.account_status = "active"
        user.deletion_requested_at = None
        user.deletion_scheduled_for = None
        user.session_version += 1
        db.session.add(PlatformAuditLog(
            actor_user_id=user.id,
            action_type="account.deletion_cancelled",
            target_type="user",
            target_id=str(user.id),
            details={"stores_reactivated": len(stores)},
            created_at=now_utc(),
        ))
        db.session.commit()
        return Response.success_response({"account_status": "active", "stores_reactivated": len(stores)}, "ACCOUNT_DELETION_CANCELLED"), 200
