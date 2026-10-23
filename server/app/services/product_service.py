# server/app/services/product_service.py
from decimal import Decimal
import re

from flask import request, session
from sqlalchemy.exc import IntegrityError

from server.app.extensions import db
from server.app.services.alert_service import AlertService
from server.app.services.audit_service import AuditService
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.category import Category
from server.app.models.product_unit import ProductUnit
from server.app.models.stock_receiving import StockReceiving
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.validators import (
    validate_non_negative_number,
    validate_positive_integer,
    validate_required_string
)


class ProductService:
    UNIT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,19}$")

    @classmethod
    def _valid_unit_name(cls, name):
        return (
            isinstance(name, str)
            and name == name.strip()
            and bool(cls.UNIT_NAME_PATTERN.fullmatch(name))
        )

    @staticmethod
    def query_for_store(store_id):
        """Return the canonical product query used for store product reads."""
        return Product.query.filter_by(store_id=store_id)

    def _unit_data(self, unit):
        return {
            "id": unit.id,
            "name": unit.name,
            "base_quantity": unit.base_quantity,
            "price": str(unit.price),
        }

    def _data(self, product):
        units = ProductUnit.query.filter_by(product_id=product.id).order_by(
            ProductUnit.base_quantity.asc(),
            ProductUnit.id.asc(),
        ).all()
        inventory_tracked = product.product_type == "Physical"
        stock_quantity = product.stock_quantity if inventory_tracked else 0
        low_stock_threshold = product.low_stock_threshold if inventory_tracked else None

        return {
            "id": product.id,
            "store_id": product.store_id,
            "name": product.name,
            "product_type": product.product_type,
            "category_id": product.category_id,
            "base_unit": product.base_unit,
            "price": str(product.price),
            "units": [self._unit_data(unit) for unit in units],
            "stock_quantity": stock_quantity,
            "low_stock_threshold": low_stock_threshold,
            "stock": {
                "quantity": stock_quantity,
                "base_unit": product.base_unit,
                "low_stock_threshold": low_stock_threshold
            },
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat()
        }

    def _validate_units(self, units):
        if not isinstance(units, list) or not units:
            return "Units must be a non-empty list"

        names = set()
        for unit in units:
            if not isinstance(unit, dict):
                return "Each unit must be an object"

            name = unit.get("name")
            if not isinstance(name, str) or not name.strip():
                return "Unit name must be a non-empty string"
            name = name.strip()
            if not self._valid_unit_name(name):
                return (
                    "Unit names must start with a letter and contain only letters, "
                    "numbers, spaces, underscores, or hyphens, up to 20 characters"
                )
            if name.lower() in names:
                return "Unit names must be unique"
            names.add(name.lower())

            base_quantity = unit.get("base_quantity")
            if (
                not isinstance(base_quantity, int)
                or isinstance(base_quantity, bool)
                or base_quantity <= 0
            ):
                return "Unit base quantity must be a positive integer"

            error = validate_non_negative_number(unit.get("price"), "Unit price")
            if error:
                return error

        return None

    def _movement_data(self, movement):
        return {
            "id": movement.id,
            "store_id": movement.store_id,
            "product_id": movement.product_id,
            "base_unit": db.session.get(Product, movement.product_id).base_unit,
            "user_id": movement.user_id,
            "movement_type": movement.movement_type,
            "quantity_change": movement.quantity_change,
            "previous_quantity": movement.previous_quantity,
            "new_quantity": movement.new_quantity,
            "reason": movement.reason,
            "created_at": movement.created_at.isoformat()
        }

    def _get_data(self):
        data = request.get_json(silent=False)

        if not isinstance(data, dict):
            return (
                None,
                Response.error_response(
                    "VALIDATION_ERROR",
                    "Invalid input data",
                    {
                        "payload": "Payload must be a JSON object"
                    }
                ),
                400
            )

        return data, None, None

    def create_product(self):
        try:
            data, error, status = self._get_data()

            if error:
                return error, status

            store_id = data.get("store_id")
            name = data.get("name")
            price = data.get("price")
            opening_stock = data.get("opening_stock", 0)
            threshold = data.get("low_stock_threshold")
            product_type = data.get("product_type", "Physical")
            category_id = data.get("category_id")
            base_unit = data.get("base_unit", "piece")
            units = data.get("units")

            fields = {}

            if product_type not in Product.PRODUCT_TYPES:
                fields["product_type"] = "Product type must be one of: Physical, Service"

            if category_id is not None:
                if not isinstance(category_id, int) or isinstance(category_id, bool):
                    fields["category_id"] = "Category ID must be an integer or null"
                else:
                    category = Category.query.filter_by(id=category_id, store_id=store_id).first()
                    if not category:
                        fields["category_id"] = "Category does not belong to this store"
                    elif category.item_type != product_type:
                        fields["category_id"] = "Category item type must match product type"

            if "stock_quantity" in data:
                fields["opening_stock"] = (
                    "Use 'opening_stock' when creating a product"
                )

            if not isinstance(base_unit, str) or not base_unit.strip():
                fields["base_unit"] = "Base unit must be a non-empty string"
            elif not self._valid_unit_name(base_unit.strip()):
                fields["base_unit"] = (
                    "Base unit must start with a letter and contain only letters, "
                    "numbers, spaces, underscores, or hyphens, up to 20 characters"
                )

            if not isinstance(store_id, int):
                fields["store_id"] = "Store ID must be an integer"
            elif not get_authorized_store(store_id):
                fields["store_id"] = "You do not have access to this store"

            error = validate_required_string(name, "Product name")

            if error:
                fields["name"] = error

            error = validate_non_negative_number(price, "Price")

            if error:
                fields["price"] = error

            error = validate_positive_integer(
                opening_stock,
                "Opening stock",
                True
            )

            if error:
                fields["opening_stock"] = error

            if product_type == "Service":
                if opening_stock != 0:
                    fields["opening_stock"] = "Services cannot have opening stock"
                if threshold is not None:
                    fields["low_stock_threshold"] = "Services cannot have a low-stock threshold"
            elif threshold is not None:
                error = validate_positive_integer(
                    threshold,
                    "Low stock threshold",
                    True
                )

                if error:
                    fields["low_stock_threshold"] = error

            if units is not None:
                error = self._validate_units(units)
                if error:
                    fields["units"] = error
                else:
                    base_matches = [
                        unit for unit in units
                        if unit["name"].strip().lower() == base_unit.strip().lower()
                    ]
                    if base_matches and base_matches[0]["base_quantity"] != 1:
                        fields["units"] = "The base unit must have a conversion quantity of 1"
                    elif base_matches:
                        base_matches[0]["name"] = base_unit.strip()

            if fields:
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Invalid input data",
                    fields
                ), 400

            from server.app.services.subscription_service import SubscriptionService
            limit_error = SubscriptionService.check_limit(store_id, "products")
            if limit_error:
                return limit_error

            product = Product(
                store_id=store_id,
                name=name.strip(),
                product_type=product_type,
                base_unit=base_unit.strip(),
                price=Decimal(str(price)),
                stock_quantity=opening_stock if product_type == "Physical" else 0,
                low_stock_threshold=threshold if product_type == "Physical" else None,
                category_id=category_id
            )

            db.session.add(product)
            db.session.flush()

            if units is None:
                units = [
                    {
                        "name": base_unit.strip(),
                        "base_quantity": 1,
                        "price": price,
                    }
                ]

            if not any(
                unit_data["name"].strip().lower() == base_unit.strip().lower()
                for unit_data in units
            ):
                units.append({
                    "name": base_unit.strip(),
                    "base_quantity": 1,
                    "price": price,
                })

            for unit_data in units:
                db.session.add(
                    ProductUnit(
                        product_id=product.id,
                        name=unit_data["name"].strip(),
                        base_quantity=unit_data["base_quantity"],
                        price=Decimal(str(unit_data["price"])),
                    )
                )

            AlertService().sync_low_stock_alert(product)
            db.session.commit()

            return Response.success_response(
                {
                    "product": self._data(product)
                },
                "PRODUCT_CREATED"
            ), 201

        except IntegrityError:
            db.session.rollback()

            return Response.error_response(
                "CONFLICT_ERROR",
                "Product could not be created",
                {}
            ), 409

    def get_product(self):
        product_id = request.args.get("id", type=int)
        store_id = request.args.get("store_id", type=int)

        if not product_id or not store_id:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Product ID and store ID are required",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        product = Product.query.filter_by(
            id=product_id,
            store_id=store_id
        ).first()

        if not product:
            return Response.error_response(
                "PRODUCT_NOT_FOUND",
                "Product not found",
                {}
            ), 404

        return Response.success_response(
            {
                "product": self._data(product)
            },
            "PRODUCT_FOUND"
        ), 200

    def list_products(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        category_id = request.args.get("category_id", type=int)
        query = self.query_for_store(store_id)
        if category_id is not None:
            category = Category.query.filter_by(id=category_id, store_id=store_id).first()
            if not category:
                return Response.error_response(
                    "CATEGORY_NOT_FOUND",
                    "Category not found",
                    {},
                ), 404
            query = query.filter_by(category_id=category_id)
        products = query.order_by(Product.name.asc()).all()

        return Response.success_response(
            {
                "products": [
                    self._data(product)
                    for product in products
                ]
            },
            "PRODUCTS_RETRIEVED"
        ), 200

    def update_product(self):
        try:
            data, error, status = self._get_data()

            if error:
                return error, status

            store_id = data.get("store_id")
            product_id = data.get("id")

            if not get_authorized_store(store_id):
                return Response.error_response(
                    "STORE_ACCESS_DENIED",
                    "You do not have access to this store",
                    {}
                ), 403

            product = Product.query.filter_by(
                id=product_id,
                store_id=store_id
            ).first()

            if not product:
                return Response.error_response(
                    "PRODUCT_NOT_FOUND",
                    "Product not found",
                    {}
                ), 404

            previous_price = str(product.price)
            fields = {}
            requested_type = data.get("product_type", product.product_type)
            effective_category_id = data.get("category_id", product.category_id)
            if effective_category_id is not None:
                if not isinstance(effective_category_id, int) or isinstance(effective_category_id, bool):
                    fields["category_id"] = "Category ID must be an integer or null"
                else:
                    category = Category.query.filter_by(
                        id=effective_category_id,
                        store_id=store_id,
                    ).first()
                    if not category:
                        fields["category_id"] = "Category does not belong to this store"
                    elif category.item_type != requested_type:
                        fields["category_id"] = "Category item type must match product type"

            if requested_type not in Product.PRODUCT_TYPES:
                fields["product_type"] = "Product type must be one of: Physical, Service"
            elif product.product_type == "Physical" and requested_type == "Service":
                if product.stock_quantity > 0:
                    fields["product_type"] = (
                        "Stock must be reduced to zero through stock adjustments before converting to a service"
                    )
            elif product.product_type == "Service" and requested_type == "Physical":
                if "stock_quantity" not in data or "base_unit" not in data:
                    fields["product_type"] = (
                        "Converting a service to a physical product requires stock_quantity and base_unit"
                    )

            if requested_type == "Service":
                if "stock_quantity" in data and data["stock_quantity"] != 0:
                    fields["stock_quantity"] = "Services cannot have stock quantity"
                if "low_stock_threshold" in data and data["low_stock_threshold"] is not None:
                    fields["low_stock_threshold"] = "Services cannot have a low-stock threshold"

            if fields:
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Invalid input data",
                    fields
                ), 400

            if "category_id" in data:
                product.category_id = data["category_id"]

            if "name" in data:
                error = validate_required_string(
                    data["name"],
                    "Product name"
                )

                if error:
                    fields["name"] = error
                else:
                    product.name = data["name"].strip()

            if "base_unit" in data:
                base_unit = data["base_unit"]
                if not isinstance(base_unit, str) or not base_unit.strip():
                    fields["base_unit"] = "Base unit must be a non-empty string"
                elif not self._valid_unit_name(base_unit.strip()):
                    fields["base_unit"] = (
                        "Base unit must start with a letter and contain only letters, "
                        "numbers, spaces, underscores, or hyphens, up to 20 characters"
                    )
                elif base_unit.strip().lower() != product.base_unit.lower():
                    from server.app.models.sale_item import SaleItem
                    has_sales = SaleItem.query.filter_by(product_id=product.id).first() is not None
                    has_receivings = StockReceiving.query.filter_by(product_id=product.id).first() is not None
                    has_movements = InventoryMovement.query.filter_by(product_id=product.id).first() is not None
                    if product.stock_quantity > 0 or has_sales or has_receivings or has_movements:
                        fields["base_unit"] = (
                            "Base unit cannot change after stock or inventory history exists; "
                            "create a new product to avoid reinterpreting saved quantities"
                        )
                    else:
                        matching_unit = ProductUnit.query.filter_by(
                            product_id=product.id,
                            name=base_unit.strip(),
                        ).first()
                        if matching_unit and matching_unit.base_quantity != 1:
                            fields["base_unit"] = "The base unit must have a conversion quantity of 1"
                        else:
                            product.base_unit = base_unit.strip()
                            if not matching_unit:
                                db.session.add(ProductUnit(
                                    product_id=product.id,
                                    name=base_unit.strip(),
                                    base_quantity=1,
                                    price=product.price,
                                ))

            if "product_type" in data:
                product.product_type = requested_type

            # Services never track inventory. Normalize legacy values whenever
            # a service is updated, without deleting historical movement records.
            if requested_type == "Service":
                product.stock_quantity = 0
                product.low_stock_threshold = None

            if "price" in data:
                error = validate_non_negative_number(
                    data["price"],
                    "Price"
                )

                if error:
                    fields["price"] = error
                else:
                    product.price = Decimal(str(data["price"]))

            if "stock_quantity" in data:
                error = validate_positive_integer(
                    data["stock_quantity"],
                    "Stock quantity",
                    True
                )

                if error:
                    fields["stock_quantity"] = error
                else:
                    product.stock_quantity = data["stock_quantity"]

            if "low_stock_threshold" in data:
                error = validate_positive_integer(
                    data["low_stock_threshold"],
                    "Low stock threshold",
                    True
                )

                if error:
                    fields["low_stock_threshold"] = error
                else:
                    product.low_stock_threshold = data["low_stock_threshold"]

            if "units" in data:
                error = self._validate_units(data["units"])
                if error:
                    fields["units"] = error
                else:
                    base_unit_name = product.base_unit
                    base_matches = [
                        unit for unit in data["units"]
                        if unit["name"].strip().lower() == base_unit_name.lower()
                    ]
                    if base_matches and base_matches[0]["base_quantity"] != 1:
                        fields["units"] = "The base unit must have a conversion quantity of 1"
                    elif base_matches:
                        base_matches[0]["name"] = base_unit_name
                    else:
                        data["units"].append({
                            "name": base_unit_name,
                            "base_quantity": 1,
                            "price": str(product.price),
                        })

                    if "units" not in fields:
                        existing_units = {
                            unit.name.lower(): unit
                            for unit in ProductUnit.query.filter_by(product_id=product.id).all()
                        }
                        for unit_data in data["units"]:
                            name = unit_data["name"].strip()
                            unit = existing_units.get(name.lower())
                            if unit is None:
                                unit = ProductUnit(
                                    product_id=product.id,
                                    name=name,
                                )
                                db.session.add(unit)

                            unit.base_quantity = unit_data["base_quantity"]
                            unit.price = Decimal(str(unit_data["price"]))

            if fields:
                db.session.rollback()
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Invalid input data",
                    fields
                ), 400

            AlertService().sync_low_stock_alert(product)
            if str(product.price) != previous_price:
                AuditService.record(
                    store_id=store_id,
                    action_type="PRODUCT_PRICE_CHANGED",
                    entity_type="product",
                    entity_id=product.id,
                    before={"price": previous_price},
                    after={"price": str(product.price)},
                    details={"product_name": product.name},
                )
            db.session.commit()

            return Response.success_response(
                {
                    "product": self._data(product)
                },
                "PRODUCT_UPDATED"
            ), 200

        except IntegrityError:
            db.session.rollback()

            return Response.error_response(
                "CONFLICT_ERROR",
                "Product could not be updated",
                {}
            ), 409

    def delete_product(self):
        data, error, status = self._get_data()

        if error:
            return error, status

        store_id = data.get("store_id")
        product_id = data.get("id")

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        product = Product.query.filter_by(
            id=product_id,
            store_id=store_id
        ).first()

        if not product:
            return Response.error_response(
                "PRODUCT_NOT_FOUND",
                "Product not found",
                {}
            ), 404

        # Preserve acquisition-cost and receiving history. SQLite deployments may
        # not enforce foreign keys unless explicitly enabled, so enforce this
        # domain rule before attempting deletion rather than relying only on RESTRICT.
        if StockReceiving.query.filter_by(
            store_id=store_id,
            product_id=product_id,
        ).first():
            return Response.error_response(
                "PRODUCT_HAS_STOCK_RECEIVINGS",
                "Product cannot be deleted because it has stock receiving history",
                {},
            ), 409

        try:
            db.session.delete(product)
            db.session.commit()

            return Response.success_response(
                message="PRODUCT_DELETED"
            ), 200

        except IntegrityError:
            db.session.rollback()

            return Response.error_response(
                "CONFLICT_ERROR",
                "Product cannot be deleted because it has related records",
                {}
            ), 409

    def adjust_stock(self):
        data, error, status = self._get_data()

        if error:
            return error, status

        store_id = data.get("store_id")
        product_id = data.get("id")
        quantity_change = data.get("quantity_change")

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        if (
            not isinstance(quantity_change, int)
            or isinstance(quantity_change, bool)
        ):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {
                    "quantity_change": "Quantity change must be an integer"
                }
            ), 400

        product = Product.query.filter_by(
            id=product_id,
            store_id=store_id
        ).first()

        if not product:
            return Response.error_response(
                "PRODUCT_NOT_FOUND",
                "Product not found",
                {}
            ), 404

        if product.product_type != "Physical":
            return Response.error_response(
                "VALIDATION_ERROR",
                "Stock adjustments are only available for physical products",
                {"product_type": "Services do not track inventory"}
            ), 400

        new_quantity = product.stock_quantity + quantity_change

        if new_quantity < 0:
            return Response.error_response(
                "INSUFFICIENT_STOCK",
                "Stock quantity cannot become negative",
                {}
            ), 400

        previous_quantity = product.stock_quantity
        product.stock_quantity = new_quantity

        movement = InventoryMovement(
            store_id=store_id,
            product_id=product.id,
            user_id=session.get("user_id"),
            movement_type="ADJUSTMENT",
            quantity_change=quantity_change,
            previous_quantity=previous_quantity,
            new_quantity=new_quantity,
            reason=data.get(
                "reason",
                "Manual stock adjustment"
            )
        )

        db.session.add(movement)
        AlertService().sync_low_stock_alert(product)
        AuditService.record(
            store_id=store_id,
            action_type="STOCK_ADJUSTED",
            entity_type="product",
            entity_id=product.id,
            before={"stock_quantity": previous_quantity},
            after={"stock_quantity": new_quantity},
            details={"quantity_change": quantity_change, "reason": movement.reason},
        )
        db.session.commit()

        return Response.success_response(
            {
                "product": self._data(product)
            },
            "STOCK_ADJUSTED"
        ), 200

    def list_inventory_movements(self):
        store_id = request.args.get("store_id", type=int)
        product_id = request.args.get("product_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        query = InventoryMovement.query.filter_by(
            store_id=store_id
        )

        if product_id:
            product = Product.query.filter_by(
                id=product_id,
                store_id=store_id
            ).first()

            if not product:
                return Response.error_response(
                    "PRODUCT_NOT_FOUND",
                    "Product not found",
                    {}
                ), 404

            query = query.filter_by(product_id=product_id)

        movements = query.order_by(
            InventoryMovement.created_at.desc()
        ).all()

        return Response.success_response(
            {
                "movements": [
                    self._movement_data(movement)
                    for movement in movements
                ]
            },
            "INVENTORY_MOVEMENTS_RETRIEVED"
        ), 200
