from flask import request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.category import Category
from server.app.models.product import Product
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.validators import validate_required_string


class CategoryService:
    def _category_data(self, category):
        return {
            "id": category.id,
            "store_id": category.store_id,
            "name": category.name,
            "item_type": category.item_type,
        }

    def _validate_name(self, name):
        error = validate_required_string(name, "Category name")
        if error:
            return error
        if len(name.strip()) > 60:
            return "Category name must be 60 characters or fewer"
        return None

    def list_categories(self):
        store_id = request.args.get("store_id", type=int)
        item_type = request.args.get("item_type")
        if not get_authorized_store(store_id):
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        if item_type is not None and item_type not in Category.ITEM_TYPES:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {
                "item_type": "Item type must be one of: Physical, Service"
            }), 400
        query = Category.query.filter_by(store_id=store_id)
        if item_type:
            query = query.filter_by(item_type=item_type)
        categories = query.order_by(Category.item_type.asc(), Category.name.asc()).all()
        return Response.success_response({"categories": [self._category_data(c) for c in categories]}, "CATEGORIES_RETRIEVED"), 200

    def create_category(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        store_id = data.get("store_id")
        name = data.get("name")
        item_type = data.get("item_type")
        fields = {}
        if not isinstance(store_id, int) or isinstance(store_id, bool):
            fields["store_id"] = "Store ID must be an integer"
        elif not get_authorized_store(store_id):
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        name_error = self._validate_name(name)
        if name_error:
            fields["name"] = name_error
        if item_type not in Category.ITEM_TYPES:
            fields["item_type"] = "Item type must be one of: Physical, Service"
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400
        normalized = name.strip()
        duplicate = Category.query.filter(
            Category.store_id == store_id,
            Category.item_type == item_type,
            func.lower(Category.name) == normalized.lower(),
        ).first()
        if duplicate:
            return Response.error_response("CATEGORY_ALREADY_EXISTS", "A category with this name already exists for this item type", {}), 409
        category = Category(store_id=store_id, name=normalized, item_type=item_type)
        db.session.add(category)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return Response.error_response("CATEGORY_ALREADY_EXISTS", "A category with this name already exists for this item type", {}), 409
        return Response.success_response({"category": self._category_data(category)}, "CATEGORY_CREATED"), 201

    def update_category(self):
        from flask import request
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        store_id, category_id = data.get("store_id"), data.get("id")
        if not isinstance(store_id, int) or isinstance(store_id, bool) or not isinstance(category_id, int) or isinstance(category_id, bool):
            return Response.error_response("VALIDATION_ERROR", "Store ID and category ID must be integers", {}), 400
        if not get_authorized_store(store_id):
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        category = Category.query.filter_by(id=category_id, store_id=store_id).first()
        if not category:
            return Response.error_response("CATEGORY_NOT_FOUND", "Category not found", {}), 404
        fields = {}
        if "name" in data:
            error = self._validate_name(data["name"])
            if error:
                fields["name"] = error
        if "item_type" in data and data["item_type"] != category.item_type:
            fields["item_type"] = "Category item type cannot be changed; create a category for the other item type"
        if fields:
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", fields), 400
        if "name" in data:
            name = data["name"].strip()
            duplicate = Category.query.filter(
                Category.store_id == store_id,
                Category.item_type == category.item_type,
                Category.id != category.id,
                func.lower(Category.name) == name.lower(),
            ).first()
            if duplicate:
                return Response.error_response("CATEGORY_ALREADY_EXISTS", "A category with this name already exists for this item type", {}), 409
            category.name = name
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return Response.error_response("CATEGORY_ALREADY_EXISTS", "A category with this name already exists for this item type", {}), 409
        return Response.success_response({"category": self._category_data(category)}, "CATEGORY_UPDATED"), 200

    def delete_category(self):
        from flask import request
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"}), 400
        store_id, category_id = data.get("store_id"), data.get("id")
        if not isinstance(store_id, int) or isinstance(store_id, bool) or not isinstance(category_id, int) or isinstance(category_id, bool):
            return Response.error_response("VALIDATION_ERROR", "Store ID and category ID must be integers", {}), 400
        if not get_authorized_store(store_id):
            return Response.error_response("STORE_ACCESS_DENIED", "You do not have access to this store", {}), 403
        category = Category.query.filter_by(id=category_id, store_id=store_id).first()
        if not category:
            return Response.error_response("CATEGORY_NOT_FOUND", "Category not found", {}), 404
        if Product.query.filter_by(store_id=store_id, category_id=category_id).first():
            return Response.error_response("CATEGORY_IN_USE", "Category cannot be deleted while items are assigned to it", {}), 409
        db.session.delete(category)
        db.session.commit()
        return Response.success_response(message="CATEGORY_DELETED"), 200
