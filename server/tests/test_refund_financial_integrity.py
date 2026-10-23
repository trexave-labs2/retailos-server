from decimal import Decimal

from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.refund import Refund, RefundItem
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem


def register_and_login(client, username):
    email = f"{username}@example.com"
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "test123"},
    )
    assert response.status_code == 201
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "test123"},
    )
    assert response.status_code == 200
    return response.json["data"]["user"]["store_id"]


def test_refund_reconciles_net_sales_stock_and_keeps_unpaid_credit_balance_intact(client):
    store_id = register_and_login(client, "refund-credit-integrity")

    product_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Credit Sale Product",
            "price": "600.00",
            "opening_stock": 3,
            "low_stock_threshold": 1,
        },
    )
    assert product_response.status_code == 201
    product_id = product_response.json["data"]["product"]["id"]

    customer = Customer(
        store_id=store_id,
        name="Credit Customer",
        contact="08000000000",
        outstanding_balance=Decimal("0.00"),
    )
    db.session.add(customer)
    db.session.commit()

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer.id,
            "payment_method": "Cash",
            "amount_paid": "300.00",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "refund-credit-sale",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]
    sale_item = SaleItem.query.filter_by(sale_id=sale_id).one()

    # A refund pays back money already received. It must not silently erase
    # the remaining credit debt on the original sale.
    refund_response = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "200.00",
            "reason": "Partial return from a credit sale",
            "payment_method": "Cash",
            "client_transaction_id": "refund-credit-sale-200",
            "returned_items": [{"sale_item_id": sale_item.id, "quantity": 1}],
        },
    )

    assert refund_response.status_code == 201
    assert refund_response.json["data"]["refund"]["amount"] == "200.00"

    db.session.refresh(customer)
    product = db.session.get(Product, product_id)
    db.session.refresh(product)
    sale = db.session.get(Sale, sale_id)

    assert sale.total_amount == Decimal("600.00")
    assert sale.amount_paid == Decimal("300.00")
    assert sale.balance == Decimal("300.00")
    assert customer.outstanding_balance == Decimal("300.00")
    assert product.stock_quantity == 3

    movement = InventoryMovement.query.filter_by(
        product_id=product_id,
        movement_type="REFUND",
    ).one()
    assert movement.quantity_change == 1
    assert movement.previous_quantity == 2
    assert movement.new_quantity == 3
    assert Refund.query.filter_by(sale_id=sale_id).count() == 1
    assert RefundItem.query.filter_by(sale_item_id=sale_item.id).one().quantity == 1

    sales_response = client.get(f"/sales?store_id={store_id}")
    assert sales_response.status_code == 200
    summary = sales_response.json["data"]["summary"]
    assert summary["total_sales"] == "600.00"
    assert summary["total_refunds"] == "200.00"
    assert summary["net_sales"] == "400.00"
    assert summary["total_balance"] == "300.00"


def test_refund_cannot_exceed_money_received_on_a_credit_sale(client):
    store_id = register_and_login(client, "refund-credit-limit")
    product_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Unpaid Product",
            "price": "600.00",
            "opening_stock": 3,
            "low_stock_threshold": 1,
        },
    )
    assert product_response.status_code == 201
    product_id = product_response.json["data"]["product"]["id"]

    customer = Customer(
        store_id=store_id,
        name="Unpaid Customer",
        outstanding_balance=Decimal("0.00"),
    )
    db.session.add(customer)
    db.session.commit()

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer.id,
            "payment_method": "Cash",
            "amount_paid": "0.00",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "refund-unpaid-sale",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    # Omitting amount_paid means "paid in full" in the sales API.
    # Explicitly record zero received to create a genuinely unpaid credit sale.
    sale = db.session.get(Sale, sale_id)
    assert sale.amount_paid == Decimal("0.00")
    assert sale.balance == Decimal("600.00")
    db.session.refresh(customer)
    assert customer.outstanding_balance == Decimal("600.00")

    response = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "0.01",
            "reason": "Attempt to refund an unpaid sale",
            "payment_method": "Cash",
            "client_transaction_id": "refund-unpaid-sale-attempt",
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "REFUND_EXCEEDS_REMAINING_AMOUNT"
    assert Refund.query.filter_by(sale_id=sale_id).count() == 0
    assert db.session.get(Product, product_id).stock_quantity == 2
    db.session.refresh(customer)
    assert customer.outstanding_balance == Decimal("600.00")
    sale = db.session.get(Sale, sale_id)
    assert sale.balance == Decimal("600.00")
