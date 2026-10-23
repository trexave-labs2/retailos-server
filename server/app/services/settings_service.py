from flask import request, session
from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.models.store import Store
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store

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
