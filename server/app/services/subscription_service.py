"""Server-authoritative subscription plans and entitlement checks.

Prices and limits live in PLAN_CATALOG. Paid plan activation is deliberately
not performed by a client request; it requires a verified billing integration.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import request
from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.store import Store
from server.app.models.subscription_change import SubscriptionChange
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store


BILLING_INTERVALS = {
    "month": {"amount": 4999, "duration_days": 30},
    "quarter": {"amount": 13500, "duration_days": 90},
    "year": {"amount": 49990, "duration_days": 365},
}
TRIAL_DURATION_DAYS = 14
SALES_LIMIT_GRACE_HOURS = 24
WARNING_THRESHOLD_PERCENT = 80

PLAN_CATALOG = {
    "free": {
        "id": "free",
        "name": "Free",
        "price": {"amount": 0, "currency": "NGN", "interval": "month"},
        "billing_options": [
            {"interval": "month", "amount": 0, "currency": "NGN"}
        ],
        "limits": {
            "products": 100,
            "customers": 100,
            "sales_per_month": 500,
            "staff_accounts": 0,
        },
        "features": {
            "inventory": True,
            "sales_and_checkout": True,
            "dashboard": True,
            "advanced_reporting": False,
            "data_export": False,
        },
        "reporting": {
            "today_and_month_sales": True,
            "current_stock_levels": True,
            "profit_per_product": False,
            "top_and_slow_sellers": False,
            "custom_date_ranges": False,
            "low_stock_alerts": False,
            "staff_sales_performance": False,
        },
        "support": "standard",
        "trial": {"pro_trial_available_once": True, "duration_days": TRIAL_DURATION_DAYS},
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "price": {"amount": BILLING_INTERVALS["month"]["amount"], "currency": "NGN", "interval": "month"},
        "billing_options": [
            {"interval": interval, "amount": details["amount"], "currency": "NGN"}
            for interval, details in BILLING_INTERVALS.items()
        ],
        "limits": {
            "products": None,
            "customers": None,
            "sales_per_month": None,
            "staff_accounts": 5,
        },
        "features": {
            "inventory": True,
            "sales_and_checkout": True,
            "dashboard": True,
            "advanced_reporting": True,
            "data_export": True,
        },
        "reporting": {
            "today_and_month_sales": True,
            "current_stock_levels": True,
            "profit_per_product": True,
            "top_and_slow_sellers": True,
            "custom_date_ranges": True,
            "low_stock_alerts": True,
            "staff_sales_performance": True,
        },
        "support": "priority_whatsapp",
        "trial": {"pro_trial_available_once": False, "duration_days": 0},
        "fair_use": "Unlimited usage is subject to fair-use review at exceptionally high volumes.",
    },
}

def current_period_start(now=None):
    """Return the UTC instant at the start of the current Africa/Lagos month."""
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo("Africa/Lagos"))
    local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


class SubscriptionService:
    @staticmethod
    def _store_data(store):
        plan = SubscriptionService._effective_plan(store)
        return {
            "store_id": store.id,
            "plan": plan,
            "subscription_status": store.subscription_status,
            "current_period_end": (
                store.current_period_end.isoformat()
                if store.current_period_end else None
            ),
            "trial_used": store.trial_used_at is not None,
            "usage": SubscriptionService._usage_for_store(store),
        }

    @staticmethod
    def _effective_plan(store, now=None):
        now = now or datetime.now(timezone.utc)
        period_end = store.current_period_end
        if period_end is not None and period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        if (
            store.subscription_status == "trial"
            and period_end is not None
            and period_end <= now
        ):
            store.current_plan = "free"
            store.subscription_status = "expired"
            db.session.commit()
        return PLAN_CATALOG.get(store.current_plan, PLAN_CATALOG["free"])

    @staticmethod
    def _usage_for_store(store):
        plan = SubscriptionService._effective_plan(store)
        start = current_period_start()
        counts = {
            "products": Product.query.filter_by(store_id=store.id).count(),
            "customers": Customer.query.filter_by(store_id=store.id).count(),
            "sales_per_month": Sale.query.filter(
                Sale.store_id == store.id, Sale.created_at >= start
            ).count(),
        }
        usage = {}
        for resource, current in counts.items():
            limit = plan["limits"].get(resource)
            warning_at = int(limit * WARNING_THRESHOLD_PERCENT / 100) if limit else None
            usage[resource] = {
                "current": current,
                "limit": limit,
                "warning_at": warning_at,
                "warning": bool(limit and current >= warning_at),
            }
        return usage

    def start_trial(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "Payload must be a JSON object", {}
            ), 400
        store = get_authorized_store(data.get("store_id"))
        if not store:
            return Response.error_response(
                "STORE_ACCESS_DENIED", "You do not have access to this store", {}
            ), 403
        now = datetime.now(timezone.utc)
        self._effective_plan(store, now)
        if store.trial_used_at is not None:
            return Response.error_response(
                "TRIAL_ALREADY_USED", "This store has already used its Pro trial", {}
            ), 409
        if store.current_plan == "pro":
            return Response.error_response(
                "PRO_ALREADY_ACTIVE", "Pro is already active for this store", {}
            ), 409
        store.trial_used_at = now
        store.current_plan = "pro"
        store.subscription_status = "trial"
        from datetime import timedelta
        store.current_period_end = now + timedelta(days=TRIAL_DURATION_DAYS)
        db.session.commit()
        return Response.success_response(
            {
                "subscription": self._store_data(store),
                "trial": {"duration_days": TRIAL_DURATION_DAYS, "ends_at": store.current_period_end.isoformat()},
            },
            "PRO_TRIAL_STARTED",
        ), 200

    def list_plans(self):
        return Response.success_response(
            {"plans": list(PLAN_CATALOG.values())},
            "SUBSCRIPTION_PLANS_RETRIEVED",
        ), 200

    def current_subscription(self):
        store_id = request.args.get("store_id", type=int)
        store = get_authorized_store(store_id)
        if not store:
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403

        pending = SubscriptionChange.query.filter_by(
            store_id=store.id, status="pending"
        ).order_by(SubscriptionChange.created_at.desc()).first()
        self._effective_plan(store)
        data = self._store_data(store)
        data["pending_change"] = ({
            "id": pending.id,
            "target_plan": pending.target_plan,
            "status": pending.status,
            "created_at": pending.created_at.isoformat(),
            "payment_required": pending.target_plan != "free",
        } if pending else None)
        return Response.success_response(
            {"subscription": data}, "SUBSCRIPTION_RETRIEVED"
        ), 200

    def request_change(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "Payload must be a JSON object", {}
            ), 400

        store_id = data.get("store_id")
        target_plan = data.get("plan")
        store = get_authorized_store(store_id)
        if not store:
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403
        if target_plan not in PLAN_CATALOG:
            return Response.error_response(
                "INVALID_PLAN", "Select a plan from the published plan catalog",
                {"plan": "Unknown plan"},
            ), 400
        self._effective_plan(store)
        billing_interval = data.get("billing_interval", "month")
        if target_plan == "pro" and billing_interval not in BILLING_INTERVALS:
            return Response.error_response(
                "INVALID_BILLING_INTERVAL",
                "Choose month, quarter, or year",
                {"billing_interval": "Unsupported billing interval"},
            ), 400
        if target_plan == "free":
            billing_interval = "month"
        if target_plan == store.current_plan and not (
            target_plan == "pro" and store.subscription_status == "trial"
        ):
            return Response.error_response(
                "PLAN_ALREADY_ACTIVE", "This plan is already active", {}
            ), 409

        existing = SubscriptionChange.query.filter_by(
            store_id=store.id, status="pending"
        ).first()
        if existing:
            if existing.target_plan == target_plan:
                return Response.success_response(
                    {
                        "change": {
                            "id": existing.id,
                            "target_plan": existing.target_plan,
                            "status": existing.status,
                            "payment_required": target_plan != "free",
                            "activation": "not_activated",
                        }
                    },
                    "PLAN_CHANGE_ALREADY_PENDING",
                ), 200
            return Response.error_response(
                "PLAN_CHANGE_PENDING",
                "Resolve the existing pending plan change before requesting another",
                {"pending_change_id": existing.id},
            ), 409

        change = SubscriptionChange(
            store_id=store.id,
            requested_by=store.user_id,
            source_plan=store.current_plan,
            target_plan=target_plan,
            billing_interval=billing_interval,
            price_amount=(BILLING_INTERVALS[billing_interval]["amount"] if target_plan == "pro" else 0),
            status="pending",
        )
        db.session.add(change)
        db.session.commit()

        # Never grant paid access based on a client claim. A verified payment
        # provider integration must confirm the payment before changing Store.
        return Response.success_response(
            {
                "change": {
                    "id": change.id,
                    "source_plan": change.source_plan,
                    "target_plan": change.target_plan,
                    "status": change.status,
                    "billing_interval": billing_interval,
                    "price": {
                        "amount": change.price_amount,
                        "currency": "NGN",
                        "interval": billing_interval,
                    },
                    "payment_required": change.price_amount > 0,
                    "activation": "not_activated",
                },
                "billing": {
                    "status": "not_configured",
                    "message": "Paid plan activation requires verified billing confirmation.",
                },
            },
            "PLAN_CHANGE_PENDING",
        ), 202

    def cancel_pending_change(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR", "Payload must be a JSON object", {}
            ), 400
        store_id = data.get("store_id")
        change_id = data.get("change_id")
        store = get_authorized_store(store_id)
        if not store:
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403
        change = SubscriptionChange.query.filter_by(
            id=change_id, store_id=store.id, status="pending"
        ).first()
        if not change:
            return Response.error_response(
                "PENDING_CHANGE_NOT_FOUND",
                "No matching pending plan change was found",
                {},
            ), 404
        change.status = "cancelled"
        change.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return Response.success_response(
            {"change_id": change.id, "status": change.status},
            "PLAN_CHANGE_CANCELLED",
        ), 200

    @staticmethod
    def check_feature(store_id, feature):
        """Return a standard API error tuple when a store lacks a feature."""
        store = db.session.get(Store, store_id)
        if not store:
            return None
        plan = SubscriptionService._effective_plan(store)
        if plan.get("features", {}).get(feature, False):
            return None
        return Response.error_response(
            "FEATURE_NOT_AVAILABLE",
            f"The {feature.replace('_', ' ')} feature is not available on the {plan['name']} plan",
            {
                "feature": feature,
                "current_plan": plan["id"],
                "upgrade_available": plan["id"] != "pro",
            },
        ), 403

    @staticmethod
    def check_limit(store_id, resource):
        """Return a standard API error tuple when a Free-plan limit is reached."""
        store = db.session.get(Store, store_id)
        if not store:
            return None
        plan = SubscriptionService._effective_plan(store)
        limit = plan["limits"].get(resource)
        if limit is None:
            return None

        if resource == "products":
            count = Product.query.filter_by(store_id=store_id).count()
        elif resource == "customers":
            count = Customer.query.filter_by(store_id=store_id).count()
        elif resource == "sales_per_month":
            start = current_period_start()
            count = Sale.query.filter(
                Sale.store_id == store_id,
                Sale.created_at >= start,
            ).count()
            if count >= limit:
                now = datetime.now(timezone.utc)
                period_start = current_period_start(now)
                grace_started = store.sales_limit_grace_started_at
                if grace_started is not None and grace_started.tzinfo is None:
                    grace_started = grace_started.replace(tzinfo=timezone.utc)
                if grace_started is not None and grace_started < period_start:
                    store.sales_limit_grace_started_at = None
                    grace_started = None
                if grace_started is None:
                    store.sales_limit_grace_started_at = now
                    db.session.commit()
                    grace_started = now
                if grace_started is not None and (now - grace_started).total_seconds() < SALES_LIMIT_GRACE_HOURS * 3600:
                    return None
        else:
            return None

        if count < limit:
            return None
        return Response.error_response(
            "PLAN_LIMIT_REACHED",
            f"The {plan['name']} plan limit for {resource} has been reached",
            {
                "resource": resource,
                "limit": limit,
                "current": count,
                "current_plan": plan["id"],
                "upgrade_available": plan["id"] != "pro",
            },
        ), 403
