from datetime import timedelta

from flask import request, session

from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.models.store import Store
from server.app.models.user import User, PLATFORM_ADMIN_ROLES
from server.app.services.audit_service import AuditService
from server.app.utils.response import Response
from server.app.utils.security import verify_password
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc, ensure_utc
from server.app.utils.validators import validate_password


STORE_DELETION_CONFIRMATION = "DELETE STORE"
STORE_DELETION_RECOVERY_DAYS = 30


class SettingsService:
    def update_store_name(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400

        store_id = data.get("store_id")
        name = data.get("name")
        fields = {}
        if not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0:
            fields["store_id"] = "Store ID must be a positive integer"
        if not isinstance(name, str) or not name.strip():
            fields["name"] = "Store name is required"
        elif len(name.strip()) > 100:
            fields["name"] = "Store name must be 100 characters or fewer"
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        store = get_authorized_store(store_id)
        if not store:
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have permission to update this store", {}), 403

        normalized_name = name.strip()
        previous_name = store.name
        store.name = normalized_name
        db.session.add(AuditLog(
            store_id=store.id,
            user_id=session.get("user_id"),
            action_type="store_name_updated",
            entity_type="store",
            entity_id=str(store.id),
            before_values={"name": previous_name},
            after_values={"name": normalized_name},
        ))
        db.session.commit()
        return Response.success_response({
            "store": {"id": store.id, "name": store.name, "user_id": store.user_id, "created_at": store.created_at.isoformat()}
        }, "STORE_UPDATED"), 200

    def request_store_deletion(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400

        store_id = data.get("store_id")
        current_password = data.get("current_password")
        confirmation = data.get("confirmation")
        fields = {}
        if not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0:
            fields["store_id"] = "Store ID must be a positive integer"
        if validate_password(current_password, minimum_length=1):
            fields["current_password"] = "Current password is required"
        if confirmation != STORE_DELETION_CONFIRMATION:
            fields["confirmation"] = f'Type "{STORE_DELETION_CONFIRMATION}" to confirm'
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user or user.account_status != "active":
            return Response.error_response("ACCOUNT_NOT_ACTIVE", "This account cannot close a store", {}), 409
        if user.platform_role in PLATFORM_ADMIN_ROLES:
            return Response.error_response("ACCOUNT_DELETION_RESTRICTED", "Platform administrator accounts cannot use self-service store closure", {}), 403
        if not verify_password(current_password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current password is incorrect", {"current_password": "Current password is incorrect"}), 401

        # Ownership is checked on the server. A client cannot close another
        # owner's store by supplying its ID.
        store = Store.query.filter_by(id=store_id, user_id=user.id).first()
        if not store:
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        if store.status != "active":
            return Response.error_response("STORE_NOT_ACTIVE", "This store is already closed or pending deletion", {}), 409

        requested_at = now_utc()
        scheduled_for = requested_at + timedelta(days=STORE_DELETION_RECOVERY_DAYS)
        AuditService.record(
            store.id,
            "store_deletion_requested",
            "store",
            store.id,
            details={"recovery_deadline": scheduled_for.isoformat()},
        )
        store.status = "pending_deletion"
        store.deletion_requested_at = requested_at
        store.deletion_scheduled_for = scheduled_for
        db.session.commit()
        return Response.success_response({
            "store_id": store.id,
            "store_status": store.status,
            "requested_at": requested_at.isoformat(),
            "recovery_deadline": scheduled_for.isoformat(),
            "can_cancel_until": scheduled_for.isoformat(),
        }, "STORE_DELETION_SCHEDULED"), 202

    def cancel_store_deletion(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400

        store_id = data.get("store_id")
        current_password = data.get("current_password")
        fields = {}
        if not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0:
            fields["store_id"] = "Store ID must be a positive integer"
        if validate_password(current_password, minimum_length=1):
            fields["current_password"] = "Current password is required"
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400

        user = User.query.filter_by(id=session.get("user_id")).first()
        if not user or user.account_status != "active":
            return Response.error_response("ACCOUNT_NOT_ACTIVE", "This account cannot restore a store", {}), 409
        if not verify_password(current_password, user.password_hash):
            return Response.error_response("AUTH_INVALID_CURRENT_PASSWORD", "Current password is incorrect", {"current_password": "Current password is incorrect"}), 401

        store = Store.query.filter_by(id=store_id, user_id=user.id).first()
        if not store:
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        if store.status != "pending_deletion" or not store.deletion_scheduled_for:
            return Response.error_response("NO_PENDING_DELETION", "No cancellable store deletion request exists", {}), 409
        if ensure_utc(store.deletion_scheduled_for) <= now_utc():
            return Response.error_response("RECOVERY_PERIOD_EXPIRED", "The store recovery period has expired", {}), 410

        store.status = "active"
        store.deletion_requested_at = None
        store.deletion_scheduled_for = None
        AuditService.record(store.id, "store_deletion_cancelled", "store", store.id)
        db.session.commit()
        return Response.success_response({"store_id": store.id, "store_status": "active"}, "STORE_DELETION_CANCELLED"), 200
