from flask import Blueprint

from server.app.middleware.auth_middleware import require_session
from server.app.services.subscription_service import SubscriptionService

subscription_bp = Blueprint("subscriptions", __name__)
subscriptions = SubscriptionService()


@subscription_bp.route("/plans", methods=["GET"])
def list_plans():
    return subscriptions.list_plans()


@subscription_bp.route("", methods=["GET"])
@require_session
def current_subscription():
    return subscriptions.current_subscription()


@subscription_bp.route("/change", methods=["POST"])
@require_session
def request_plan_change():
    return subscriptions.request_change()


@subscription_bp.route("/change/cancel", methods=["POST"])
@require_session
def cancel_plan_change():
    return subscriptions.cancel_pending_change()


@subscription_bp.route("/trial", methods=["POST"])
@require_session
def start_trial():
    return subscriptions.start_trial()
