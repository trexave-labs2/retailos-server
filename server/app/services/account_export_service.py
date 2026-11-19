"""Owner-authorized, bounded JSON export of a RetailOS store account."""

from datetime import datetime, timezone

from flask import jsonify, request

from server.app.models.alert import Alert
from server.app.models.category import Category
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.refund import Refund, RefundItem
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.models.sale_payment import SalePayment
from server.app.models.stock_receiving import StockReceiving
from server.app.models.user import User
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store


MAX_EXPORT_RECORDS = 25000


def _timestamp(value):
    return value.isoformat() if value is not None else None


def _money(value):
    return str(value) if value is not None else None


class AccountExportService:
    """Build a store-only export using explicit allowlisted fields."""

    def export_store(self):
        raw_store_id = request.args.get("store_id")
        try:
            store_id = int(raw_store_id)
        except (TypeError, ValueError):
            store_id = None

        if not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0:
            return Response.error_response(
                "VALIDATION_ERROR",
                "A valid store_id query parameter is required",
                {"store_id": "Store ID must be a positive integer"},
            ), 400

        store = get_authorized_store(store_id)
        if not store:
            return Response.error_response(
                "STORE_ACCESS_DENIED", "You do not have access to this store", {}
            ), 403

        from server.app.services.subscription_service import SubscriptionService
        feature_error = SubscriptionService.check_feature(store_id, "data_export")
        if feature_error:
            return feature_error

        owner = User.query.filter_by(id=store.user_id).first()
        if owner is None:
            return Response.error_response(
                "STORE_NOT_FOUND", "The requested store could not be exported", {}
            ), 404

        export_data = {
            "format": "retailos-account-export",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "store": {
                "id": store.id,
                "name": store.name,
                "current_plan": store.current_plan,
                "subscription_status": store.subscription_status,
                "current_period_end": _timestamp(store.current_period_end),
                "trial_used_at": _timestamp(store.trial_used_at),
                "sales_limit_grace_started_at": _timestamp(
                    store.sales_limit_grace_started_at
                ),
                "created_at": _timestamp(store.created_at),
            },
            "owner": {
                "name": owner.name,
                "email": owner.email,
                "created_at": _timestamp(owner.created_at),
            },
            "data": {},
            "limits": {
                "maximum_records": MAX_EXPORT_RECORDS,
                "records_exported": 0,
            },
        }

        remaining = MAX_EXPORT_RECORDS

        def add_collection(name, query, serializer):
            nonlocal remaining
            records = query.limit(remaining + 1).all()
            if len(records) > remaining:
                return False
            export_data["data"][name] = [serializer(record) for record in records]
            remaining -= len(records)
            return True

        # Explicit allowlists prevent credentials and future sensitive columns
        # from leaking into the export through generic model serialization.
        collections = [
            (
                "categories",
                Category.query.filter_by(store_id=store_id).order_by(Category.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id, "name": row.name,
                    "item_type": row.item_type,
                },
            ),
            (
                "products",
                Product.query.filter_by(store_id=store_id).order_by(Product.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id, "name": row.name,
                    "product_type": row.product_type, "base_unit": row.base_unit,
                    "price": _money(row.price), "sku": row.sku,
                    "barcode": row.barcode, "cost_price": _money(row.cost_price),
                    "stock_quantity": row.stock_quantity,
                    "low_stock_threshold": row.low_stock_threshold,
                    "category_id": row.category_id,
                    "created_at": _timestamp(row.created_at),
                    "updated_at": _timestamp(row.updated_at),
                },
            ),
            (
                "product_units",
                ProductUnit.query.join(Product).filter(
                    Product.store_id == store_id
                ).order_by(ProductUnit.id),
                lambda row: {
                    "id": row.id, "product_id": row.product_id, "name": row.name,
                    "base_quantity": row.base_quantity, "price": _money(row.price),
                },
            ),
            (
                "customers",
                Customer.query.filter_by(store_id=store_id).order_by(Customer.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id, "name": row.name,
                    "contact": row.contact,
                    "outstanding_balance": _money(row.outstanding_balance),
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "sales",
                Sale.query.filter_by(store_id=store_id).order_by(Sale.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id,
                    "customer_id": row.customer_id,
                    "client_transaction_id": row.client_transaction_id,
                    "total_amount": _money(row.total_amount),
                    "amount_paid": _money(row.amount_paid),
                    "balance": _money(row.balance),
                    "payment_method": row.payment_method,
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "sale_items",
                SaleItem.query.join(Sale).filter(
                    Sale.store_id == store_id
                ).order_by(SaleItem.id),
                lambda row: {
                    "id": row.id, "sale_id": row.sale_id,
                    "product_id": row.product_id, "unit_id": row.unit_id,
                    "unit_name_at_sale": row.unit_name_at_sale,
                    "unit_base_quantity": row.unit_base_quantity,
                    "base_quantity_deducted": row.base_quantity_deducted,
                    "quantity": row.quantity,
                    "price_at_sale": _money(row.price_at_sale),
                    "total": _money(row.total),
                },
            ),
            (
                "sale_payments",
                SalePayment.query.join(Sale).filter(
                    Sale.store_id == store_id
                ).order_by(SalePayment.id),
                lambda row: {
                    "id": row.id, "sale_id": row.sale_id,
                    "amount": _money(row.amount),
                    "payment_method": row.payment_method,
                    "client_transaction_id": row.client_transaction_id,
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "refunds",
                Refund.query.filter_by(store_id=store_id).order_by(Refund.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id,
                    "sale_id": row.sale_id, "user_id": row.user_id,
                    "amount": _money(row.amount), "refund_type": row.refund_type,
                    "reason": row.reason, "payment_method": row.payment_method,
                    "reference": row.reference,
                    "client_transaction_id": row.client_transaction_id,
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "refund_items",
                RefundItem.query.join(Refund).filter(
                    Refund.store_id == store_id
                ).order_by(RefundItem.id),
                lambda row: {
                    "id": row.id, "refund_id": row.refund_id,
                    "sale_item_id": row.sale_item_id, "product_id": row.product_id,
                    "quantity": row.quantity,
                    "unit_name_at_sale": row.unit_name_at_sale,
                    "unit_base_quantity": row.unit_base_quantity,
                },
            ),
            (
                "inventory_movements",
                InventoryMovement.query.filter_by(
                    store_id=store_id
                ).order_by(InventoryMovement.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id,
                    "product_id": row.product_id, "user_id": row.user_id,
                    "movement_type": row.movement_type,
                    "quantity_change": row.quantity_change,
                    "previous_quantity": row.previous_quantity,
                    "new_quantity": row.new_quantity, "reason": row.reason,
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "stock_receivings",
                StockReceiving.query.filter_by(
                    store_id=store_id
                ).order_by(StockReceiving.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id,
                    "product_id": row.product_id, "user_id": row.user_id,
                    "unit_id": row.unit_id, "quantity": row.quantity,
                    "unit_name": row.unit_name,
                    "unit_base_quantity": row.unit_base_quantity,
                    "base_quantity_received": row.base_quantity_received,
                    "unit_cost": _money(row.unit_cost),
                    "total_cost": _money(row.total_cost),
                    "supplier_or_source": row.supplier_or_source,
                    "client_transaction_id": row.client_transaction_id,
                    "receiving_date": _timestamp(row.receiving_date),
                    "created_at": _timestamp(row.created_at),
                },
            ),
            (
                "alerts",
                Alert.query.filter_by(store_id=store_id).order_by(Alert.id),
                lambda row: {
                    "id": row.id, "store_id": row.store_id,
                    "product_id": row.product_id, "type": row.type.value,
                    "message": row.message, "is_resolved": row.is_resolved,
                    "condition_active": row.condition_active,
                    "read_at": _timestamp(row.read_at),
                    "dismissed_at": _timestamp(row.dismissed_at),
                    "created_at": _timestamp(row.created_at),
                },
            ),
        ]

        for name, query, serializer in collections:
            if not add_collection(name, query, serializer):
                return Response.error_response(
                    "EXPORT_TOO_LARGE",
                    "This store has too much data for a single export. Contact support for an assisted export.",
                    {"maximum_records": MAX_EXPORT_RECORDS},
                ), 413

        export_data["limits"]["records_exported"] = MAX_EXPORT_RECORDS - remaining

        response = jsonify(export_data)
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="retailos-store-{store_id}-export-{date_stamp}.json"'
        )
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response, 200
