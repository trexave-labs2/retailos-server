from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from flask import request, session
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.services.audit_service import AuditService
from server.app.services.alert_service import AlertService
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.refund import Refund, RefundItem
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc


CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")


class RefundService:
    def _error(self, code, message, details=None, status=400):
        return Response.error_response(code, message, details or {}), status

    def _serialize(self, refund):
        returned_items = (
            db.session.query(RefundItem, Product)
            .join(Product, Product.id == RefundItem.product_id)
            .filter(RefundItem.refund_id == refund.id)
            .order_by(RefundItem.id.asc())
            .all()
        )
        sale = db.session.get(Sale, refund.sale_id)
        return {
            "id": refund.id,
            "store_id": refund.store_id,
            "sale_id": refund.sale_id,
            "original_sale_reference": sale.client_transaction_id if sale else None,
            "amount": str(refund.amount),
            "refund_type": refund.refund_type,
            "reason": refund.reason,
            "payment_method": refund.payment_method,
            "reference": refund.reference,
            "client_transaction_id": refund.client_transaction_id,
            "created_at": refund.created_at.isoformat(),
            "returned_items": [
                {
                    "sale_item_id": item.sale_item_id,
                    "product_id": item.product_id,
                    "product_name": product.name,
                    "quantity": item.quantity,
                    "unit_name": item.unit_name_at_sale,
                    "unit_base_quantity": item.unit_base_quantity,
                    "base_quantity_restored": item.quantity * item.unit_base_quantity,
                }
                for item, product in returned_items
            ],
            "receipt": self._receipt_data(refund, sale),
        }

    def _receipt_data(self, refund, sale):
        return {
            "receipt_type": "Refund",
            "refund_id": refund.id,
            "refund_reference": refund.reference,
            "original_sale_id": refund.sale_id,
            "original_sale_reference": sale.client_transaction_id if sale else None,
            "store_id": refund.store_id,
            "amount": str(refund.amount),
            "refund_type": refund.refund_type,
            "reason": refund.reason,
            "payment_method": refund.payment_method,
            "created_at": refund.created_at.isoformat(),
            "printable_text": "\n".join([
                "RetailOS Refund Receipt",
                f"Refund reference: {refund.reference}",
                f"Original sale: #{refund.sale_id}",
                f"Date: {refund.created_at.isoformat()}",
                f"Type: {refund.refund_type}",
                f"Amount refunded: {refund.amount:.2f}",
                f"Refund method: {refund.payment_method}",
                f"Reason: {refund.reason}",
            ]),
        }

    def _response(self, refund, message, status=200):
        return Response.success_response(
            {"refund": self._serialize(refund)},
            message,
        ), status

    @staticmethod
    def _normalize_returned_items(value):
        if value is None:
            return [], None
        if not isinstance(value, list):
            return None, "returned_items must be a list"
        normalized = []
        seen = set()
        for entry in value:
            if not isinstance(entry, dict):
                return None, "Each returned item must be an object"
            sale_item_id = entry.get("sale_item_id")
            quantity = entry.get("quantity")
            if (
                not isinstance(sale_item_id, int)
                or isinstance(sale_item_id, bool)
                or sale_item_id <= 0
                or not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or quantity <= 0
            ):
                return None, "Each returned item requires a positive sale_item_id and quantity"
            if sale_item_id in seen:
                return None, "A sale item may appear only once in returned_items"
            seen.add(sale_item_id)
            normalized.append({"sale_item_id": sale_item_id, "quantity": quantity})
        return sorted(normalized, key=lambda item: item["sale_item_id"]), None

    def create_refund(self):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return self._error("VALIDATION_ERROR", "Invalid input data")

        store_id = data.get("store_id")
        sale_id = data.get("sale_id")
        amount_input = data.get("amount")
        reason = data.get("reason")
        payment_method = data.get("payment_method")
        transaction_id = data.get("client_transaction_id")
        returned_items, returned_items_error = self._normalize_returned_items(data.get("returned_items"))

        if not isinstance(store_id, int) or isinstance(store_id, bool) or store_id <= 0:
            return self._error("VALIDATION_ERROR", "Store ID must be a positive integer")
        if not isinstance(sale_id, int) or isinstance(sale_id, bool) or sale_id <= 0:
            return self._error("VALIDATION_ERROR", "Sale ID must be a positive integer")
        if not get_authorized_store(store_id):
            return self._error("STORE_ACCESS_DENIED", "You do not have access to this store", status=403)
        if returned_items_error:
            return self._error("VALIDATION_ERROR", returned_items_error)
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id.strip()) > 128:
            return self._error("VALIDATION_ERROR", "A unique client_transaction_id of 1 to 128 characters is required")
        transaction_id = transaction_id.strip()
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            return self._error("VALIDATION_ERROR", "A refund reason of 1 to 500 characters is required")
        reason = reason.strip()
        if payment_method not in Sale.PAYMENT_METHODS:
            return self._error("VALIDATION_ERROR", "Payment method must be one of: Cash, Transfer, POS")

        try:
            amount = Decimal(str(amount_input))
            if not amount.is_finite() or amount <= 0 or amount > MAX_MONEY:
                raise InvalidOperation
            rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
            if amount != rounded:
                raise InvalidOperation
            amount = rounded
        except (InvalidOperation, TypeError, ValueError, ArithmeticError):
            return self._error("VALIDATION_ERROR", "Refund amount must be positive, finite, and have at most two decimal places")

        try:
            sale = db.session.execute(
                select(Sale)
                .where(Sale.id == sale_id, Sale.store_id == store_id)
                .with_for_update()
            ).scalar_one_or_none()
            if not sale:
                db.session.rollback()
                return self._error("SALE_NOT_FOUND", "Sale not found", status=404)

            # Idempotency is checked before eligibility so a successful retry
            # returns its original refund instead of attempting to refund again.
            existing = Refund.query.filter_by(client_transaction_id=transaction_id).first()
            if existing:
                existing_items = sorted(
                    [
                        {"sale_item_id": item.sale_item_id, "quantity": item.quantity}
                        for item in RefundItem.query.filter_by(refund_id=existing.id).all()
                    ],
                    key=lambda item: item["sale_item_id"],
                )
                same = (
                    existing.store_id == store_id
                    and existing.sale_id == sale_id
                    and existing.amount == amount
                    and existing.reason == reason
                    and existing.payment_method == payment_method
                    and existing_items == returned_items
                )
                if same:
                    return self._response(existing, "REFUND_ALREADY_PROCESSED")
                db.session.rollback()
                return self._error(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Client transaction ID has already been used for a different refund",
                    status=409,
                )

            prior_refunds = db.session.query(
                func.coalesce(func.sum(Refund.amount), 0)
            ).filter(Refund.sale_id == sale.id).scalar()
            prior_refunds = Decimal(str(prior_refunds or "0.00"))
            remaining_refundable = Decimal(str(sale.amount_paid)) - prior_refunds
            if amount > remaining_refundable:
                db.session.rollback()
                return self._error(
                    "REFUND_EXCEEDS_REMAINING_AMOUNT",
                    "Refund amount exceeds the remaining refundable amount",
                    {
                        "remaining_refundable_amount": str(max(remaining_refundable, Decimal("0.00"))),
                        "sale_id": sale.id,
                    },
                    400,
                )

            validated_returns = []
            returned_value = Decimal("0.00")
            for returned in returned_items:
                sale_item = db.session.execute(
                    select(SaleItem)
                    .where(SaleItem.id == returned["sale_item_id"], SaleItem.sale_id == sale.id)
                    .with_for_update()
                ).scalar_one_or_none()
                if not sale_item:
                    db.session.rollback()
                    return self._error(
                        "SALE_ITEM_NOT_FOUND",
                        "Returned item does not belong to the original sale",
                        {"sale_item_id": returned["sale_item_id"]},
                        404,
                    )

                previously_returned = db.session.query(
                    func.coalesce(func.sum(RefundItem.quantity), 0)
                ).filter(RefundItem.sale_item_id == sale_item.id).scalar()
                remaining_quantity = sale_item.quantity - int(previously_returned or 0)
                if returned["quantity"] > remaining_quantity:
                    db.session.rollback()
                    return self._error(
                        "RETURN_QUANTITY_EXCEEDED",
                        "Returned quantity exceeds the quantity sold that has not already been returned",
                        {
                            "sale_item_id": sale_item.id,
                            "remaining_returnable_quantity": remaining_quantity,
                        },
                    )

                product = db.session.execute(
                    select(Product)
                    .where(Product.id == sale_item.product_id, Product.store_id == store_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if not product:
                    db.session.rollback()
                    return self._error("PRODUCT_NOT_FOUND", "Original sale product not found", status=404)
                if product.product_type != "Physical":
                    db.session.rollback()
                    return self._error(
                        "INVALID_RETURN_ITEM",
                        "Only physical products can be returned to stock",
                        {"sale_item_id": sale_item.id},
                    )

                returned_value += Decimal(str(sale_item.price_at_sale)) * returned["quantity"]
                validated_returns.append((sale_item, product, returned["quantity"]))

            if validated_returns and amount > returned_value:
                db.session.rollback()
                return self._error(
                    "REFUND_EXCEEDS_RETURNED_ITEMS_VALUE",
                    "Refund amount cannot exceed the recorded returned items' sale value",
                    {"returned_items_value": str(returned_value.quantize(CENT))},
                )

            new_total_refunded = prior_refunds + amount
            is_full_refund = (
                new_total_refunded == Decimal(str(sale.total_amount))
                and Decimal(str(sale.amount_paid)) >= Decimal(str(sale.total_amount))
            )
            refund = Refund(
                store_id=store_id,
                sale_id=sale.id,
                user_id=session.get("user_id"),
                amount=amount,
                refund_type="Full" if is_full_refund else "Partial",
                reason=reason,
                payment_method=payment_method,
                reference=f"RFD-{uuid4().hex.upper()}",
                client_transaction_id=transaction_id,
                created_at=now_utc(),
            )
            db.session.add(refund)
            db.session.flush()

            for sale_item, product, quantity in validated_returns:
                base_quantity = quantity * sale_item.unit_base_quantity
                previous_quantity = product.stock_quantity
                product.stock_quantity += base_quantity
                db.session.add(RefundItem(
                    refund_id=refund.id,
                    sale_item_id=sale_item.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_name_at_sale=sale_item.unit_name_at_sale,
                    unit_base_quantity=sale_item.unit_base_quantity,
                ))
                db.session.add(InventoryMovement(
                    store_id=store_id,
                    product_id=product.id,
                    user_id=session.get("user_id"),
                    movement_type="REFUND",
                    quantity_change=base_quantity,
                    previous_quantity=previous_quantity,
                    new_quantity=product.stock_quantity,
                    reason=f"Refund {refund.reference} for sale #{sale.id}",
                    created_at=now_utc(),
                ))
                AlertService().sync_low_stock_alert(product)

            AuditService.record(
                store_id=store_id,
                action_type="REFUND_PROCESSED",
                entity_type="refund",
                entity_id=refund.id,
                after={
                    "sale_id": refund.sale_id,
                    "amount": str(refund.amount),
                    "refund_type": refund.refund_type,
                    "payment_method": refund.payment_method,
                    "reference": refund.reference,
                },
                details={"reason": refund.reason},
            )
            db.session.commit()
            return self._response(refund, "REFUND_PROCESSED", 201)

        except IntegrityError:
            db.session.rollback()
            existing = Refund.query.filter_by(client_transaction_id=transaction_id).first()
            if existing:
                existing_items = sorted(
                    [
                        {"sale_item_id": item.sale_item_id, "quantity": item.quantity}
                        for item in RefundItem.query.filter_by(refund_id=existing.id).all()
                    ],
                    key=lambda item: item["sale_item_id"],
                )
                same = (
                    existing.store_id == store_id
                    and existing.sale_id == sale_id
                    and existing.amount == amount
                    and existing.reason == reason
                    and existing.payment_method == payment_method
                    and existing_items == returned_items
                )
                if same:
                    return self._response(existing, "REFUND_ALREADY_PROCESSED")
                return self._error(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Client transaction ID has already been used for a different refund",
                    status=409,
                )
            return self._error("REFUND_CONFLICT", "Refund could not be processed because of a conflicting request", status=409)
        except Exception:
            db.session.rollback()
            raise

    def list_refunds(self):
        store_id = request.args.get("store_id", type=int)
        sale_id = request.args.get("sale_id", type=int)
        if not isinstance(store_id, int) or store_id <= 0:
            return self._error("VALIDATION_ERROR", "Store ID is required as a positive integer")
        if not get_authorized_store(store_id):
            return self._error("STORE_ACCESS_DENIED", "You do not have access to this store", status=403)

        query = Refund.query.filter_by(store_id=store_id)
        if sale_id is not None:
            if sale_id <= 0:
                return self._error("VALIDATION_ERROR", "Sale ID must be a positive integer")
            sale = Sale.query.filter_by(id=sale_id, store_id=store_id).first()
            if not sale:
                return self._error("SALE_NOT_FOUND", "Sale not found", status=404)
            query = query.filter_by(sale_id=sale_id)

        refunds = query.order_by(Refund.created_at.desc(), Refund.id.desc()).all()
        total_refunded = sum((Decimal(str(refund.amount)) for refund in refunds), Decimal("0.00"))
        return Response.success_response(
            {
                "refund_count": len(refunds),
                "total_refunded": str(total_refunded),
                "refunds": [self._serialize(refund) for refund in refunds],
            },
            "REFUNDS_RETRIEVED",
        ), 200

    def refund_receipt(self):
        store_id = request.args.get("store_id", type=int)
        refund_id = request.args.get("refund_id", type=int)
        if not isinstance(store_id, int) or store_id <= 0 or not isinstance(refund_id, int) or refund_id <= 0:
            return self._error("VALIDATION_ERROR", "Store ID and refund ID are required positive integers")
        if not get_authorized_store(store_id):
            return self._error("STORE_ACCESS_DENIED", "You do not have access to this store", status=403)
        refund = Refund.query.filter_by(id=refund_id, store_id=store_id).first()
        if not refund:
            return self._error("REFUND_NOT_FOUND", "Refund not found", status=404)
        return Response.success_response(
            {"receipt": self._receipt_data(refund, db.session.get(Sale, refund.sale_id))},
            "REFUND_RECEIPT_RETRIEVED",
        ), 200
