"""Read-only platform-admin subscription and plan-request queries."""
from datetime import datetime, timezone
from flask import request
from sqlalchemy import or_
from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.subscription_change import SubscriptionChange
from server.app.models.user import User
from server.app.services.platform_admin_service import _pagination, _page_args, _subscription
from server.app.services.subscription_service import PLAN_CATALOG
from server.app.utils.response import Response


def _state_filter(query, state, now):
    expired_period = (Store.current_period_end.is_not(None) &
                      (Store.current_period_end <= now))
    if state == "free":
        return query.filter(
            or_(Store.current_plan == "free", Store.current_plan.is_(None)),
            Store.subscription_status != "expired",
        )
    if state == "trial":
        return query.filter(Store.current_plan == "pro",
                            Store.subscription_status == "trial",
                            or_(Store.current_period_end.is_(None), Store.current_period_end > now))
    if state == "active":
        return query.filter(Store.current_plan == "pro",
                            Store.subscription_status == "active",
                            or_(Store.current_period_end.is_(None), Store.current_period_end > now))
    if state == "expired":
        return query.filter(or_(
            Store.subscription_status == "expired",
            (Store.current_plan == "pro") &
            Store.subscription_status.in_(("active", "trial")) & expired_period
        ))
    return query


class PlatformAdminSubscriptionService:
    @staticmethod
    def list_subscriptions():
        page, per_page, error = _page_args()
        if error:
            return error
        now = datetime.now(timezone.utc)
        query = db.session.query(Store).join(User, User.id == Store.user_id)

        state = request.args.get("state")
        if state:
            if state not in ("free", "trial", "active", "expired"):
                return Response.error_response(
                    "VALIDATION_ERROR", "state must be free, trial, active, or expired",
                    {"state": state}
                ), 400
            query = _state_filter(query, state, now)

        plan = request.args.get("plan")
        if plan:
            if plan not in PLAN_CATALOG:
                return Response.error_response("VALIDATION_ERROR", "Unknown plan filter", {"plan": plan}), 400
            query = query.filter(Store.current_plan == plan)

        search = request.args.get("search", "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(Store.name.ilike(pattern), User.name.ilike(pattern), User.email.ilike(pattern)))

        sort_fields = {
            "created_at": Store.created_at, "name": Store.name, "id": Store.id,
            "plan": Store.current_plan, "status": Store.subscription_status,
            "period_end": Store.current_period_end,
        }
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        if sort_by not in sort_fields or sort_order not in ("asc", "desc"):
            return Response.error_response(
                "VALIDATION_ERROR", "Unsupported sort_by or sort_order",
                {"sort_by": sort_by, "sort_order": sort_order}
            ), 400
        column = sort_fields[sort_by]
        query = query.order_by(column.asc() if sort_order == "asc" else column.desc(), Store.id.asc())

        def serialize(store):
            owner = db.session.get(User, store.user_id)
            subscription = _subscription(store, now)
            state_value = ("expired" if subscription["subscription_status"] == "expired"
                           else "free" if subscription["plan"] == "free"
                           else subscription["subscription_status"])
            return {
                "store": {"id": store.id, "name": store.name, "created_at": store.created_at.isoformat()},
                "account": {"id": owner.id, "name": owner.name, "email": owner.email} if owner else None,
                "subscription": {**subscription, "state": state_value},
            }

        return Response.success_response(
            _pagination(query, page, per_page, serialize), "PLATFORM_SUBSCRIPTIONS_RETRIEVED"
        ), 200

    @staticmethod
    def list_subscription_requests():
        page, per_page, error = _page_args()
        if error:
            return error
        query = (db.session.query(SubscriptionChange)
                 .join(Store, Store.id == SubscriptionChange.store_id)
                 .join(User, User.id == Store.user_id))

        status = request.args.get("status")
        if status:
            if status not in ("pending", "cancelled"):
                return Response.error_response(
                    "VALIDATION_ERROR", "status must be pending or cancelled", {"status": status}
                ), 400
            query = query.filter(SubscriptionChange.status == status)

        search = request.args.get("search", "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(Store.name.ilike(pattern), User.name.ilike(pattern), User.email.ilike(pattern)))

        sort_fields = {
            "created_at": SubscriptionChange.created_at, "updated_at": SubscriptionChange.updated_at,
            "id": SubscriptionChange.id, "status": SubscriptionChange.status,
        }
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        if sort_by not in sort_fields or sort_order not in ("asc", "desc"):
            return Response.error_response(
                "VALIDATION_ERROR", "Unsupported sort_by or sort_order",
                {"sort_by": sort_by, "sort_order": sort_order}
            ), 400
        column = sort_fields[sort_by]
        query = query.order_by(column.asc() if sort_order == "asc" else column.desc(),
                               SubscriptionChange.id.asc())
        now = datetime.now(timezone.utc)

        def serialize(change):
            store = db.session.get(Store, change.store_id)
            owner = db.session.get(User, store.user_id) if store else None
            current = _subscription(store, now) if store else None
            requested_plan = PLAN_CATALOG.get(change.target_plan, PLAN_CATALOG["free"])
            return {
                "id": change.id,
                "status": change.status,
                "requested_at": change.created_at.isoformat(),
                "updated_at": change.updated_at.isoformat(),
                "requested_by": change.requested_by,
                "account": {"id": owner.id, "name": owner.name, "email": owner.email} if owner else None,
                "store": {"id": store.id, "name": store.name} if store else None,
                "current_plan": {
                    "id": current["plan"], "name": current["plan_name"],
                    "subscription_status": current["subscription_status"],
                } if current else None,
                "requested_plan": {
                    "id": change.target_plan, "name": requested_plan["name"],
                    "billing_interval": change.billing_interval,
                    "price": {"amount": change.price_amount, "currency": "NGN"},
                },
                "activation": "not_activated" if change.status == "pending" else "not_granted_by_request",
            }

        return Response.success_response(
            _pagination(query, page, per_page, serialize), "PLATFORM_SUBSCRIPTION_REQUESTS_RETRIEVED"
        ), 200
