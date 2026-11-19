from flask import Blueprint

from server.app.middleware.auth_middleware import require_platform_admin, require_super_admin
from server.app.services.platform_admin_service import PlatformAdminService
from server.app.services.platform_admin_subscription_service import PlatformAdminSubscriptionService
from server.app.services.platform_admin_support_service import PlatformAdminSupportService
from server.app.services.platform_admin_settings_service import PlatformAdminSettingsService
from server.app.services.platform_admin_roles_service import PlatformAdminRolesService


platform_admin_bp = Blueprint("platform_admin", __name__)


# Existing platform-admin APIs. Keep these routes registered when adding new
# admin settings; replacing the route module would silently remove them.
@platform_admin_bp.get("/subscriptions")
@require_platform_admin
def list_subscriptions():
    return PlatformAdminSubscriptionService.list_subscriptions()


@platform_admin_bp.get("/subscription-requests")
@require_platform_admin
def list_subscription_requests():
    return PlatformAdminSubscriptionService.list_subscription_requests()


@platform_admin_bp.get("/feedback")
@require_platform_admin
def list_feedback():
    return PlatformAdminSupportService.list_feedback()


@platform_admin_bp.get("/feedback/<int:feedback_id>")
@require_platform_admin
def feedback_detail(feedback_id):
    return PlatformAdminSupportService.feedback_detail(feedback_id)


@platform_admin_bp.patch("/feedback/<int:feedback_id>/status")
@require_platform_admin
def update_feedback_status(feedback_id):
    return PlatformAdminSupportService.update_feedback_status(feedback_id)


@platform_admin_bp.get("/audit-logs")
@require_platform_admin
def list_platform_audit_logs():
    return PlatformAdminSupportService.list_audit_logs()


# New self-service administrator settings APIs.
@platform_admin_bp.get("/profile")
@require_platform_admin
def get_profile():
    return PlatformAdminSettingsService.get_profile()


@platform_admin_bp.patch("/profile")
@require_platform_admin
def update_profile():
    return PlatformAdminSettingsService.update_profile()


@platform_admin_bp.post("/sessions/revoke-all")
@require_platform_admin
def revoke_all_sessions():
    return PlatformAdminSettingsService.revoke_all_sessions()


@platform_admin_bp.get("/overview")
@require_platform_admin
def overview():
    return PlatformAdminService.overview()


@platform_admin_bp.get("/users")
@require_platform_admin
def list_users():
    return PlatformAdminService.list_users()


@platform_admin_bp.get("/users/<int:user_id>")
@require_platform_admin
def user_detail(user_id):
    return PlatformAdminService.user_detail(user_id)


@platform_admin_bp.get("/stores")
@require_platform_admin
def list_stores():
    return PlatformAdminService.list_stores()


@platform_admin_bp.get("/stores/<int:store_id>")
@require_platform_admin
def store_detail(store_id):
    return PlatformAdminService.store_detail(store_id)


@platform_admin_bp.get("/administrators")
@require_super_admin
def list_administrators():
    return PlatformAdminRolesService.list_administrators()


@platform_admin_bp.put("/users/<int:user_id>/admin-role")
@require_super_admin
def set_admin_role(user_id):
    return PlatformAdminRolesService.set_admin_role(user_id)
