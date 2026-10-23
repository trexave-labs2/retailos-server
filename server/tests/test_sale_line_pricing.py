from server.app.extensions import db
from server.app.models.product import Product
from server.app.models.sale_item import SaleItem


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


def create_service(client, store_id, price=5000):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Consultation",
            "product_type": "Service",
            "price": price,
            "opening_stock": 0,
        },
    )

    assert response.status_code == 201
    return response.json["data"]["product"]["id"]


def test_service_sale_stores_checkout_unit_price(client):
    store_id = register_and_login(client, "service-price-user")
    product_id = create_service(client, store_id, price=5000)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 2,
                    "unit_price": 3500,
                }
            ],
        },
    )

    assert response.status_code == 201
    assert response.json["data"]["receipt"]["total_amount"] == "7000.00"
    assert response.json["data"]["receipt"]["items"][0]["price_at_sale"] == "3500.00"

    sale_item = SaleItem.query.one()
    assert sale_item.price_at_sale == 3500
    assert sale_item.total == 7000


def test_service_sale_uses_catalog_price_when_checkout_price_is_omitted(client):
    store_id = register_and_login(client, "service-default-price-user")
    product_id = create_service(client, store_id, price=5000)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 2,
                }
            ],
        },
    )

    assert response.status_code == 201
    assert response.json["data"]["receipt"]["total_amount"] == "10000.00"
    assert response.json["data"]["receipt"]["items"][0]["price_at_sale"] == "5000.00"


def test_physical_sale_cannot_override_unit_price(client):
    store_id = register_and_login(client, "physical-price-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "product_type": "Physical",
            "price": 2500,
            "opening_stock": 5,
        },
    )

    assert response.status_code == 201
    product_id = response.json["data"]["product"]["id"]

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1,
                    "unit_price": 1000,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert db.session.query(SaleItem).count() == 0


def test_service_sale_rejects_negative_checkout_unit_price(client):
    store_id = register_and_login(client, "negative-service-price-user")
    product_id = create_service(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1,
                    "unit_price": -100,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert db.session.query(SaleItem).count() == 0



def test_catalog_price_change_applies_to_new_sales_and_preserves_history(client):
    store_id = register_and_login(client, "catalog-price-change-user")
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Bread",
            "product_type": "Physical",
            "price": 500,
            "opening_stock": 10,
        },
    )
    assert response.status_code == 201
    product_id = response.json["data"]["product"]["id"]

    first_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert first_sale.status_code == 201
    assert first_sale.json["data"]["receipt"]["total_amount"] == "500.00"

    update = client.patch(
        "/product/update",
        json={"store_id": store_id, "id": product_id, "price": 600},
    )
    assert update.status_code == 200
    assert update.json["data"]["product"]["price"] == "600.00"

    product_info = client.get(
        f"/product/get?store_id={store_id}&id={product_id}"
    )
    assert product_info.status_code == 200
    assert product_info.json["data"]["product"]["price"] == "600.00"

    second_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert second_sale.status_code == 201
    assert second_sale.json["data"]["receipt"]["total_amount"] == "600.00"

    sale_items = SaleItem.query.order_by(SaleItem.id.asc()).all()
    assert [str(item.price_at_sale) for item in sale_items] == ["500.00", "600.00"]
    assert [str(item.total) for item in sale_items] == ["500.00", "600.00"]


def test_product_price_rejects_non_finite_values(client):
    store_id = register_and_login(client, "non-finite-price-user")
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Invalid price",
            "price": "NaN",
            "opening_stock": 1,
        },
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
