# server/app/services/customer_service.py
from decimal import Decimal

from flask import request
from sqlalchemy import or_

from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.validators import validate_required_string


class CustomerService:
    def _get_customer(self, customer_id, store_id):
        return Customer.query.filter_by(
            id=customer_id,
            store_id=store_id
        ).first()

    def list_customers(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        search = request.args.get("search")
        customers_query = Customer.query.filter_by(store_id=store_id)

        if search is not None:
            search = search.strip()

            if search:
                pattern = f"%{search}%"
                customers_query = customers_query.filter(
                    or_(
                        Customer.name.ilike(pattern),
                        Customer.contact.ilike(pattern)
                    )
                )

        customers = customers_query.order_by(Customer.name.asc()).all()

        return Response.success_response(
            {
                "search": search,
                "customers": [
                    {
                        "id": customer.id,
                        "name": customer.name,
                        "contact": customer.contact,
                        "outstanding_balance": str(customer.outstanding_balance),
                        "created_at": customer.created_at.isoformat()
                    }
                    for customer in customers
                ]
            },
            "CUSTOMERS_RETRIEVED"
        ), 200

    def create_customer(self):
        data = request.get_json(silent=False)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        store_id = data.get("store_id")
        name = data.get("name")
        contact = data.get("contact")

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        error = validate_required_string(
            name,
            "Customer name"
        )

        if error:
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {
                    "name": error
                }
            ), 400

        customer = Customer(
            store_id=store_id,
            name=name.strip(),
            contact=contact
        )

        db.session.add(customer)
        db.session.commit()

        return Response.success_response(
            {
                "customer": {
                    "id": customer.id,
                    "name": customer.name,
                    "contact": customer.contact,
                    "outstanding_balance": str(customer.outstanding_balance)
                }
            },
            "CUSTOMER_CREATED"
        ), 201

    def update_customer(self):
        data = request.get_json(silent=False)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        customer_id = data.get("id")
        store_id = data.get("store_id")

        if (
            not isinstance(customer_id, int)
            or isinstance(customer_id, bool)
            or not isinstance(store_id, int)
            or isinstance(store_id, bool)
        ):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Customer ID and store ID must be integers",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        customer = self._get_customer(customer_id, store_id)

        if not customer:
            return Response.error_response(
                "CUSTOMER_NOT_FOUND",
                "Customer not found",
                {}
            ), 404

        has_name = "name" in data
        has_contact = "contact" in data

        if not has_name and not has_contact:
            return Response.error_response(
                "VALIDATION_ERROR",
                "At least one customer field must be provided",
                {}
            ), 400

        if has_name:
            error = validate_required_string(
                data.get("name"),
                "Customer name"
            )

            if error:
                return Response.error_response(
                    "VALIDATION_ERROR",
                    "Invalid input data",
                    {"name": error}
                ), 400

            customer.name = data["name"].strip()

        if has_contact:
            customer.contact = data.get("contact")

        db.session.commit()

        return Response.success_response(
            {
                "customer": {
                    "id": customer.id,
                    "name": customer.name,
                    "contact": customer.contact,
                    "outstanding_balance": str(customer.outstanding_balance)
                }
            },
            "CUSTOMER_UPDATED"
        ), 200

    def delete_customer(self):
        data = request.get_json(silent=False)

        if not isinstance(data, dict):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Invalid input data",
                {}
            ), 400

        customer_id = data.get("id")
        store_id = data.get("store_id")

        if (
            not isinstance(customer_id, int)
            or isinstance(customer_id, bool)
            or not isinstance(store_id, int)
            or isinstance(store_id, bool)
        ):
            return Response.error_response(
                "VALIDATION_ERROR",
                "Customer ID and store ID must be integers",
                {}
            ), 400

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        customer = self._get_customer(customer_id, store_id)

        if not customer:
            return Response.error_response(
                "CUSTOMER_NOT_FOUND",
                "Customer not found",
                {}
            ), 404

        sale_count = Sale.query.filter_by(
            customer_id=customer.id,
            store_id=store_id
        ).count()

        if sale_count:
            return Response.error_response(
                "CUSTOMER_HAS_SALES",
                "Customer cannot be deleted because sales are linked to this customer",
                {"sale_count": sale_count}
            ), 409

        db.session.delete(customer)
        db.session.commit()

        return Response.success_response(
            {"customer_id": customer_id},
            "CUSTOMER_DELETED"
        ), 200

    def list_customers_with_balance(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        customers = Customer.query.filter(
            Customer.store_id == store_id,
            Customer.outstanding_balance > 0
        ).order_by(
            Customer.outstanding_balance.desc(),
            Customer.name.asc()
        ).all()

        return Response.success_response(
            {
                "customers": [
                    {
                        "id": customer.id,
                        "name": customer.name,
                        "contact": customer.contact,
                        "outstanding_balance": str(customer.outstanding_balance)
                    }
                    for customer in customers
                ],
                "total_outstanding": str(
                    sum(
                        (customer.outstanding_balance for customer in customers),
                        Decimal("0.00")
                    )
                )
            },
            "CUSTOMERS_WITH_BALANCES_RETRIEVED"
        ), 200

    def get_customer_history(self):
        customer_id = request.args.get("id", type=int)
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

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

        sales = Sale.query.filter_by(
            customer_id=customer.id,
            store_id=store_id
        ).order_by(Sale.created_at.desc()).all()

        history = []

        for sale in sales:
            items = SaleItem.query.filter_by(
                sale_id=sale.id
            ).all()

            history.append(
                {
                    "sale_id": sale.id,
                    "total_amount": str(sale.total_amount),
                    "amount_paid": str(sale.amount_paid),
                    "balance": str(sale.balance),
                    "created_at": sale.created_at.isoformat(),
                    "items": [
                        {
                            "product_id": item.product_id,
                            "quantity": item.quantity,
                            "total": str(item.total)
                        }
                        for item in items
                    ]
                }
            )

        return Response.success_response(
            {
                "customer": {
                    "id": customer.id,
                    "name": customer.name,
                    "contact": customer.contact,
                    "outstanding_balance": str(customer.outstanding_balance),
                    "purchase_history": history
                }
            },
            "CUSTOMER_HISTORY_RETRIEVED"
        ), 200
