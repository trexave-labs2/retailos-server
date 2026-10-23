from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import request, session
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.services.audit_service import AuditService
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.stock_receiving import StockReceiving
from server.app.services.alert_service import AlertService
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import is_future, now_utc


CENT = Decimal("0.01")


class StockReceivingService:
    def _serialize(self, receiving):
        return {
            "id": receiving.id,
            "store_id": receiving.store_id,
            "product_id": receiving.product_id,
            "user_id": receiving.user_id,
            "quantity": receiving.quantity,
            "unit_id": receiving.unit_id,
            "unit": receiving.unit_name,
            "unit_base_quantity": receiving.unit_base_quantity,
            "base_quantity_received": receiving.base_quantity_received,
            "base_unit": db.session.get(Product, receiving.product_id).base_unit,
            "unit_cost": str(receiving.unit_cost),
            "total_cost": str(receiving.total_cost),
            "supplier_or_source": receiving.supplier_or_source,
            "client_transaction_id": receiving.client_transaction_id,
            "receiving_date": receiving.receiving_date.replace(tzinfo=timezone.utc).isoformat(),
            "created_at": receiving.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }

    def _error(self, code, message, details, status=400):
        return Response.error_response(code, message, details), status

    def _parse_receiving_date(self, value):
        if value is None:
            return now_utc().replace(tzinfo=None), None
        if not isinstance(value, str) or not value.strip():
            return None, "Receiving date must be an ISO-8601 date-time string"
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None, "Receiving date must be a valid ISO-8601 date-time"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if is_future(parsed):
            return None, "Receiving date cannot be in the future"
        return parsed.replace(tzinfo=None), None

    def _money(self, value, field):
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None, f"{field} must be a valid monetary amount"
        if not amount.is_finite() or amount < 0:
            return None, f"{field} must be finite and zero or greater"
        if amount >= Decimal("10000000000"):
            return None, f"{field} exceeds the supported range"
        try:
            rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return None, f"{field} exceeds the supported precision"
        if amount != rounded:
            return None, f"{field} must have no more than two decimal places"
        return rounded, None

    def _existing_response(self, existing, data, product_id, quantity, unit, unit_cost, source, receiving_date_supplied, receiving_date):
        same = (
            existing.product_id == product_id
            and existing.quantity == quantity
            and existing.unit_id == unit.id
            and existing.unit_cost == unit_cost
            and existing.supplier_or_source == source
            and (not receiving_date_supplied or existing.receiving_date == receiving_date)
        )
        if not same:
            return self._error(
                "IDEMPOTENCY_KEY_REUSED",
                "This receiving transaction ID has already been used for different data",
                {},
                409,
            )
        return Response.success_response(
            {"receiving": self._serialize(existing), "duplicate": True},
            "STOCK_RECEIVING_ALREADY_RECORDED",
        ), 200

    def receive_stock(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return self._error("VALIDATION_ERROR", "Invalid input data", {"payload": "Payload must be a JSON object"})

        store_id = data.get("store_id")
        product_id = data.get("product_id")
        quantity = data.get("quantity")
        transaction_id = data.get("client_transaction_id")
        fields = {}

        if not isinstance(store_id, int) or isinstance(store_id, bool):
            fields["store_id"] = "Store ID must be an integer"
        elif not get_authorized_store(store_id):
            return self._error("STORE_ACCESS_DENIED", "You do not have access to this store", {}, 403)

        if not isinstance(product_id, int) or isinstance(product_id, bool) or product_id <= 0:
            fields["product_id"] = "Product ID must be a positive integer"
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            fields["quantity"] = "Quantity must be a positive integer"
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id.strip()) > 128:
            fields["client_transaction_id"] = "A unique transaction ID of 1 to 128 characters is required"

        source = data.get("supplier_or_source")
        if source is not None:
            if not isinstance(source, str) or len(source.strip()) > 160:
                fields["supplier_or_source"] = "Supplier or source must be 160 characters or fewer"
            else:
                source = source.strip() or None

        if fields:
            return self._error("VALIDATION_ERROR", "Invalid input data", fields)

        product = Product.query.filter_by(id=product_id, store_id=store_id).first()
        if not product:
            return self._error("PRODUCT_NOT_FOUND", "Product not found", {}, 404)
        if product.product_type != "Physical":
            return self._error("VALIDATION_ERROR", "Stock can only be received for physical products", {"product_type": "Services do not track inventory"})

        unit_id = data.get("unit_id")
        if unit_id is not None:
            if not isinstance(unit_id, int) or isinstance(unit_id, bool) or unit_id <= 0:
                return self._error("VALIDATION_ERROR", "Invalid input data", {"unit_id": "Unit ID must be a positive integer"})
            unit = ProductUnit.query.filter_by(id=unit_id, product_id=product.id).first()
        else:
            unit = ProductUnit.query.filter_by(
                product_id=product.id,
                name=product.base_unit,
                base_quantity=1,
            ).first()
        if not unit:
            return self._error("VALIDATION_ERROR", "Invalid stock receiving unit", {"unit_id": "Choose a configured unit belonging to this product"})

        unit_cost, cost_error = self._money(data.get("unit_cost"), "Unit cost")
        if cost_error:
            return self._error("VALIDATION_ERROR", "Invalid input data", {"unit_cost": cost_error})

        total_cost = (Decimal(quantity) * unit_cost).quantize(CENT)
        if "total_cost" in data:
            supplied_total, total_error = self._money(data["total_cost"], "Total cost")
            if total_error:
                return self._error("VALIDATION_ERROR", "Invalid input data", {"total_cost": total_error})
            if supplied_total != total_cost:
                return self._error("VALIDATION_ERROR", "Invalid input data", {"total_cost": "Total cost must equal quantity multiplied by unit cost"})

        receiving_date_supplied = "receiving_date" in data
        receiving_date, date_error = self._parse_receiving_date(data.get("receiving_date"))
        if date_error:
            return self._error("VALIDATION_ERROR", "Invalid input data", {"receiving_date": date_error})

        transaction_id = transaction_id.strip()
        existing = StockReceiving.query.filter_by(
            store_id=store_id,
            client_transaction_id=transaction_id,
        ).first()
        if existing:
            return self._existing_response(
                existing, data, product_id, quantity, unit, unit_cost, source,
                receiving_date_supplied, receiving_date
            )

        base_quantity_received = quantity * unit.base_quantity
        previous_quantity = product.stock_quantity
        new_quantity = previous_quantity + base_quantity_received
        try:
            receiving = StockReceiving(
                store_id=store_id,
                product_id=product.id,
                user_id=session.get("user_id"),
                unit_id=unit.id,
                quantity=quantity,
                unit_name=unit.name,
                unit_base_quantity=unit.base_quantity,
                base_quantity_received=base_quantity_received,
                unit_cost=unit_cost,
                total_cost=total_cost,
                supplier_or_source=source,
                client_transaction_id=transaction_id,
                receiving_date=receiving_date,
            )
            product.stock_quantity = new_quantity
            db.session.add(receiving)
            db.session.flush()
            db.session.add(InventoryMovement(
                store_id=store_id,
                product_id=product.id,
                user_id=session.get("user_id"),
                movement_type="RECEIVING",
                quantity_change=base_quantity_received,
                previous_quantity=previous_quantity,
                new_quantity=new_quantity,
                reason=f"Stock received: {quantity} {unit.name}",
            ))
            AlertService().sync_low_stock_alert(product)
            AuditService.record(
                store_id=store_id,
                action_type="STOCK_RECEIVED",
                entity_type="stock_receiving",
                entity_id=receiving.id,
                after={
                    "product_id": product.id,
                    "quantity_received": base_quantity_received,
                    "stock_quantity": new_quantity,
                    "total_cost": str(total_cost),
                },
                details={"supplier_or_source": source},
            )
            db.session.commit()
            return Response.success_response(
                {"receiving": self._serialize(receiving), "product_stock_quantity": product.stock_quantity, "duplicate": False},
                "STOCK_RECEIVED",
            ), 201
        except IntegrityError:
            db.session.rollback()
            # A concurrent retry can race the initial idempotency lookup.
            existing = StockReceiving.query.filter_by(
                store_id=store_id,
                client_transaction_id=transaction_id,
            ).first()
            if existing:
                return self._existing_response(
                    existing, data, product_id, quantity, unit, unit_cost, source,
                    receiving_date_supplied, receiving_date
                )
            return self._error("CONFLICT_ERROR", "Stock receiving could not be recorded", {}, 409)

    def list_receivings(self):
        store_id = request.args.get("store_id", type=int)
        product_id = request.args.get("product_id", type=int)
        if not get_authorized_store(store_id):
            return self._error("STORE_ACCESS_DENIED", "You do not have access to this store", {}, 403)
        query = StockReceiving.query.filter_by(store_id=store_id)
        if product_id is not None:
            product = Product.query.filter_by(id=product_id, store_id=store_id).first()
            if not product:
                return self._error("PRODUCT_NOT_FOUND", "Product not found", {}, 404)
            query = query.filter_by(product_id=product_id)
        records = query.order_by(StockReceiving.receiving_date.desc(), StockReceiving.id.desc()).all()
        return Response.success_response(
            {"receivings": [self._serialize(record) for record in records]},
            "STOCK_RECEIVINGS_RETRIEVED",
        ), 200
