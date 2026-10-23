from flask import Blueprint, request
from server.app.middleware.auth_middleware import require_session
from server.app.services.insight_service import InsightService

insight = InsightService()
dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("", methods=["GET"])
@require_session
def dashboard():
    return insight.dashboard(request.args.get("store_id", type=int))


@dashboard_bp.route("/rankings", methods=["GET"])
@require_session
def dashboard_rankings():
    return insight.dashboard_rankings(request.args.get("store_id", type=int))


@dashboard_bp.route("/time-series", methods=["GET"])
@require_session
def dashboard_time_series():
    return insight.dashboard_time_series(request.args.get("store_id", type=int))


@dashboard_bp.route("/daily-summary", methods=["GET"])
@require_session
def daily_summary():
    return insight.daily_summary(request.args.get("store_id", type=int))


@dashboard_bp.route("/daily-brief", methods=["GET"])
@require_session
def daily_brief():
    return insight.daily_brief(request.args.get("store_id", type=int))
