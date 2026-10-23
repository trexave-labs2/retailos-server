import csv
import io
import re
from decimal import Decimal, InvalidOperation

from flask import request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.category import Category
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.services.alert_service import AlertService
from server.app.services.subscription_service import SubscriptionService
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.validators import validate_required_string


class CsvImportService:
    """Import product and service catalog rows from a UTF-8 CSV upload."""

    SUPPORTED_FIELDS = {
        "name", "price", "product_type", "category_id", "category",
        "base_unit", "opening_stock", "low_stock_threshold",
        "sku", "barcode", "cost_price",
    }
    MAX_FILE_BYTES = 2 * 1024 * 1024
    MAX_ROWS = 1000
    UNIT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,19}$")

    @staticmethod
    def _row_error(row_number, message, field=None):
        error = {"row": row_number, "message": message}
        if field:
            error["field"] = field
        return error

    @staticmethod
    def _parse_integer(value, field, allow_blank=False, default=None, minimum=0):
        if value is None or value.strip() == "":
            if allow_blank:
                return default, None
            return None, f"{field} is required"
        try:
            if not value.strip().isdigit():
                raise ValueError
            parsed = int(value.strip())
        except (ValueError, TypeError):
            return None, f"{field} must be an integer"
        if parsed < minimum:
            return None, f"{field} must be {minimum} or greater"
        return parsed, None

    @staticmethod
    def _parse_money(value, field, allow_blank=False):
        if value is None or value.strip() == "":
            if allow_blank:
                return None, None
            return None, f"{field} is required"
        try:
            parsed = Decimal(value)
            if (not parsed.is_finite() or parsed < 0
                    or parsed > Decimal("99999999.99")
                    or parsed.as_tuple().exponent < -2):
                raise InvalidOperation
            return parsed, None
        except (InvalidOperation, ValueError, TypeError):
            return None, f"{field} must be a non-negative amount with at most 2 decimal places"

    def import_csv(self):
        store_id = request.form.get("store_id", type=int)
        if not store_id:
            return Response.error_response(
                "VALIDATION_ERROR", "Store ID is required", {"store_id": "Store ID is required"}
            ), 400
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED", "You do not have access to this store", {}
            ), 403

        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return Response.error_response(
                "VALIDATION_ERROR", "Upload a CSV file using the 'file' field",
                {"file": "A CSV file is required"},
            ), 400
        if not upload.filename.lower().endswith(".csv"):
            return Response.error_response(
                "VALIDATION_ERROR", "Only CSV files are supported",
                {"file": "File extension must be .csv"},
            ), 400

        raw = upload.stream.read(self.MAX_FILE_BYTES + 1)
        if len(raw) > self.MAX_FILE_BYTES:
            return Response.error_response(
                "VALIDATION_ERROR", "CSV file exceeds the 2 MiB limit",
                {"file": "File is too large"},
            ), 413
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response.error_response(
                "VALIDATION_ERROR", "CSV must use UTF-8 encoding", {"file": "Invalid UTF-8"}
            ), 400

        try:
            reader = csv.DictReader(io.StringIO(text, newline=""))
            if not reader.fieldnames:
                return Response.error_response(
                    "VALIDATION_ERROR", "CSV must include a header row", {"file": "Header row is required"}
                ), 400
            headers = [header.strip() if header else "" for header in reader.fieldnames]
            if len(headers) != len(set(headers)):
                return Response.error_response(
                    "VALIDATION_ERROR", "CSV headers must be unique", {"headers": "Duplicate column names are not allowed"}
                ), 400
            if any(not header for header in headers):
                return Response.error_response(
                    "VALIDATION_ERROR", "CSV headers cannot be empty", {"headers": "Empty column name"}
                ), 400
            unknown = sorted(set(headers) - self.SUPPORTED_FIELDS)
            if unknown:
                return Response.error_response(
                    "VALIDATION_ERROR", "CSV contains unsupported columns",
                    {"headers": f"Unsupported columns: {', '.join(unknown)}"},
                ), 400
            if not {"name", "price"}.issubset(headers):
                return Response.error_response(
                    "VALIDATION_ERROR", "CSV must include name and price columns",
                    {"headers": "Required columns: name, price"},
                ), 400
            if "category" in headers and "category_id" in headers:
                return Response.error_response(
                    "VALIDATION_ERROR", "Use category or category_id, not both",
                    {"headers": "category and category_id are mutually exclusive"},
                ), 400
            rows = list(reader)
        except csv.Error:
            return Response.error_response(
                "VALIDATION_ERROR", "CSV could not be parsed", {"file": "Malformed CSV"}
            ), 400

        if not rows:
            return Response.error_response(
                "VALIDATION_ERROR", "CSV contains no records",
                {"file": "At least one data row is required"},
            ), 400
        if len(rows) > self.MAX_ROWS:
            return Response.error_response(
                "VALIDATION_ERROR", "CSV exceeds the 1000-record limit", {"file": "Too many records"}
            ), 400

        created = []
        errors = []
        seen_skus = set()
        seen_barcodes = set()
        for index, raw_row in enumerate(rows, start=2):
            row = {
                (key.strip() if key else key): (value.strip() if isinstance(value, str) else value)
                for key, value in raw_row.items() if key is not None
            }
            if None in raw_row:
                errors.append(self._row_error(index, "Row has more values than the header"))
                continue

            name = row.get("name", "")
            name_error = validate_required_string(name, "Name")
            if name_error:
                errors.append(self._row_error(index, name_error, "name"))
                continue

            price, error = self._parse_money(row.get("price", ""), "Price")
            if error:
                errors.append(self._row_error(index, error, "price"))
                continue
            cost_price, error = self._parse_money(row.get("cost_price", ""), "Cost price", allow_blank=True)
            if error:
                errors.append(self._row_error(index, error, "cost_price"))
                continue

            product_type = row.get("product_type") or "Physical"
            if product_type not in Product.PRODUCT_TYPES:
                errors.append(self._row_error(index, "Product type must be Physical or Service", "product_type"))
                continue
            base_unit = row.get("base_unit") or "piece"
            if not self.UNIT_NAME_PATTERN.fullmatch(base_unit):
                errors.append(self._row_error(index, "Base unit must start with a letter and contain only letters, numbers, spaces, underscores, or hyphens, up to 20 characters", "base_unit"))
                continue

            sku = row.get("sku", "").strip() or None
            barcode = row.get("barcode", "").strip() or None
            if sku is not None and (len(sku) > 64 or any(ord(ch) < 32 for ch in sku)):
                errors.append(self._row_error(index, "SKU must be at most 64 characters and contain no control characters", "sku"))
                continue
            if barcode is not None and (len(barcode) > 128 or any(ord(ch) < 32 for ch in barcode)):
                errors.append(self._row_error(index, "Barcode must be at most 128 characters and contain no control characters", "barcode"))
                continue

            sku_key = sku.casefold() if sku else None
            barcode_key = barcode if barcode else None
            if sku_key and (sku_key in seen_skus or Product.query.filter(
                Product.store_id == store_id, func.lower(Product.sku) == sku_key
            ).first()):
                errors.append(self._row_error(index, "SKU already exists in this store", "sku"))
                continue
            if barcode_key and (barcode_key in seen_barcodes or Product.query.filter_by(
                store_id=store_id, barcode=barcode_key
            ).first()):
                errors.append(self._row_error(index, "Barcode already exists in this store", "barcode"))
                continue

            opening_stock, error = self._parse_integer(
                row.get("opening_stock", ""), "Opening stock", allow_blank=True, default=0
            )
            if error:
                errors.append(self._row_error(index, error, "opening_stock"))
                continue
            threshold = None
            if row.get("low_stock_threshold", ""):
                threshold, error = self._parse_integer(row["low_stock_threshold"], "Low stock threshold", minimum=1)
                if error:
                    errors.append(self._row_error(index, error, "low_stock_threshold"))
                    continue
            if product_type == "Service" and (opening_stock != 0 or threshold is not None):
                errors.append(self._row_error(index, "Services must have zero opening stock and no low-stock threshold", "product_type"))
                continue

            category_id = None
            if row.get("category_id", ""):
                category_id, error = self._parse_integer(row["category_id"], "Category ID", minimum=1)
                if error:
                    errors.append(self._row_error(index, error, "category_id"))
                    continue
            elif row.get("category", ""):
                category_matches = Category.query.filter(
                    Category.store_id == store_id,
                    Category.item_type == product_type,
                    func.lower(Category.name) == row["category"].casefold(),
                ).all()
                if len(category_matches) != 1:
                    errors.append(self._row_error(index, "Category must match one existing category in this store and product type", "category"))
                    continue
                category_id = category_matches[0].id

            if category_id is not None:
                category = Category.query.filter_by(id=category_id, store_id=store_id).first()
                if not category:
                    errors.append(self._row_error(index, "Category does not belong to this store", "category_id" if row.get("category_id", "") else "category"))
                    continue
                if category.item_type != product_type:
                    errors.append(self._row_error(index, "Category item type must match product type", "category_id" if row.get("category_id", "") else "category"))
                    continue

            limit_error = SubscriptionService.check_limit(store_id, "products")
            if limit_error:
                db.session.rollback()
                errors.append(self._row_error(index, "Store product limit has been reached"))
                break

            try:
                product = Product(
                    store_id=store_id, name=name.strip(), product_type=product_type,
                    category_id=category_id, base_unit=base_unit, price=price,
                    sku=sku, barcode=barcode, cost_price=cost_price,
                    stock_quantity=opening_stock if product_type == "Physical" else 0,
                    low_stock_threshold=threshold if product_type == "Physical" else None,
                )
                db.session.add(product)
                db.session.flush()
                db.session.add(ProductUnit(
                    product_id=product.id, name=base_unit, base_quantity=1, price=price,
                ))
                AlertService().sync_low_stock_alert(product)
                db.session.commit()
                created.append({
                    "row": index, "id": product.id, "name": product.name,
                    "product_type": product.product_type, "sku": product.sku,
                    "barcode": product.barcode,
                    "cost_price": str(product.cost_price) if product.cost_price is not None else None,
                    "category_id": product.category_id,
                })
                if sku_key:
                    seen_skus.add(sku_key)
                if barcode_key:
                    seen_barcodes.add(barcode_key)
            except IntegrityError:
                db.session.rollback()
                errors.append(self._row_error(index, "Record conflicts with existing data"))
            except Exception:
                db.session.rollback()
                errors.append(self._row_error(index, "Record could not be imported"))

        return Response.success_response(
            {"created_count": len(created), "failed_count": len(errors), "created": created, "errors": errors},
            "CSV_IMPORT_COMPLETED",
        ), 200
