# server/app/services/sales_service.py
from decimal import Decimal, InvalidOperation

from flask import request, session
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.category import Category
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.sale import Sale
from server.app.models.store import Store
from server.app.models.sale_payment import SalePayment
from server.app.models.refund import Refund
from server.app.models.sale_item import SaleItem
from server.app.services.audit_service import AuditService
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc


class SalesService:
    def create_sale(self):
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        store_id = data.get("store_id")
        items = data.get("items")
        customer_id = data.get("customer_id")
        client_transaction_id = data.get("client_transaction_id")
        payment_method = data.get("payment_method")
        amount_paid_input = data.get("amount_paid")

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        if not isinstance(items, list) or not items:
            return Response.error_response(
                "VALIDATION_ERROR",
                "At least one sale item is required",
                {}
            ), 400

        if payment_method not in Sale.PAYMENT_METHODS:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment method must be one of: Cash, Transfer, POS",
                {}
            ), 400

        if client_transaction_id is not None:
            if not isinstance(client_transaction_id, str):
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Client transaction ID must be a string",
                    {}
                ), 400

            client_transaction_id = client_transaction_id.strip()
            if not client_transaction_id or len(client_transaction_id) > 128:
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Client transaction ID must contain 1 to 128 characters",
                    {}
                ), 400

            old_sale = Sale.query.filter_by(
                store_id=store_id,
                client_transaction_id=client_transaction_id
            ).first()

            if old_sale:
                if not self._sale_matches_retry(old_sale, data):
                    return Response.error_response(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Client transaction ID has already been used for a different sale",
                        {"sync_status": "rejected"}
                    ), 409

                return self._response(old_sale, "SALE_ALREADY_PROCESSED"), 200

        if customer_id is not None:
            customer = Customer.query.filter_by(
                id=customer_id,
                store_id=store_id
            ).first()

            if not customer:
                return Response.error_response(
                    "CUSTOMER_NOT_FOUND",
                    "Customer not found",
                    {}
                ), 404

        from server.app.services.subscription_service import SubscriptionService
        limit_error = SubscriptionService.check_limit(store_id, "sales_per_month")
        if limit_error:
            return limit_error

        try:
            created_at = now_utc()
            sale = Sale(
                store_id=store_id,
                customer_id=customer_id,
                client_transaction_id=client_transaction_id,
                total_amount=Decimal("0.00"),
                payment_method=payment_method,
                created_at=created_at
            )

            db.session.add(sale)
            db.session.flush()

            total = Decimal("0.00")

            for item in items:
                if not isinstance(item, dict):
                    db.session.rollback()

                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Each sale item must be an object",
                        {}
                    ), 400

                product_id = item.get("product_id")
                quantity = item.get("quantity")
                unit_id = item.get("unit_id")
                unit_price = item.get("unit_price")

                if (
                    not isinstance(product_id, int)
                    or isinstance(product_id, bool)
                ):
                    db.session.rollback()

                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Product ID must be an integer",
                        {}
                    ), 400

                if (
                    not isinstance(quantity, int)
                    or isinstance(quantity, bool)
                    or quantity <= 0
                ):
                    db.session.rollback()

                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Quantity must be greater than zero",
                        {}
                    ), 400

                product = db.session.execute(
                    select(Product)
                    .where(
                        Product.id == product_id,
                        Product.store_id == store_id
                    )
                    .with_for_update()
                ).scalar_one_or_none()

                if not product:
                    db.session.rollback()

                    return Response.error_response(
                        "PRODUCT_NOT_FOUND",
                        "Product not found",
                        {}
                    ), 404

                selected_unit = None
                if unit_id is not None:
                    if (
                        not isinstance(unit_id, int)
                        or isinstance(unit_id, bool)
                    ):
                        db.session.rollback()
                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Unit ID must be an integer",
                            {}
                        ), 400

                    selected_unit = db.session.execute(
                        select(ProductUnit)
                        .where(
                            ProductUnit.id == unit_id,
                            ProductUnit.product_id == product.id,
                        )
                        .with_for_update()
                    ).scalar_one_or_none()

                    if not selected_unit:
                        db.session.rollback()
                        return Response.error_response(
                            "PRODUCT_UNIT_NOT_FOUND",
                            "Product unit not found",
                            {}
                        ), 404

                base_quantity = selected_unit.base_quantity if selected_unit else 1
                catalog_unit_price = selected_unit.price if selected_unit else product.price

                expected_unit_price = item.get("expected_unit_price")
                if expected_unit_price is not None:
                    try:
                        expected_price = Decimal(str(expected_unit_price))
                    except (ArithmeticError, ValueError, TypeError):
                        db.session.rollback()
                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Expected unit price must be a valid number",
                            {"retryable": False}
                        ), 400

                    if not expected_price.is_finite() or expected_price < 0:
                        db.session.rollback()
                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Expected unit price must be a finite, non-negative number",
                            {"retryable": False}
                        ), 400

                    if expected_price != Decimal(str(catalog_unit_price)):
                        db.session.rollback()
                        return Response.error_response(
                            "STALE_PRICE",
                            "The product price has changed. Refresh the item price before checking out.",
                            {
                                "product_id": product.id,
                                "expected_unit_price": str(expected_price),
                                "current_unit_price": str(catalog_unit_price),
                                "retryable": False
                            }
                        ), 409

                if unit_price is not None:
                    if product.product_type != "Service":
                        db.session.rollback()

                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Unit price can only be edited for service products",
                            {}
                        ), 400

                    try:
                        actual_unit_price = Decimal(str(unit_price))
                    except (ArithmeticError, ValueError, TypeError):
                        db.session.rollback()

                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Unit price must be a valid number",
                            {}
                        ), 400

                    if not actual_unit_price.is_finite():
                        db.session.rollback()

                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Unit price must be a finite number",
                            {}
                        ), 400

                    if actual_unit_price < 0:
                        db.session.rollback()

                        return Response.error_response(
                            "VALIDATION_ERROR",
                            "Unit price must be zero or greater",
                            {}
                        ), 400
                else:
                    actual_unit_price = catalog_unit_price

                base_quantity_deducted = quantity * base_quantity
                item_total = actual_unit_price * quantity
                total += item_total

                sale_item = SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_id=selected_unit.id if selected_unit else None,
                    unit_name_at_sale=selected_unit.name if selected_unit else product.base_unit,
                    unit_base_quantity=base_quantity,
                    base_quantity_deducted=base_quantity_deducted,
                    price_at_sale=actual_unit_price,
                    total=item_total
                )

                db.session.add(sale_item)

                if product.product_type == "Physical":
                    stock_quantity_required = base_quantity_deducted
                    if product.stock_quantity < stock_quantity_required:
                        db.session.rollback()

                        return Response.error_response(
                            "INSUFFICIENT_STOCK",
                            "Insufficient stock",
                            {}
                        ), 400

                    previous_quantity = product.stock_quantity
                    product.stock_quantity -= stock_quantity_required

                    movement = InventoryMovement(
                        store_id=store_id,
                        product_id=product.id,
                        user_id=session.get("user_id"),
                        movement_type="SALE",
                        quantity_change=-stock_quantity_required,
                        previous_quantity=previous_quantity,
                        new_quantity=product.stock_quantity,
                        reason=f"Sale #{sale.id}"
                    )

                    db.session.add(movement)

            if amount_paid_input is None:
                amount_paid = total
            else:
                try:
                    amount_paid = Decimal(str(amount_paid_input))
                except (ArithmeticError, ValueError, TypeError):
                    db.session.rollback()
                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Amount paid must be a valid number",
                        {}
                    ), 400

                if amount_paid < 0:
                    db.session.rollback()
                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Amount paid must be zero or greater",
                        {}
                    ), 400

                if amount_paid > total:
                    db.session.rollback()
                    return Response.error_response(
                        "VALIDATION_ERROR",
                        "Amount paid cannot exceed sale total",
                        {}
                    ), 400

            balance = total - amount_paid

            if balance > 0 and customer_id is None:
                db.session.rollback()
                return Response.error_response(
                    "CUSTOMER_REQUIRED_FOR_BALANCE",
                    "A customer is required when a sale has an outstanding balance",
                    {}
                ), 400

            sale.total_amount = total
            sale.amount_paid = amount_paid
            sale.balance = balance

            # Record the initial payment too, so payment history is complete.
            if amount_paid > 0:
                db.session.add(
                    SalePayment(
                        sale_id=sale.id,
                        amount=amount_paid,
                        payment_method=payment_method,
                        created_at=created_at,
                    )
                )

            if customer_id is not None:
                customer = db.session.execute(
                    select(Customer)
                    .where(
                        Customer.id == customer_id,
                        Customer.store_id == store_id
                    )
                    .with_for_update()
                ).scalar_one_or_none()

                if not customer:
                    db.session.rollback()
                    return Response.error_response(
                        "CUSTOMER_NOT_FOUND",
                        "Customer not found",
                        {}
                    ), 404

                customer.outstanding_balance += balance

            from server.app.services.alert_service import AlertService

            physical_product_ids = [
                item["product_id"]
                for item in items
                if db.session.get(Product, item["product_id"]).product_type == "Physical"
            ]

            if physical_product_ids:
                AlertService().create_low_stock_alerts(
                    store_id,
                    physical_product_ids
                )

            AuditService.record(
                store_id=store_id,
                action_type="SALE_CREATED",
                entity_type="sale",
                entity_id=sale.id,
                after={
                    "total_amount": str(sale.total_amount),
                    "amount_paid": str(sale.amount_paid),
                    "balance": str(sale.balance),
                    "payment_method": sale.payment_method,
                },
                details={"item_count": len(items)},
            )
            db.session.commit()

            return self._response(sale, "SALE_CREATED"), 201

        except IntegrityError:
            db.session.rollback()

            if client_transaction_id:
                existing_sale = Sale.query.filter_by(
                    store_id=store_id,
                    client_transaction_id=client_transaction_id
                ).first()

                if existing_sale:
                    if not self._sale_matches_retry(existing_sale, data):
                        return Response.error_response(
                            "IDEMPOTENCY_KEY_REUSED",
                            "Client transaction ID has already been used for a different sale",
                            {}
                        ), 409
                    return self._response(existing_sale, "SALE_ALREADY_PROCESSED"), 200

            return Response.error_response(
                "CONFLICT_ERROR",
                "Sale could not be completed",
                {}
            ), 409

        except Exception:
            db.session.rollback()
            raise


    def checkout(self):
        """Run checkout with a required idempotency key and stable error metadata."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {"retryable": False}
            ), 400

        transaction_id = data.get("client_transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            return Response.error_response(
                "VALIDATION_ERROR",
                "A client transaction ID is required to safely retry checkout",
                {"retryable": False}
            ), 400

        items = data.get("items")
        if not isinstance(items, list) or not items:
            return Response.error_response(
                "VALIDATION_ERROR",
                "At least one sale item is required",
                {"retryable": False}
            ), 400
        if any(not isinstance(item, dict) or item.get("expected_unit_price") is None for item in items):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Each checkout item must include expected_unit_price to detect stale prices",
                {"retryable": False}
            ), 400

        try:
            response, status = self.create_sale()
            if isinstance(response, dict) and isinstance(response.get("error"), dict):
                error = response["error"]
                fields = error.setdefault("fields", {})
                code = error.get("code")
                retryable = status >= 500 or (
                    status == 409 and code not in {
                        "IDEMPOTENCY_KEY_REUSED",
                        "STALE_PRICE",
                    }
                )
                fields.setdefault("retryable", retryable)
                # Log a compact event without request bodies, customer details,
                # credentials, or payment data.
                from flask import current_app
                current_app.logger.info(
                    "Checkout rejected code=%s status=%s retryable=%s",
                    code or "UNKNOWN_ERROR",
                    status,
                    retryable,
                )
            return response, status
        except Exception as error:
            db.session.rollback()
            from flask import current_app
            current_app.logger.error(
                "Checkout operation failed error_type=%s",
                type(error).__name__,
            )
            return Response.error_response(
                "INTERNAL_SERVER_ERROR",
                "Checkout could not be completed. Retry using the same client transaction ID.",
                {"retryable": True}
            ), 500

    def sync_offline_sale(self):
        """Accept a queued sale using the normal transactional sale workflow."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {"sync_status": "rejected", "retryable": False}
            ), 400

        client_transaction_id = data.get("client_transaction_id")
        if not isinstance(client_transaction_id, str) or not client_transaction_id.strip():
            return Response.error_response(
                "VALIDATION_ERROR",
                "A client-generated transaction ID is required for offline synchronization",
                {"sync_status": "rejected", "retryable": False}
            ), 400

        response, status = self.create_sale()
        if status >= 400:
            if isinstance(response, dict) and isinstance(response.get("error"), dict):
                response["error"]["fields"].setdefault("sync_status", "rejected")
                response["error"]["fields"].setdefault(
                    "retryable",
                    status >= 500 or status == 409
                )
            return response, status

        # create_sale uses the same inventory, price, payment and audit
        # transaction as online sales. A queued client record becomes confirmed
        # only when the server returns accepted/already_processed.
        response["data"]["sync_status"] = "accepted" if status == 201 else "already_processed"
        response["data"]["client_transaction_id"] = client_transaction_id.strip()
        return response, status

    def _sale_matches_retry(self, sale, data):
        """Prevent an idempotency key from silently referring to another sale."""
        if data.get("payment_method") != sale.payment_method:
            return False
        if data.get("customer_id") != sale.customer_id:
            return False

        amount_paid_input = data.get("amount_paid")
        if amount_paid_input is not None:
            try:
                if Decimal(str(amount_paid_input)) != Decimal(str(sale.amount_paid)):
                    return False
            except (ArithmeticError, ValueError, TypeError):
                return False

        requested_items = data.get("items")
        if not isinstance(requested_items, list):
            return False

        saved_items = SaleItem.query.filter_by(sale_id=sale.id).order_by(SaleItem.id).all()
        if len(requested_items) != len(saved_items):
            return False

        for requested, saved in zip(requested_items, saved_items):
            if not isinstance(requested, dict):
                return False
            if requested.get("product_id") != saved.product_id:
                return False
            if requested.get("quantity") != saved.quantity:
                return False
            if requested.get("unit_id") != saved.unit_id:
                return False
            # Editable prices are supported only for service products. When
            # supplied on a retry, require the same recorded unit price.
            if requested.get("unit_price") is not None:
                try:
                    if Decimal(str(requested["unit_price"])) != Decimal(str(saved.price_at_sale)):
                        return False
                except (ArithmeticError, ValueError, TypeError):
                    return False

        return True

    def add_payment(self):
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        store_id = data.get("store_id")
        sale_id = data.get("sale_id")
        amount_input = data.get("amount")
        payment_method = data.get("payment_method")
        client_transaction_id = data.get("client_transaction_id")

        if (
            not isinstance(store_id, int)
            or isinstance(store_id, bool)
            or store_id <= 0
            or not isinstance(sale_id, int)
            or isinstance(sale_id, bool)
            or sale_id <= 0
        ):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Store ID and sale ID must be positive integers",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        if not isinstance(client_transaction_id, str):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Client transaction ID is required and must be a string",
                {}
            ), 400

        client_transaction_id = client_transaction_id.strip()
        if not client_transaction_id or len(client_transaction_id) > 128:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Client transaction ID must contain 1 to 128 characters",
                {}
            ), 400

        if payment_method not in Sale.PAYMENT_METHODS:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment method must be one of: Cash, Transfer, POS",
                {}
            ), 400

        try:
            amount = Decimal(str(amount_input))
        except (ArithmeticError, ValueError, TypeError):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment amount must be a valid number",
                {}
            ), 400

        if not amount.is_finite() or amount <= 0:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment amount must be greater than zero",
                {}
            ), 400

        try:
            rounded_amount = amount.quantize(Decimal("0.01"))
        except InvalidOperation:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment amount is outside the supported range",
                {}
            ), 400

        if amount != rounded_amount or amount > Decimal("9999999999.99"):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment amount must fit a monetary value with at most two decimal places",
                {}
            ), 400

        try:
            sale = db.session.execute(
                select(Sale)
                .where(Sale.id == sale_id, Sale.store_id == store_id)
                .with_for_update()
            ).scalar_one_or_none()

            if not sale:
                db.session.rollback()
                return Response.error_response(
                    "SALE_NOT_FOUND",
                    "Sale not found",
                    {}
                ), 404

            # Check idempotency before checking the current balance. A retry of
            # a previously successful payment must not fail because that
            # payment already reduced the balance.
            existing_payment = SalePayment.query.filter_by(
                client_transaction_id=client_transaction_id
            ).first()

            if existing_payment:
                if (
                    existing_payment.sale_id == sale.id
                    and existing_payment.amount == amount
                    and existing_payment.payment_method == payment_method
                ):
                    return self._response(
                        sale,
                        "SALE_PAYMENT_ALREADY_PROCESSED"
                    ), 200

                db.session.rollback()
                return Response.error_response(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Client transaction ID has already been used for a different payment",
                    {}
                ), 409

            if amount > sale.balance:
                db.session.rollback()
                return Response.error_response(
                    "PAYMENT_EXCEEDS_BALANCE",
                    "Payment amount cannot exceed the outstanding sale balance",
                    {"balance": str(sale.balance)}
                ), 400

            customer = None
            if sale.customer_id is not None:
                customer = db.session.execute(
                    select(Customer)
                    .where(
                        Customer.id == sale.customer_id,
                        Customer.store_id == store_id
                    )
                    .with_for_update()
                ).scalar_one_or_none()

                if not customer:
                    db.session.rollback()
                    return Response.error_response(
                        "CUSTOMER_NOT_FOUND",
                        "Customer not found",
                        {}
                    ), 404

            previous_amount_paid = sale.amount_paid
            previous_balance = sale.balance
            payment = SalePayment(
                sale_id=sale.id,
                amount=amount,
                payment_method=payment_method,
                client_transaction_id=client_transaction_id,
            )
            sale.amount_paid += amount
            sale.balance -= amount
            db.session.add(payment)

            if customer is not None:
                customer.outstanding_balance -= amount

            AuditService.record(
                store_id=store_id,
                action_type="SALE_PAYMENT_RECORDED",
                entity_type="sale_payment",
                entity_id=client_transaction_id,
                before={"sale_amount_paid": str(previous_amount_paid), "sale_balance": str(previous_balance)},
                after={"sale_amount_paid": str(sale.amount_paid), "sale_balance": str(sale.balance)},
                details={"sale_id": sale.id, "amount": str(amount), "payment_method": payment_method},
            )
            db.session.commit()
            return self._response(sale, "SALE_PAYMENT_RECORDED"), 200

        except IntegrityError:
            db.session.rollback()

            # The unique constraint is the final guard if two requests race.
            existing_payment = SalePayment.query.filter_by(
                client_transaction_id=client_transaction_id
            ).first()
            if existing_payment:
                if (
                    existing_payment.sale_id == sale_id
                    and existing_payment.amount == amount
                    and existing_payment.payment_method == payment_method
                ):
                    existing_sale = Sale.query.filter_by(
                        id=sale_id,
                        store_id=store_id
                    ).first()
                    if existing_sale:
                        return self._response(
                            existing_sale,
                            "SALE_PAYMENT_ALREADY_PROCESSED"
                        ), 200

                return Response.error_response(
                    "IDEMPOTENCY_KEY_REUSED",
                    "Client transaction ID has already been used for a different payment",
                    {}
                ), 409

            return Response.error_response(
                "CONFLICT_ERROR",
                "Payment could not be recorded",
                {}
            ), 409

        except Exception:
            db.session.rollback()
            raise

    def payment_history(self):
        store_id = request.args.get("store_id", type=int)
        sale_id = request.args.get("sale_id", type=int)

        if (
            store_id is None
            or sale_id is None
            or store_id <= 0
            or sale_id <= 0
        ):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Store ID and sale ID are required positive integers",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        sale = Sale.query.filter_by(
            id=sale_id,
            store_id=store_id
        ).first()

        if not sale:
            return Response.error_response(
                "SALE_NOT_FOUND",
                "Sale not found",
                {}
            ), 404

        payments = SalePayment.query.filter_by(
            sale_id=sale.id
        ).order_by(
            SalePayment.created_at.asc(),
            SalePayment.id.asc()
        ).all()

        return Response.success_response(
            {
                "sale_id": sale.id,
                "total_amount": str(sale.total_amount),
                "amount_paid": str(sale.amount_paid),
                "balance": str(sale.balance),
                "payments": [
                    {
                        "id": payment.id,
                        "amount": str(payment.amount),
                        "payment_method": payment.payment_method,
                        "created_at": payment.created_at.isoformat(),
                    }
                    for payment in payments
                ],
            },
            "SALE_PAYMENT_HISTORY_RETRIEVED"
        ), 200

    def list_sales(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        payment_method = request.args.get("payment_method")
        group_by = request.args.get("group_by")
        category_id_raw = request.args.get("category_id")
        start_date_raw = request.args.get("start_date")
        end_date_raw = request.args.get("end_date")

        if payment_method is not None and payment_method not in Sale.PAYMENT_METHODS:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Payment method must be one of: Cash, Transfer, POS",
                {}
            ), 400

        if group_by is not None and group_by != "payment_method":
            return Response.error_response(
                "VALIDATION_ERROR",
                "Supported group_by value is: payment_method",
                {}
            ), 400

        category_id = None
        if category_id_raw is not None:
            try:
                category_id = int(category_id_raw)
            except (TypeError, ValueError):
                category_id = None
            if category_id is None or category_id <= 0:
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Category ID must be a positive integer",
                    {}
                ), 400
            category = Category.query.filter_by(
                id=category_id,
                store_id=store_id,
            ).first()
            if not category:
                return Response.error_response(
                    "CATEGORY_NOT_FOUND",
                    "Category not found",
                    {}
                ), 404

        try:
            from datetime import date
            if any(
                raw is not None and (
                    len(raw) != 10 or raw[4:5] != "-" or raw[7:8] != "-"
                )
                for raw in (start_date_raw, end_date_raw)
            ):
                raise ValueError("Dates must use YYYY-MM-DD format")
            start_date = date.fromisoformat(start_date_raw) if start_date_raw else None
            end_date = date.fromisoformat(end_date_raw) if end_date_raw else None
        except (TypeError, ValueError):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Dates must use YYYY-MM-DD format",
                {}
            ), 400

        if start_date and end_date and start_date > end_date:
            return Response.error_response(
                "VALIDATION_ERROR",
                "start_date must be on or before end_date",
                {}
            ), 400

        sales_query = Sale.query.filter(Sale.store_id == store_id)

        if payment_method is not None:
            sales_query = sales_query.filter(Sale.payment_method == payment_method)

        # Date filters are calendar dates in Africa/Lagos. Convert them to UTC
        # half-open bounds so records at the end of the selected day are included.
        from server.app.utils.time import utc_bounds_for_lagos_date
        if start_date:
            start_utc, _ = utc_bounds_for_lagos_date(start_date)
            sales_query = sales_query.filter(Sale.created_at >= start_utc)
        if end_date:
            _, end_utc = utc_bounds_for_lagos_date(end_date)
            sales_query = sales_query.filter(Sale.created_at < end_utc)

        if category_id is not None:
            matching_sale_ids = (
                db.session.query(SaleItem.sale_id)
                .join(Product, Product.id == SaleItem.product_id)
                .filter(
                    Product.store_id == store_id,
                    Product.category_id == category_id,
                )
                .distinct()
            )
            # A sale with multiple lines in the selected category is still one
            # transaction. Totals remain the full sale totals, not partial line totals.
            sales_query = sales_query.filter(Sale.id.in_(matching_sale_ids))

        if group_by == "payment_method":
            grouped_sales = sales_query.with_entities(
                Sale.payment_method,
                func.count(Sale.id).label("sales_count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("total_sales")
            ).group_by(Sale.payment_method).order_by(Sale.payment_method.asc()).all()
            filtered_sales = sales_query.with_entities(Sale.id, Sale.payment_method).all()
            refunds_by_sale = dict(
                db.session.query(Refund.sale_id, func.sum(Refund.amount))
                .filter(Refund.sale_id.in_([sale_id for sale_id, _ in filtered_sales]))
                .group_by(Refund.sale_id)
                .all()
            ) if filtered_sales else {}
            method_refunds = {}
            for sale_id, method in filtered_sales:
                method_refunds[method] = method_refunds.get(method, Decimal("0.00")) + Decimal(str(refunds_by_sale.get(sale_id, 0) or 0))

            return Response.success_response(
                {
                    "group_by": "payment_method",
                    "sales_by_payment_method": [
                        {
                            "payment_method": method,
                            "sales_count": int(count),
                            "total_sales": str(total),
                            "total_refunds": str(method_refunds.get(method, Decimal("0.00"))),
                            "net_sales": str(Decimal(str(total)) - method_refunds.get(method, Decimal("0.00"))),
                        }
                        for method, count, total in grouped_sales
                    ]
                },
                "SALES_GROUPED"
            ), 200

        sales = sales_query.order_by(Sale.created_at.desc(), Sale.id.desc()).all()
        sale_ids = [sale.id for sale in sales]
        refunds_by_sale = dict(
            db.session.query(Refund.sale_id, func.sum(Refund.amount))
            .filter(Refund.sale_id.in_(sale_ids))
            .group_by(Refund.sale_id)
            .all()
        ) if sale_ids else {}
        export_sales = []
        for sale in sales:
            refunded_amount = Decimal(str(refunds_by_sale.get(sale.id, 0) or 0))
            sale_items = (
                db.session.query(SaleItem, Product)
                .join(Product, Product.id == SaleItem.product_id)
                .filter(SaleItem.sale_id == sale.id)
                .order_by(SaleItem.id.asc())
                .all()
            )
            customer = db.session.get(Customer, sale.customer_id) if sale.customer_id else None
            export_sales.append({
                "id": sale.id,
                "client_transaction_id": sale.client_transaction_id,
                "customer_id": sale.customer_id,
                "customer_name": customer.name if customer else None,
                "total_amount": str(sale.total_amount),
                "amount_paid": str(sale.amount_paid),
                "balance": str(sale.balance),
                "refunded_amount": str(refunded_amount),
                "net_amount": str(Decimal(str(sale.total_amount)) - refunded_amount),
                "payment_method": sale.payment_method,
                "created_at": sale.created_at.isoformat(),
                "items": [
                    {
                        "product_id": item.product_id,
                        "product_name": product.name,
                        "category_id": product.category_id,
                        "category_name": (
                            db.session.get(Category, product.category_id).name
                            if product.category_id and db.session.get(Category, product.category_id)
                            else None
                        ),
                        "quantity": item.quantity,
                        "unit_name": item.unit_name_at_sale,
                        "unit_base_quantity": item.unit_base_quantity,
                        "price_at_sale": str(item.price_at_sale),
                        "line_total": str(item.total),
                    }
                    for item, product in sale_items
                ],
            })

        total_sales = sum((Decimal(str(sale.total_amount)) for sale in sales), Decimal("0.00"))
        total_paid = sum((Decimal(str(sale.amount_paid)) for sale in sales), Decimal("0.00"))
        total_balance = sum((Decimal(str(sale.balance)) for sale in sales), Decimal("0.00"))
        total_refunds = sum((Decimal(str(value)) for value in refunds_by_sale.values()), Decimal("0.00"))
        net_sales = total_sales - total_refunds
        net_amount_paid = total_paid - total_refunds

        return Response.success_response(
            {
                "payment_method": payment_method,
                "filters": {
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "category_id": category_id,
                    "payment_method": payment_method,
                },
                "summary": {
                    "transaction_count": len(sales),
                    "total_sales": str(total_sales),
                    "total_amount_paid": str(total_paid),
                    "total_balance": str(total_balance),
                    "total_refunds": str(total_refunds),
                    "net_sales": str(net_sales),
                    "net_amount_paid": str(net_amount_paid),
                },
                "sales": export_sales,
            },
            "SALES_RETRIEVED"
        ), 200

    @staticmethod
    def _default_unit(product):
        unit = ProductUnit.query.filter_by(
            product_id=product.id,
            name=product.base_unit,
        ).first()

        if unit:
            return unit

        return ProductUnit.query.filter_by(
            product_id=product.id,
            base_quantity=1,
        ).order_by(ProductUnit.id.asc()).first()

    @staticmethod
    def _product_default_data(product, **extra):
        unit = SalesService._default_unit(product)
        data = {
            "id": product.id,
            "name": product.name,
            "product_type": product.product_type,
            "base_unit": product.base_unit,
            "price": str(product.price),
            "stock_quantity": product.stock_quantity,
            "low_stock_threshold": product.low_stock_threshold,
            "default_unit": (
                {
                    "id": unit.id,
                    "name": unit.name,
                    "base_quantity": unit.base_quantity,
                    "price": str(unit.price),
                }
                if unit
                else None
            ),
        }
        data.update(extra)
        return data

    def defaults(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        frequent_rows = db.session.execute(
            select(
                SaleItem.product_id,
                func.count(SaleItem.id).label("sale_count"),
                func.sum(SaleItem.quantity).label("quantity_sold"),
                func.max(Sale.created_at).label("last_sold_at"),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(
                Product.store_id == store_id,
                Product.product_type == "Physical",
                Sale.store_id == store_id,
            )
            .group_by(SaleItem.product_id)
            .order_by(
                func.count(SaleItem.id).desc(),
                func.max(Sale.created_at).desc(),
                SaleItem.product_id.asc(),
            )
            .limit(8)
        ).all()

        frequent_products = []
        for product_id, sale_count, quantity_sold, last_sold_at in frequent_rows:
            product = db.session.get(Product, product_id)
            if product:
                frequent_products.append(
                    self._product_default_data(
                        product,
                        sale_count=int(sale_count),
                        quantity_sold=int(quantity_sold),
                        last_sold_at=last_sold_at.isoformat() if last_sold_at else None,
                    )
                )

        service_rows = db.session.execute(
            select(
                Product.id,
                func.max(Sale.created_at).label("last_sold_at"),
            )
            .outerjoin(SaleItem, SaleItem.product_id == Product.id)
            .outerjoin(
                Sale,
                (Sale.id == SaleItem.sale_id) & (Sale.store_id == store_id),
            )
            .where(
                Product.store_id == store_id,
                Product.product_type == "Service",
            )
            .group_by(Product.id)
            .order_by(
                func.max(Sale.created_at).desc(),
                Product.id.asc(),
            )
            .limit(5)
        ).all()

        quick_add_services = []
        for product_id, last_sold_at in service_rows:
            product = db.session.get(Product, product_id)
            if product:
                quick_add_services.append(
                    self._product_default_data(
                        product,
                        last_sold_at=last_sold_at.isoformat() if last_sold_at else None,
                    )
                )

        recent_customer_rows = db.session.execute(
            select(
                Sale.customer_id,
                func.max(Sale.created_at).label("last_purchase_at"),
            )
            .join(Customer, Customer.id == Sale.customer_id)
            .where(
                Customer.store_id == store_id,
                Sale.store_id == store_id,
                Sale.customer_id.is_not(None),
            )
            .group_by(Sale.customer_id)
            .order_by(
                func.max(Sale.created_at).desc(),
                Sale.customer_id.asc(),
            )
            .limit(5)
        ).all()

        recent_customers = []
        for customer_id, last_purchase_at in recent_customer_rows:
            customer = db.session.get(Customer, customer_id)
            if customer:
                recent_customers.append(
                    {
                        "id": customer.id,
                        "name": customer.name,
                        "contact": customer.contact,
                        "outstanding_balance": str(customer.outstanding_balance),
                        "last_purchase_at": last_purchase_at.isoformat(),
                    }
                )

        return Response.success_response(
            {
                "frequent_products": frequent_products,
                "quick_add_services": quick_add_services,
                "recent_customers": recent_customers,
            },
            "SALES_DEFAULTS_RETRIEVED"
        ), 200

    def quick_add(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        latest_item = db.session.execute(
            select(SaleItem)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(Sale.store_id == store_id)
            .order_by(
                Sale.created_at.desc(),
                Sale.id.desc(),
                SaleItem.id.desc()
            )
            .limit(1)
        ).scalar_one_or_none()

        if not latest_item:
            return Response.error_response(
                "QUICK_ADD_NOT_FOUND",
                "No recorded product is available for Quick Add",
                {}
            ), 404

        product = db.session.get(Product, latest_item.product_id)
        sale = db.session.get(Sale, latest_item.sale_id)
        units = ProductUnit.query.filter_by(product_id=product.id).order_by(
            ProductUnit.base_quantity.asc(),
            ProductUnit.id.asc(),
        ).all()

        return Response.success_response(
            {
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "product_type": product.product_type,
                    "base_unit": product.base_unit,
                    "price": str(product.price),
                    "units": [
                        {
                            "id": unit.id,
                            "name": unit.name,
                            "base_quantity": unit.base_quantity,
                            "price": str(unit.price),
                        }
                        for unit in units
                    ],
                    "stock_quantity": product.stock_quantity,
                    "low_stock_threshold": product.low_stock_threshold,
                },
                "source_sale": {
                    "sale_id": sale.id,
                    "quantity": latest_item.quantity,
                    "price_at_sale": str(latest_item.price_at_sale),
                    "recorded_at": sale.created_at.isoformat(),
                },
            },
            "QUICK_ADD_PRODUCT_RETRIEVED"
        ), 200

    def get_sale(self):
        store_id = request.args.get("store_id", type=int)
        sale_id = request.args.get("id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        sale = Sale.query.filter_by(
            id=sale_id,
            store_id=store_id
        ).first()

        if not sale:
            return Response.error_response(
                "SALE_NOT_FOUND",
                "Sale not found",
                {}
            ), 404

        return self._response(
            sale,
            "SALE_FOUND"
        ), 200

    def receipt(self):
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        store_id = data.get("store_id")
        sale_id = data.get("id")

        if not isinstance(store_id, int) or isinstance(store_id, bool):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Store ID must be an integer",
                {}
            ), 400

        if not isinstance(sale_id, int) or isinstance(sale_id, bool):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Sale ID must be an integer",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        sale = Sale.query.filter_by(
            id=sale_id,
            store_id=store_id
        ).first()

        if not sale:
            return Response.error_response(
                "SALE_NOT_FOUND",
                "Sale not found",
                {}
            ), 404

        return Response.success_response(
            {
                "receipt": self._receipt_data(sale)
            },
            "RECEIPT_GENERATED"
        ), 200

    def _receipt_data(self, sale):
        items = SaleItem.query.filter_by(
            sale_id=sale.id
        ).all()

        receipt_items = []

        for item in items:
            product = db.session.get(Product, item.product_id)
            receipt_items.append(
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_id": item.unit_id,
                    "unit_name": item.unit_name_at_sale,
                    "unit_base_quantity": item.unit_base_quantity,
                    "base_quantity_deducted": item.base_quantity_deducted,
                    "base_unit": product.base_unit,
                    "price_at_sale": str(item.price_at_sale),
                    "total": str(item.total)
                }
            )

        store = db.session.get(Store, sale.store_id)
        customer = (
            db.session.get(Customer, sale.customer_id)
            if sale.customer_id is not None
            else None
        )
        total_amount = Decimal(str(sale.total_amount))
        amount_paid = Decimal(str(sale.amount_paid))
        # Derive the receipt balance from the persisted sale totals so the
        # receipt cannot label a partially paid sale as fully paid.
        balance = max(total_amount - amount_paid, Decimal("0.00"))
        if balance == 0:
            payment_status = "Paid"
        elif amount_paid == 0:
            payment_status = "Unpaid"
        else:
            payment_status = "Partially paid"

        printable_lines = [
            "RetailOS Receipt",
            f"Receipt: #{sale.id}",
            f"Date: {sale.created_at.isoformat()}",
            f"Store: {store.name if store else 'Store'}",
            f"Customer: {customer.name if customer else 'Walk-in'}",
            ""
        ]

        for item in receipt_items:
            printable_lines.append(
                f"Product {item['product_id']} x "
                f"{item['quantity']} @ {item['price_at_sale']} = {item['total']}"
            )

        printable_lines.extend(
            [
                "",
                f"Total: {total_amount:.2f}",
                f"Amount paid: {amount_paid:.2f}",
                f"Outstanding balance: {balance:.2f}",
                f"Payment status: {payment_status}",
                f"Payment method: {sale.payment_method}",
            ]
        )

        return {
            "sale_id": sale.id,
            "store_id": sale.store_id,
            "store_name": store.name if store else None,
            "customer_id": sale.customer_id,
            "customer_name": customer.name if customer else None,
            "total_amount": str(total_amount),
            "amount_paid": str(amount_paid),
            "balance": str(balance),
            "payment_status": payment_status,
            "payment_method": sale.payment_method,
            "created_at": sale.created_at.isoformat(),
            "items": receipt_items,
            "printable_text": "\n".join(printable_lines)
        }

    def _response(self, sale, message):
        return Response.success_response(
            {
                "receipt": self._receipt_data(sale)
            },
            message
        )
