from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.category_service import CategoryService

category_bp = Blueprint("categories", __name__)
category = CategoryService()


@category_bp.route("", methods=["GET"])
@require_session
def list_categories():
    return category.list_categories()


@category_bp.route("", methods=["POST"])
@require_session
def create_category():
    return category.create_category()


@category_bp.route("", methods=["PATCH"])
@require_session
def update_category():
    return category.update_category()


@category_bp.route("", methods=["DELETE"])
@require_session
def delete_category():
    return category.delete_category()
