"""Read-only platform administration queries.

All aggregation and filtering stays on the server. Responses intentionally use
explicit allow-lists so credentials and session state never leave the backend.
"""
from datetime import datetime, timedelta, timezone

from flask import request
from sqlalchemy import and_, func, or_

from server.app.extensions import db
from server.app.models.alert import Alert
from server.app.models.customer import Customer
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.store import Store
from server.app.models.user import User
from server.app.services.subscription_service import PLAN_CATALOG
from server.app.utils.response import Response


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _subscription(store, now):
    end = _utc(store.current_period_end)
    expired = end is not None and end <= now
    plan_id = store.current_plan or "free"
    status = store.subscription_status or "active"
    if expired and status in ("trial", "active"):
        plan_id = "free"
        status = "expired"
    plan = PLAN_CATALOG.get(plan_id, PLAN_CATALOG["free"])
    premium_active = plan_id == "pro" and status in ("active", "trial") and not expired
    return {
        "plan": plan_id,
        "plan_name": plan["name"],
        "subscription_status": status,
        "current_period_end": store.current_period_end.isoformat() if store.current_period_end else None,
        "trial_used": store.trial_used_at is not None,
        "premium_active": premium_active,
    }


def _page_args():
    raw_page = request.args.get("page", "1")
    raw_size = request.args.get("per_page", str(DEFAULT_PAGE_SIZE))
    try:
        page, per_page = int(raw_page), int(raw_size)
    except (TypeError, ValueError):
        return None, None, (Response.error_response(
            "VALIDATION_ERROR", "page and per_page must be integers", {}
        ), 400)
    if page < 1 or per_page < 1 or per_page > MAX_PAGE_SIZE:
        return None, None, (Response.error_response(
            "VALIDATION_ERROR", "page must be positive and per_page must be between 1 and 100",
            {"page": raw_page, "per_page": raw_size},
        ), 400)
    return page, per_page, None


def _pagination(query, page, per_page, serialize):
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


class PlatformAdminService:
    @staticmethod
    def overview():
        now = datetime.now(timezone.utc)
        since_30_days = now - timedelta(days=30)
        users_total = db.session.query(func.count(User.id)).scalar() or 0
        stores_total = db.session.query(func.count(Store.id)).scalar() or 0
        new_users_30d = User.query.filter(User.created_at >= since_30_days.replace(tzinfo=None)).count()
        new_stores_30d = Store.query.filter(Store.created_at >= since_30_days.replace(tzinfo=None)).count()

        # Count only Pro subscriptions whose period has not expired. The
        # explicit period-end condition avoids treating stale Pro rows as paid.
        active_premium = Store.query.filter(
            Store.current_plan == "pro",
            Store.subscription_status == "active",
            or_(Store.current_period_end.is_(None), Store.current_period_end > now),
        ).count()
        active_trials = Store.query.filter(
            Store.current_plan == "pro",
            Store.subscription_status == "trial",
            or_(Store.current_period_end.is_(None), Store.current_period_end > now),
        ).count()
        free_stores = Store.query.filter(
            or_(Store.current_plan == "free", Store.current_plan.is_(None))
        ).count()
        sales_30d = db.session.query(func.count(Sale.id)).filter(
            Sale.created_at >= since_30_days
        ).scalar() or 0

        return Response.success_response({
            "generated_at": now.isoformat(),
            "metrics": {
                "users_total": users_total,
                "stores_total": stores_total,
                "active_premium_stores": active_premium,
                "active_trial_stores": active_trials,
                "free_stores": free_stores,
                "new_users_last_30_days": new_users_30d,
                "new_stores_last_30_days": new_stores_30d,
                "sales_last_30_days": sales_30d,
            },
            "definitions": {
                "active_premium_stores": "Stores on the Pro plan with active status and no expired current_period_end. Trial stores are counted separately.",
                "active_trial_stores": "Pro stores with trial status and no expired current_period_end.",
                "free_stores": "Stores whose stored current_plan is free or null; expired Pro rows are not silently rewritten and are reported separately by subscription details.",
                "new_users_last_30_days": "Users created in the rolling 30 days before generated_at.",
                "new_stores_last_30_days": "Stores created in the rolling 30 days before generated_at.",
                "sales_last_30_days": "Persisted sales created in the rolling 30 days before generated_at; this is transaction count, not revenue.",
            },
        }, "PLATFORM_OVERVIEW_RETRIEVED"), 200

    @staticmethod
    def list_users():
        page, per_page, error = _page_args()
        if error:
            return error
        query = db.session.query(User)
        search = request.args.get("search", "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(User.name.ilike(pattern), User.email.ilike(pattern)))
        has_store = request.args.get("has_store")
        if has_store not in (None, "true", "false"):
            return Response.error_response(
                "VALIDATION_ERROR", "has_store must be true or false", {"has_store": has_store}
            ), 400
        if has_store == "true":
            query = query.filter(User.id.in_(db.session.query(Store.user_id)))
        elif has_store == "false":
            query = query.filter(~User.id.in_(db.session.query(Store.user_id)))

        plan = request.args.get("plan")
        if plan:
            if plan not in PLAN_CATALOG:
                return Response.error_response("VALIDATION_ERROR", "Unknown plan filter", {"plan": plan}), 400
            matching_users = db.session.query(Store.user_id).filter(Store.current_plan == plan)
            query = query.filter(User.id.in_(matching_users))

        sort_fields = {"created_at": User.created_at, "name": User.name, "email": User.email, "id": User.id}
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        if sort_by not in sort_fields or sort_order not in ("asc", "desc"):
            return Response.error_response("VALIDATION_ERROR", "Unsupported sort_by or sort_order", {
                "sort_by": sort_by, "sort_order": sort_order,
            }), 400
        column = sort_fields[sort_by]
        query = query.order_by(column.asc() if sort_order == "asc" else column.desc(), User.id.asc())

        def serialize(user):
            stores = Store.query.filter_by(user_id=user.id).order_by(Store.id.asc()).all()
            now = datetime.now(timezone.utc)
            return {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "created_at": user.created_at.isoformat(),
                "stores": [{
                    "id": store.id,
                    "name": store.name,
                    "subscription": _subscription(store, now),
                } for store in stores],
                "stores_count": len(stores),
            }

        return Response.success_response(
            _pagination(query, page, per_page, serialize), "PLATFORM_USERS_RETRIEVED"
        ), 200

    @staticmethod
    def user_detail(user_id):
        user = db.session.get(User, user_id)
        if not user:
            return Response.error_response("USER_NOT_FOUND", "User not found", {}), 404
        now = datetime.now(timezone.utc)
        stores = Store.query.filter_by(user_id=user.id).order_by(Store.id.asc()).all()
        store_items = []
        for store in stores:
            subscription = _subscription(store, now)
            last_sale = db.session.query(func.max(Sale.created_at)).filter(
                Sale.store_id == store.id
            ).scalar()
            store_items.append({
                "id": store.id,
                "name": store.name,
                "created_at": store.created_at.isoformat(),
                "subscription": subscription,
                "activity": {
                    "products_count": Product.query.filter_by(store_id=store.id).count(),
                    "customers_count": Customer.query.filter_by(store_id=store.id).count(),
                    "sales_count": Sale.query.filter_by(store_id=store.id).count(),
                    "last_sale_at": last_sale.isoformat() if last_sale else None,
                },
            })
        return Response.success_response({
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "registered_at": user.created_at.isoformat(),
                "stores": store_items,
                "stores_count": len(store_items),
            }
        }, "PLATFORM_USER_RETRIEVED"), 200

    @staticmethod
    def list_stores():
        page, per_page, error = _page_args()
        if error:
            return error
        query = db.session.query(Store)
        search = request.args.get("search", "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.join(User, User.id == Store.user_id).filter(
                or_(Store.name.ilike(pattern), User.name.ilike(pattern), User.email.ilike(pattern))
            )
        plan = request.args.get("plan")
        if plan:
            if plan not in PLAN_CATALOG:
                return Response.error_response("VALIDATION_ERROR", "Unknown plan filter", {"plan": plan}), 400
            query = query.filter(Store.current_plan == plan)
        status = request.args.get("subscription_status")
        if status:
            if status not in ("active", "trial", "expired"):
                return Response.error_response("VALIDATION_ERROR", "Unsupported subscription_status", {
                    "subscription_status": status,
                }), 400
            if status == "expired":
                now = datetime.now(timezone.utc)
                query = query.filter(or_(
                    Store.subscription_status == "expired",
                    and_(
                        Store.current_period_end.is_not(None),
                        Store.current_period_end <= now,
                    ),
                ))
            else:
                query = query.filter(Store.subscription_status == status)
        sort_fields = {"created_at": Store.created_at, "name": Store.name, "id": Store.id, "plan": Store.current_plan}
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        if sort_by not in sort_fields or sort_order not in ("asc", "desc"):
            return Response.error_response("VALIDATION_ERROR", "Unsupported sort_by or sort_order", {
                "sort_by": sort_by, "sort_order": sort_order,
            }), 400
        column = sort_fields[sort_by]
        query = query.order_by(column.asc() if sort_order == "asc" else column.desc(), Store.id.asc())

        def serialize(store):
            owner = db.session.get(User, store.user_id)
            return {
                "id": store.id,
                "name": store.name,
                "registered_at": store.created_at.isoformat(),
                "owner": {
                    "id": owner.id,
                    "name": owner.name,
                    "email": owner.email,
                } if owner else None,
                "subscription": _subscription(store, datetime.now(timezone.utc)),
            }

        return Response.success_response(
            _pagination(query, page, per_page, serialize), "PLATFORM_STORES_RETRIEVED"
        ), 200

    @staticmethod
    def store_detail(store_id):
        store = db.session.get(Store, store_id)
        if not store:
            return Response.error_response("STORE_NOT_FOUND", "Store not found", {}), 404
        owner = db.session.get(User, store.user_id)
        last_sale = db.session.query(func.max(Sale.created_at)).filter(Sale.store_id == store.id).scalar()
        return Response.success_response({
            "store": {
                "id": store.id,
                "name": store.name,
                "registered_at": store.created_at.isoformat(),
                "owner": {
                    "id": owner.id,
                    "name": owner.name,
                    "email": owner.email,
                } if owner else None,
                "subscription": _subscription(store, datetime.now(timezone.utc)),
                "activity": {
                    "products_count": Product.query.filter_by(store_id=store.id).count(),
                    "customers_count": Customer.query.filter_by(store_id=store.id).count(),
                    "sales_count": Sale.query.filter_by(store_id=store.id).count(),
                    "open_alerts_count": Alert.query.filter_by(store_id=store.id, is_resolved=False).count(),
                    "last_sale_at": last_sale.isoformat() if last_sale else None,
                },
            }
        }, "PLATFORM_STORE_RETRIEVED"), 200
