from server.app.extensions import db
from server.app.models.customer import Customer
from server.app.models.product import Product
from server.app.models.sale import Sale


def register_and_login(client, username):
    email = f"{username}@example.com"

    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "test123",
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "test123",
        },
    )
    assert response.status_code == 200

    return response.json["data"]["user"]["store_id"]


def create_product(client, store_id, name="Rice 1kg"):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": 2500,
            "opening_stock": 10,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["product"]["id"]


def create_customer(client, store_id, name):
    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": name,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["customer"]["id"]


def sale_payload(store_id, product_id, **overrides):
    payload = {
        "store_id": store_id,
        "payment_method": "Cash",
        "items": [{"product_id": product_id, "quantity": 2}],
    }
    payload.update(overrides)
    return payload


def test_sale_defaults_amount_paid_to_total_and_has_zero_balance(client):
    store_id = register_and_login(client, "sale-balance-full-payment")
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json=sale_payload(store_id, product_id),
    )

    assert response.status_code == 201
    receipt = response.json["data"]["receipt"]
    assert receipt["total_amount"] == "5000.00"
    assert receipt["amount_paid"] == "5000.00"
    assert receipt["balance"] == "0.00"

    sale = db.session.get(Sale, receipt["sale_id"])
    assert sale.amount_paid == 5000
    assert sale.balance == 0


def test_sale_rejects_amount_paid_above_total(client):
    store_id = register_and_login(client, "sale-balance-overpaid")
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            amount_paid=5001,
        ),
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert Sale.query.count() == 0


def test_sale_requires_customer_when_balance_is_positive(client):
    store_id = register_and_login(client, "sale-balance-customer-required")
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            amount_paid=2000,
        ),
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "CUSTOMER_REQUIRED_FOR_BALANCE"
    assert Sale.query.count() == 0
    assert db.session.get(Product, product_id).stock_quantity == 10


def test_customer_outstanding_balance_accumulates_across_sales(client):
    store_id = register_and_login(client, "customer-running-balance")
    product_id = create_product(client, store_id)
    customer_id = create_customer(client, store_id, "Jane Debtor")

    first = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            customer_id=customer_id,
            amount_paid=2000,
        ),
    )
    assert first.status_code == 201
    assert first.json["data"]["receipt"]["balance"] == "3000.00"

    second = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            customer_id=customer_id,
            amount_paid=1000,
        ),
    )
    assert second.status_code == 201
    assert second.json["data"]["receipt"]["balance"] == "4000.00"

    customer = db.session.get(Customer, customer_id)
    assert customer.outstanding_balance == 7000

    response = client.get(f"/customers?store_id={store_id}")
    assert response.status_code == 200
    assert response.json["data"]["customers"][0]["outstanding_balance"] == "7000.00"


def test_who_owes_me_lists_only_customers_with_positive_balance(client):
    store_id = register_and_login(client, "who-owes-me")
    product_id = create_product(client, store_id, name="Who Owes Product")
    debtor_id = create_customer(client, store_id, "Debtor")
    paid_id = create_customer(client, store_id, "Paid Customer")

    debtor_sale = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            customer_id=debtor_id,
            amount_paid=1000,
        ),
    )
    assert debtor_sale.status_code == 201

    paid_sale = client.post(
        "/sales",
        json=sale_payload(
            store_id,
            product_id,
            customer_id=paid_id,
            amount_paid=5000,
        ),
    )
    assert paid_sale.status_code == 201

    response = client.get(f"/customers/owes?store_id={store_id}")

    assert response.status_code == 200
    data = response.json["data"]
    assert data["total_outstanding"] == "4000.00"
    assert len(data["customers"]) == 1
    assert data["customers"][0]["id"] == debtor_id
    assert data["customers"][0]["name"] == "Debtor"
    assert data["customers"][0]["outstanding_balance"] == "4000.00"


def test_who_owes_me_is_store_scoped(client):
    first_store_id = register_and_login(client, "who-owes-first-store")
    first_product_id = create_product(client, first_store_id)
    first_customer_id = create_customer(client, first_store_id, "Private Debtor")

    sale = client.post(
        "/sales",
        json=sale_payload(
            first_store_id,
            first_product_id,
            customer_id=first_customer_id,
            amount_paid=1000,
        ),
    )
    assert sale.status_code == 201

    second_store_id = register_and_login(client, "who-owes-second-store")

    response = client.get(f"/customers/owes?store_id={first_store_id}")
    assert response.status_code == 403

    response = client.get(f"/customers/owes?store_id={second_store_id}")
    assert response.status_code == 200
    assert response.json["data"]["customers"] == []
    assert response.json["data"]["total_outstanding"] == "0.00"
