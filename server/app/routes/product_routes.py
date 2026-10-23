# server/app/routes/product_routes.py
from flask import Blueprint

from server.app.middleware.auth_middleware import require_session
from server.app.services.product_service import ProductService
from server.app.services.csv_import_service import CsvImportService


product = ProductService()
csv_import = CsvImportService()
product_bp = Blueprint("product", __name__)


@product_bp.route("/create", methods=["POST"])
@require_session
def create_product():
    return product.create_product()


@product_bp.route("/get", methods=["GET"])
@require_session
def get_product():
    return product.get_product()


@product_bp.route("/list", methods=["GET"])
@require_session
def list_products():
    return product.list_products()


@product_bp.route("/update", methods=["PATCH"])
@require_session
def update_product():
    return product.update_product()


@product_bp.route("/delete", methods=["DELETE"])
@require_session
def delete_product():
    return product.delete_product()


@product_bp.route("/adjust", methods=["POST"])
@require_session
def adjust_stock():
    return product.adjust_stock()


@product_bp.route("/movements", methods=["GET"])
@require_session
def list_inventory_movements():
    return product.list_inventory_movements()


@product_bp.route("/import", methods=["POST"])
@require_session
def import_products_csv():
    return csv_import.import_csv()
