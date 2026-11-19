from functools import wraps

from flask import current_app, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from server.app.models.user import PLATFORM_ADMIN_ROLES, SUPER_ADMIN_ROLE, User
from server.app.utils.response import Response

ADMIN_SESSION_COOKIE = "retailos_admin_session"
ADMIN_SESSION_TTL_SECONDS = 12 * 60 * 60
ADMIN_SESSION_SALT = "retailos-platform-admin-session-v1"


def require_session(view_function):
    """Validate the RetailOS Client's ordinary signed session cookie."""
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        from flask import session
        user_id = session.get("user_id")
        session_version = session.get("session_version")
        if not user_id or session_version is None:
            session.clear()
            return Response.error_response("SESSION_NOT_FOUND", "Session not found", {"retryable": False}), 401
        user = User.query.filter_by(id=user_id).first()
        if not user or user.session_version != session_version:
            session.clear()
            return Response.error_response("SESSION_INVALIDATED", "Session is no longer valid. Please log in again.", {"retryable": False}), 401
        if user.account_status != "active":
            session.clear()
            return Response.error_response("ACCOUNT_CLOSURE_PENDING", "This account is closed to normal access while deletion is pending.", {"retryable": False}), 403
        return view_function(*args, **kwargs)
    return wrapper


def _load_platform_admin():
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        return None
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=ADMIN_SESSION_SALT)
    try:
        payload = serializer.loads(token, max_age=ADMIN_SESSION_TTL_SECONDS)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    user = User.query.filter_by(id=payload.get("uid")).first()
    if (not user or user.platform_role not in PLATFORM_ADMIN_ROLES
            or user.account_status != "active" or not user.admin_password_hash
            or user.admin_session_version != payload.get("sv")):
        return None
    return user


def require_platform_admin(view_function):
    """Require the independent platform-admin cookie, never the Client session."""
    @wraps(view_function)
    def platform_admin_wrapper(*args, **kwargs):
        user = _load_platform_admin()
        if not user:
            return Response.error_response("PLATFORM_ADMIN_SESSION_REQUIRED", "A valid platform administrator session is required", {}), 401
        g.platform_admin = user
        return view_function(*args, **kwargs)
    return platform_admin_wrapper


def require_super_admin(view_function):
    @wraps(view_function)
    def super_admin_wrapper(*args, **kwargs):
        user = _load_platform_admin()
        if not user:
            return Response.error_response("PLATFORM_ADMIN_SESSION_REQUIRED", "A valid platform administrator session is required", {}), 401
        if user.platform_role != SUPER_ADMIN_ROLE:
            return Response.error_response("SUPER_ADMIN_REQUIRED", "Super administrator permission is required", {}), 403
        g.platform_admin = user
        return view_function(*args, **kwargs)
    return super_admin_wrapper


def admin_session_response(response, user):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=ADMIN_SESSION_SALT)
    token = serializer.dumps({"uid": user.id, "sv": user.admin_session_version})
    response.set_cookie(
        ADMIN_SESSION_COOKIE, token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        partitioned=current_app.config.get("SESSION_COOKIE_PARTITIONED", False),
        path="/",
    )
    return response


def clear_admin_session(response):
    response.delete_cookie(
        ADMIN_SESSION_COOKIE, path="/", httponly=True,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        partitioned=current_app.config.get("SESSION_COOKIE_PARTITIONED", False),
    )
    return response
