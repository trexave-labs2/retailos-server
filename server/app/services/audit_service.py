from datetime import date, datetime, time, timedelta, timezone

from flask import request, session

from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc


SENSITIVE_KEYS = {
    "password", "password_hash", "token", "access_token", "refresh_token",
    "secret", "secret_key", "session", "session_id", "authorization",
    "cookie", "csrf_token", "credential", "credentials",
}


def _safe_values(value):
    if value is None or not isinstance(value, dict):
        return value
    safe = {}
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in SENSITIVE_KEYS or any(
            marker in normalized for marker in ("password", "token", "secret", "credential")
        ):
            continue
        if isinstance(item, dict):
            safe[key] = _safe_values(item)
        elif isinstance(item, list):
            safe[key] = [_safe_values(entry) if isinstance(entry, dict) else entry for entry in item]
        elif item is None or isinstance(item, (str, int, float, bool)):
            safe[key] = item
        else:
            safe[key] = str(item)
    return safe


class AuditService:
    @staticmethod
    def record(store_id, action_type, entity_type, entity_id, before=None, after=None, details=None):
        """Add an audit row to the caller's transaction; caller owns commit/rollback."""
        entry = AuditLog(
            store_id=store_id,
            user_id=session.get("user_id"),
            action_type=action_type,
            entity_type=entity_type,
            entity_id=str(entity_id),
            before_values=_safe_values(before),
            after_values=_safe_values(after),
            details=_safe_values(details),
            created_at=now_utc(),
        )
        db.session.add(entry)
        return entry

    def list_logs(self):
        store_id = request.args.get("store_id", type=int)
        if not isinstance(store_id, int) or store_id <= 0:
            return Response.error_response("VALIDATION_ERROR", "Store ID is required as a positive integer", {}), 400
        if not get_authorized_store(store_id):
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403

        query = AuditLog.query.filter_by(store_id=store_id)
        action_type = request.args.get("action_type")
        entity_type = request.args.get("entity_type")
        entity_id = request.args.get("entity_id")
        user_id = request.args.get("user_id", type=int)
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        if action_type:
            query = query.filter(AuditLog.action_type == action_type)
        if entity_type:
            query = query.filter(AuditLog.entity_type == entity_type)
        if entity_id:
            query = query.filter(AuditLog.entity_id == entity_id)
        if user_id is not None:
            query = query.filter(AuditLog.user_id == user_id)

        try:
            if start_date:
                if len(start_date) == 10:
                    start = datetime.combine(date.fromisoformat(start_date), time.min, tzinfo=timezone.utc)
                else:
                    start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                query = query.filter(AuditLog.created_at >= start.astimezone(timezone.utc))
            if end_date:
                if len(end_date) == 10:
                    end = datetime.combine(date.fromisoformat(end_date), time.min, tzinfo=timezone.utc) + timedelta(days=1)
                    query = query.filter(AuditLog.created_at < end)
                else:
                    end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    query = query.filter(AuditLog.created_at <= end.astimezone(timezone.utc))
        except ValueError:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Dates must be valid ISO-8601 date-time values",
                {"start_date": start_date, "end_date": end_date},
            ), 400

        limit = request.args.get("limit", default=100, type=int)
        if limit < 1 or limit > 500:
            return Response.error_response("VALIDATION_ERROR", "Limit must be between 1 and 500", {}), 400

        rows = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()
        return Response.success_response({
            "audit_logs": [{
                "id": row.id,
                "store_id": row.store_id,
                "user_id": row.user_id,
                "action_type": row.action_type,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "before": row.before_values,
                "after": row.after_values,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            } for row in rows],
            "count": len(rows),
            "limit": limit,
        }, "AUDIT_LOGS_RETRIEVED"), 200
