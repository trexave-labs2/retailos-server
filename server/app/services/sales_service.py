# server/app/services/sales_service.py
from decimal import Decimal

from flask import request, session
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc


class SalesService:
    def create_sale(self):
        data = request.get_json(silent=False)

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

            old_sale = Sale.query.filter_by(
                store_id=store_id,
                client_transaction_id=client_transaction_id
            ).first()

            if old_sale:
                return self._response(
                    old_sale,
                    "SALE_ALREADY_PROCESSED"
                ), 200

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

            db.session.commit()

            return self._response(
                sale,
                "SALE_CREATED"
            ), 201

        except IntegrityError:
            db.session.rollback()

            if client_transaction_id:
                existing_sale = Sale.query.filter_by(
                    store_id=store_id,
                    client_transaction_id=client_transaction_id
                ).first()

                if existing_sale:
                    return self._response(
                        existing_sale,
                        "SALE_ALREADY_PROCESSED"
                    ), 200

            return Response.error_response(
                "CONFLICT_ERROR",
                "Sale could not be completed",
                {}
            ), 409

        except Exception:
            db.session.rollback()
            raise

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

        sales_query = Sale.query.filter(
            Sale.store_id == store_id
        )

        if payment_method is not None:
            sales_query = sales_query.filter(
                Sale.payment_method == payment_method
            )

        if group_by == "payment_method":
            grouped_sales = sales_query.with_entities(
                Sale.payment_method,
                func.count(Sale.id).label("sales_count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("total_sales")
            ).group_by(
                Sale.payment_method
            ).order_by(
                Sale.payment_method.asc()
            ).all()

            return Response.success_response(
                {
                    "group_by": "payment_method",
                    "sales_by_payment_method": [
                        {
                            "payment_method": method,
                            "sales_count": int(count),
                            "total_sales": str(total)
                        }
                        for method, count, total in grouped_sales
                    ]
                },
                "SALES_GROUPED"
            ), 200

        sales = sales_query.order_by(Sale.created_at.desc()).all()

        return Response.success_response(
            {
                "payment_method": payment_method,
                "sales": [
                    {
                        "id": sale.id,
                        "client_transaction_id": sale.client_transaction_id,
                        "customer_id": sale.customer_id,
                        "total_amount": str(sale.total_amount),
                        "amount_paid": str(sale.amount_paid),
                        "balance": str(sale.balance),
                        "payment_method": sale.payment_method,
                        "created_at": sale.created_at.isoformat()
                    }
                    for sale in sales
                ]
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
        data = request.get_json(silent=False)

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

        printable_lines = [
            "RetailOS Receipt",
            f"Sale: #{sale.id}",
            f"Date: {sale.created_at.isoformat()}",
            ""
        ]

        for item in receipt_items:
            printable_lines.append(
                f"Product {item['product_id']} x "
                f"{item['quantity']} = {item['total']}"
            )

        printable_lines.extend(
            [
                "",
                f"Total: {sale.total_amount}"
            ]
        )

        return {
            "sale_id": sale.id,
            "customer_id": sale.customer_id,
            "total_amount": str(sale.total_amount),
            "amount_paid": str(sale.amount_paid),
            "balance": str(sale.balance),
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
