from functools import wraps
from flask import session
from server.app.models.user import User
from server.app.utils.response import Response

def require_session(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        session_version = session.get("session_version")
        if not user_id or session_version is None:
            session.clear()
            return Response.error_response(
                "SESSION_NOT_FOUND", "Session not found", {"retryable": False}
            ), 401

        user = User.query.filter_by(id=user_id).first()
        if not user or user.session_version != session_version:
            session.clear()
            return Response.error_response(
                "SESSION_INVALIDATED", "Session is no longer valid. Please log in again.", {"retryable": False}
            ), 401

        return view_function(*args, **kwargs)
    return wrapper
