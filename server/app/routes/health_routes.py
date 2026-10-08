from flask import Blueprint

from server.app.utils.response import Response


health_bp = Blueprint("health", __name__)


@health_bp.route("", methods=["GET"])
def health():
    return Response.success_response(
        {
            "status": "ok",
            "ready": True
        },
        "HEALTHY"
    ), 200
