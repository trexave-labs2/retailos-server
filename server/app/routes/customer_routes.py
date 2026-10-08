from flask import Blueprint
from server.app.middleware.auth_middleware import require_session
from server.app.services.customer_service import CustomerService

customer = CustomerService()
customer_bp = Blueprint("customer", __name__)


@customer_bp.route("", methods=["GET"])
@require_session
def list_customers():
    return customer.list_customers()


@customer_bp.route("", methods=["POST"])
@require_session
def create_customer():
    return customer.create_customer()


@customer_bp.route("", methods=["PATCH"])
@require_session
def update_customer():
    return customer.update_customer()


@customer_bp.route("", methods=["DELETE"])
@require_session
def delete_customer():
    return customer.delete_customer()


@customer_bp.route("/owes", methods=["GET"])
@require_session
def customers_with_balances():
    return customer.list_customers_with_balance()


@customer_bp.route("/history", methods=["GET"])
@require_session
def customer_history():
    return customer.get_customer_history()
