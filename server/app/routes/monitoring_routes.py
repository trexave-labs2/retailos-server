from flask import Blueprint, g

from server.app.middleware.auth_middleware import require_platform_admin
from server.app.services.monitoring_service import MonitoringService


monitoring_bp = Blueprint("platform_monitoring", __name__)


@monitoring_bp.get("/monitoring/summary")
@require_platform_admin
def monitoring_summary():
    return MonitoringService.summary()


@monitoring_bp.get("/monitors")
@require_platform_admin
def list_monitors():
    return MonitoringService.list_monitors()


@monitoring_bp.post("/monitors")
@require_platform_admin
def create_monitor():
    return MonitoringService.create_monitor(g.platform_admin.id)


@monitoring_bp.patch("/monitors/<int:monitor_id>")
@require_platform_admin
def update_monitor(monitor_id):
    return MonitoringService.update_monitor(g.platform_admin.id, monitor_id)


@monitoring_bp.post("/monitors/<int:monitor_id>/checks")
@require_platform_admin
def manual_check(monitor_id):
    return MonitoringService.manual_check(g.platform_admin.id, monitor_id)


@monitoring_bp.get("/monitors/<int:monitor_id>/checks")
@require_platform_admin
def check_history(monitor_id):
    return MonitoringService.check_history(monitor_id)


@monitoring_bp.get("/incidents")
@require_platform_admin
def list_incidents():
    return MonitoringService.list_incidents()
