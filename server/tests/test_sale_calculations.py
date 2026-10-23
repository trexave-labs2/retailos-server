from decimal import Decimal

from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.models.sale_payment import SalePayment


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


def create_customer(client, store_id):
    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "Calculation Test Customer",
            "contact": "08011112222",
        },
    )
    assert response.status_code == 201
    return response.json["data"]["customer"]["id"]


def create_product(client, store_id, name, stock, units):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": units[0]["price"],
            "opening_stock": stock,
            "base_unit": units[0]["name"],
            "units": units,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.json["data"]["product"]


def test_mixed_unit_sale_calculates_total_stock_credit_and_part_payments(client):
    store_id = register_and_login(client, "sale-calculation-user")
    customer_id = create_customer(client, store_id)
    drinks = create_product(
        client,
        store_id,
        "Soft Drink",
        24,
        [
            {"name": "piece", "base_quantity": 1, "price": 300},
            {"name": "pack", "base_quantity": 6, "price": 1650},
        ],
    )
    bread = create_product(
        client,
        store_id,
        "Bread",
        10,
        [{"name": "loaf", "base_quantity": 1, "price": 250}],
    )
    pack_id = next(unit["id"] for unit in drinks["units"] if unit["name"] == "pack")

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "amount_paid": "1700.00",
            "client_transaction_id": "sale-calculation-mixed-units-001",
            "items": [
                {"product_id": drinks["id"], "unit_id": pack_id, "quantity": 2},
                {"product_id": bread["id"], "quantity": 3},
            ],
        },
    )

    assert response.status_code == 201, response.get_json()
    receipt = response.json["data"]["receipt"]
    sale_id = receipt["sale_id"]

    assert receipt["total_amount"] == "4050.00"
    assert receipt["amount_paid"] == "1700.00"
    assert receipt["balance"] == "2350.00"
    assert receipt["payment_status"] == "Partially paid"
    assert [(item["quantity"], item["total"]) for item in receipt["items"]] == [
        (2, "3300.00"),
        (3, "750.00"),
    ]

    saved_drinks = db.session.get(Product, drinks["id"])
    saved_bread = db.session.get(Product, bread["id"])
    customer = db.session.get(Customer, customer_id)
    sale = db.session.get(Sale, sale_id)

    assert saved_drinks.stock_quantity == 12
    assert saved_bread.stock_quantity == 7
    assert sale.total_amount == Decimal("4050.00")
    assert sale.amount_paid == Decimal("1700.00")
    assert sale.balance == Decimal("2350.00")
    assert customer.outstanding_balance == Decimal("2350.00")

    movements = {
        movement.product_id: movement
        for movement in InventoryMovement.query.filter_by(store_id=store_id).all()
    }
    assert movements[drinks["id"]].quantity_change == -12
    assert movements[drinks["id"]].previous_quantity == 24
    assert movements[drinks["id"]].new_quantity == 12
    assert movements[bread["id"]].quantity_change == -3
    assert movements[bread["id"]].previous_quantity == 10
    assert movements[bread["id"]].new_quantity == 7

    items = SaleItem.query.filter_by(sale_id=sale_id).order_by(SaleItem.id).all()
    assert [item.total for item in items] == [
        Decimal("3300.00"),
        Decimal("750.00"),
    ]
    assert items[0].unit_base_quantity == 6
    assert items[0].base_quantity_deducted == 12

    initial_payment = SalePayment.query.filter_by(sale_id=sale_id).one()
    assert initial_payment.amount == Decimal("1700.00")
    assert initial_payment.payment_method == "Cash"

    payment_response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "2350.00",
            "payment_method": "Transfer",
            "client_transaction_id": "sale-calculation-settlement-001",
        },
    )

    assert payment_response.status_code == 200, payment_response.get_json()
    settled = payment_response.json["data"]["receipt"]
    assert settled["total_amount"] == "4050.00"
    assert settled["amount_paid"] == "4050.00"
    assert settled["balance"] == "0.00"
    assert settled["payment_status"] == "Paid"
    assert db.session.get(Customer, customer_id).outstanding_balance == Decimal("0.00")
    assert SalePayment.query.filter_by(sale_id=sale_id).count() == 2
    assert sum(
        (payment.amount for payment in SalePayment.query.filter_by(sale_id=sale_id).all()),
        Decimal("0.00"),
    ) == Decimal("4050.00")


def test_sale_uses_decimal_unit_prices_without_float_rounding(client):
    store_id = register_and_login(client, "sale-decimal-price-user")
    product = create_product(
        client,
        store_id,
        "Small Item",
        10,
        [{"name": "piece", "base_quantity": 1, "price": "19.99"}],
    )

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "POS",
            "items": [{"product_id": product["id"], "quantity": 3}],
        },
    )

    assert response.status_code == 201, response.get_json()
    receipt = response.json["data"]["receipt"]
    assert receipt["total_amount"] == "59.97"
    assert receipt["items"][0]["price_at_sale"] == "19.99"
    assert receipt["items"][0]["total"] == "59.97"
    assert db.session.get(Product, product["id"]).stock_quantity == 7
    assert db.session.query(Sale).one().total_amount == Decimal("59.97")


def test_sale_rejects_amount_paid_above_total_without_changing_records(client):
    store_id = register_and_login(client, "sale-overpayment-user")
    customer_id = create_customer(client, store_id)
    product = create_product(
        client,
        store_id,
        "Overpayment Item",
        5,
        [{"name": "piece", "base_quantity": 1, "price": 100}],
    )

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "amount_paid": "100.01",
            "client_transaction_id": "sale-overpayment-rejected-001",
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert db.session.get(Product, product["id"]).stock_quantity == 5
    assert db.session.get(Customer, customer_id).outstanding_balance == Decimal("0.00")
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert SalePayment.query.count() == 0
    assert InventoryMovement.query.count() == 0
