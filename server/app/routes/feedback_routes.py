from flask import Blueprint
from flask_limiter.util import get_remote_address

from server.app.extensions import limiter
from server.app.services.feedback_service import FeedbackService


feedback_bp = Blueprint("feedback", __name__)
feedback_service = FeedbackService()


@feedback_bp.route("", methods=["POST"])
@limiter.limit("5 per hour", key_func=get_remote_address)
def submit_feedback():
    return feedback_service.submit()
