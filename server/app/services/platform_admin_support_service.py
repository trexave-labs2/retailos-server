"""Platform-admin feedback support workflow and audit history APIs."""

from datetime import date, datetime, time, timedelta, timezone

from flask import g, request
from sqlalchemy import or_

from server.app.extensions import db
from server.app.models.feedback_submission import FeedbackSubmission
from server.app.models.platform_audit_log import PlatformAuditLog
from server.app.models.user import User
from server.app.utils.response import Response
from server.app.utils.time import now_utc


SUPPORT_STATUSES = {"new", "in_progress", "resolved"}
DELIVERY_STATUSES = {"pending", "sent", "failed"}
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _page_args():
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", str(DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        return None, None, (Response.error_response(
            "VALIDATION_ERROR", "page and per_page must be integers", {}
        ), 400)
    if page < 1 or not 1 <= per_page <= MAX_PAGE_SIZE:
        return None, None, (Response.error_response(
            "VALIDATION_ERROR", "page must be positive and per_page must be between 1 and 100", {}
        ), 400)
    return page, per_page, None


def _paginate(query, page, per_page, serialize):
    total = query.order_by(None).count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [serialize(row) for row in rows],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
            "has_next": page * per_page < total,
            "has_previous": page > 1,
        },
    }


def _parse_bound(value, is_end=False):
    if not value:
        return None
    if len(value) == 10:
        parsed_date = date.fromisoformat(value)
        boundary = datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)
        return boundary + timedelta(days=1) if is_end else boundary
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _feedback_summary(record):
    return {
        "id": record.id,
        "submission_id": record.submission_id,
        "category": record.category,
        "contact_name": record.contact_name,
        "contact_email": record.contact_email,
        "delivery_status": record.status,
        "support_status": record.support_status,
        "created_at": record.created_at.isoformat(),
        "delivered_at": record.delivered_at.isoformat() if record.delivered_at else None,
    }


class PlatformAdminSupportService:
    @staticmethod
    def list_feedback():
        page, per_page, error = _page_args()
        if error:
            return error
        query = db.session.query(FeedbackSubmission)

        support_status = request.args.get("support_status")
        if support_status:
            if support_status not in SUPPORT_STATUSES:
                return Response.error_response(
                    "VALIDATION_ERROR", "Unsupported support_status", {"support_status": support_status}
                ), 400
            query = query.filter(FeedbackSubmission.support_status == support_status)

        delivery_status = request.args.get("delivery_status")
        if delivery_status:
            if delivery_status not in DELIVERY_STATUSES:
                return Response.error_response(
                    "VALIDATION_ERROR", "Unsupported delivery_status", {"delivery_status": delivery_status}
                ), 400
            query = query.filter(FeedbackSubmission.status == delivery_status)

        category = request.args.get("category")
        if category:
            if category not in {"bug", "suggestion", "question", "other"}:
                return Response.error_response(
                    "VALIDATION_ERROR", "Unsupported category", {"category": category}
                ), 400
            query = query.filter(FeedbackSubmission.category == category)

        search = request.args.get("search", "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(
                FeedbackSubmission.submission_id.ilike(pattern),
                FeedbackSubmission.category.ilike(pattern),
                FeedbackSubmission.message.ilike(pattern),
                FeedbackSubmission.contact_name.ilike(pattern),
                FeedbackSubmission.contact_email.ilike(pattern),
            ))

        sort_fields = {
            "created_at": FeedbackSubmission.created_at,
            "id": FeedbackSubmission.id,
            "support_status": FeedbackSubmission.support_status,
            "delivery_status": FeedbackSubmission.status,
        }
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        if sort_by not in sort_fields or sort_order not in {"asc", "desc"}:
            return Response.error_response(
                "VALIDATION_ERROR", "Unsupported sort_by or sort_order",
                {"sort_by": sort_by, "sort_order": sort_order},
            ), 400
        column = sort_fields[sort_by]
        query = query.order_by(
            column.asc() if sort_order == "asc" else column.desc(),
            FeedbackSubmission.id.desc(),
        )
        return Response.success_response(
            _paginate(query, page, per_page, _feedback_summary), "PLATFORM_FEEDBACK_RETRIEVED"
        ), 200

    @staticmethod
    def feedback_detail(feedback_id):
        record = db.session.get(FeedbackSubmission, feedback_id)
        if not record:
            return Response.error_response("FEEDBACK_NOT_FOUND", "Feedback not found", {}), 404
        # Explicit allow-list: delivery internals and unrelated database fields
        # are not part of the support workflow response.
        data = _feedback_summary(record)
        data["message"] = record.message
        return Response.success_response({"feedback": data}, "PLATFORM_FEEDBACK_RETRIEVED"), 200

    @staticmethod
    def update_feedback_status(feedback_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "A JSON object is required", {}), 400
        requested_status = data.get("support_status")
        if not isinstance(requested_status, str) or requested_status not in SUPPORT_STATUSES:
            return Response.error_response(
                "VALIDATION_ERROR",
                "support_status must be new, in_progress, or resolved",
                {"support_status": "invalid"},
            ), 400

        record = db.session.query(FeedbackSubmission).filter_by(id=feedback_id).with_for_update().first()
        if not record:
            return Response.error_response("FEEDBACK_NOT_FOUND", "Feedback not found", {}), 404

        previous_status = record.support_status
        if previous_status == requested_status:
            return Response.success_response({
                "feedback": _feedback_summary(record),
                "changed": False,
            }, "FEEDBACK_SUPPORT_STATUS_UNCHANGED"), 200

        actor_id = g.platform_admin.id
        record.support_status = requested_status
        db.session.add(PlatformAuditLog(
            actor_user_id=actor_id,
            action_type="feedback.support_status_changed",
            target_type="feedback_submission",
            target_id=str(record.id),
            details={
                "submission_id": record.submission_id,
                "previous_support_status": previous_status,
                "new_support_status": requested_status,
            },
            created_at=now_utc(),
        ))
        db.session.commit()
        return Response.success_response({
            "feedback": _feedback_summary(record),
            "changed": True,
        }, "FEEDBACK_SUPPORT_STATUS_UPDATED"), 200

    @staticmethod
    def list_audit_logs():
        page, per_page, error = _page_args()
        if error:
            return error
        query = db.session.query(PlatformAuditLog)

        actor_raw = request.args.get("actor_user_id")
        if actor_raw is not None:
            try:
                actor_id = int(actor_raw)
                if actor_id <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return Response.error_response(
                    "VALIDATION_ERROR", "actor_user_id must be a positive integer",
                    {"actor_user_id": actor_raw},
                ), 400
            query = query.filter(PlatformAuditLog.actor_user_id == actor_id)

        for parameter, column in (
            ("action_type", PlatformAuditLog.action_type),
            ("target_type", PlatformAuditLog.target_type),
            ("target_id", PlatformAuditLog.target_id),
        ):
            value = request.args.get(parameter)
            if value:
                query = query.filter(column == value)

        start_raw = request.args.get("start_date")
        end_raw = request.args.get("end_date")
        try:
            start = _parse_bound(start_raw)
            end = _parse_bound(end_raw, is_end=True)
        except ValueError:
            return Response.error_response(
                "VALIDATION_ERROR", "Dates must be valid ISO-8601 date or date-time values",
                {"start_date": start_raw, "end_date": end_raw},
            ), 400
        if start and end and start >= end:
            return Response.error_response(
                "VALIDATION_ERROR", "start_date must be earlier than end_date", {}
            ), 400
        if start:
            query = query.filter(PlatformAuditLog.created_at >= start)
        if end:
            if end_raw and len(end_raw) == 10:
                query = query.filter(PlatformAuditLog.created_at < end)
            else:
                query = query.filter(PlatformAuditLog.created_at <= end)

        query = query.order_by(PlatformAuditLog.created_at.desc(), PlatformAuditLog.id.desc())

        def serialize(row):
            actor = db.session.get(User, row.actor_user_id) if row.actor_user_id else None
            return {
                "id": row.id,
                "actor": {
                    "id": actor.id,
                    "name": actor.name,
                } if actor else ({"id": row.actor_user_id, "name": None} if row.actor_user_id else None),
                "actor_user_id": row.actor_user_id,
                "action_type": row.action_type,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }

        return Response.success_response(
            _paginate(query, page, per_page, serialize), "PLATFORM_AUDIT_LOGS_RETRIEVED"
        ), 200
