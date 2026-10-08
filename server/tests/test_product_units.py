from server.app.extensions import db
from server.app.models.product_unit import ProductUnit
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


def test_product_creates_default_base_unit(client):
    store_id = register_and_login(client, "unit-default-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Water",
            "price": 300,
            "opening_stock": 24,
        },
    )

    assert response.status_code == 201
    product = response.json["data"]["product"]

    assert product["units"] == [
        {
            "id": product["units"][0]["id"],
            "name": "piece",
            "base_quantity": 1,
            "price": "300.00",
        }
    ]


def test_product_accepts_multiple_units_with_independent_prices(client):
    store_id = register_and_login(client, "unit-pricing-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Soft Drink",
            "price": 300,
            "opening_stock": 24,
            "base_unit": "piece",
            "units": [
                {"name": "piece", "base_quantity": 1, "price": 300},
                {"name": "pack", "base_quantity": 6, "price": 1650},
                {"name": "carton", "base_quantity": 24, "price": 6200},
            ],
        },
    )

    assert response.status_code == 201
    units = response.json["data"]["product"]["units"]

    assert [(unit["name"], unit["base_quantity"], unit["price"]) for unit in units] == [
        ("piece", 1, "300.00"),
        ("pack", 6, "1650.00"),
        ("carton", 24, "6200.00"),
    ]

    assert ProductUnit.query.count() == 3


def test_product_rejects_invalid_unit_definition(client):
    store_id = register_and_login(client, "unit-validation-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 1000,
            "opening_stock": 10,
            "units": [
                {"name": "bag", "base_quantity": 0, "price": 5000},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "units" in response.json["error"]["fields"]


def test_product_units_can_be_added_or_repriced_on_update(client):
    store_id = register_and_login(client, "unit-update-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Egg",
            "price": 200,
            "opening_stock": 30,
        },
    )
    assert response.status_code == 201
    product_id = response.json["data"]["product"]["id"]

    response = client.patch(
        "/product/update",
        json={
            "store_id": store_id,
            "id": product_id,
            "units": [
                {"name": "piece", "base_quantity": 1, "price": 250},
                {"name": "crate", "base_quantity": 30, "price": 6500},
            ],
        },
    )

    assert response.status_code == 200
    units = response.json["data"]["product"]["units"]

    assert {(u["name"], u["base_quantity"], u["price"]) for u in units} == {
        ("piece", 1, "250.00"),
        ("crate", 30, "6500.00"),
    }


def test_sale_uses_selected_unit_price_and_base_quantity(client):
    store_id = register_and_login(client, "unit-sale-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Egg",
            "price": 200,
            "opening_stock": 60,
            "units": [
                {"name": "piece", "base_quantity": 1, "price": 200},
                {"name": "crate", "base_quantity": 30, "price": 5500},
            ],
        },
    )
    assert response.status_code == 201

    product = response.json["data"]["product"]
    crate = next(unit for unit in product["units"] if unit["name"] == "crate")

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product["id"],
                    "unit_id": crate["id"],
                    "quantity": 2,
                }
            ],
        },
    )

    assert response.status_code == 201
    receipt_item = response.json["data"]["receipt"]["items"][0]

    assert receipt_item["unit_id"] == crate["id"]
    assert receipt_item["unit_name"] == "crate"
    assert receipt_item["unit_base_quantity"] == 30
    assert receipt_item["base_quantity_deducted"] == 60
    assert receipt_item["price_at_sale"] == "5500.00"
    assert receipt_item["total"] == "11000.00"

    saved_product = db.session.get(Product, product["id"])
    assert saved_product.stock_quantity == 0

    sale_item = SaleItem.query.one()
    assert sale_item.unit_id == crate["id"]
    assert sale_item.unit_base_quantity == 30
    assert sale_item.base_quantity_deducted == 60


def test_sale_blocks_selected_unit_when_base_stock_is_insufficient(client):
    store_id = register_and_login(client, "unit-stock-limit-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 1000,
            "opening_stock": 100,
            "units": [
                {"name": "piece", "base_quantity": 1, "price": 1000},
                {"name": "carton", "base_quantity": 40, "price": 38000},
            ],
        },
    )
    assert response.status_code == 201

    product = response.json["data"]["product"]
    carton = next(unit for unit in product["units"] if unit["name"] == "carton")

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product["id"],
                    "unit_id": carton["id"],
                    "quantity": 3,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"

    saved_product = db.session.get(Product, product["id"])
    assert saved_product.stock_quantity == 100
    assert SaleItem.query.count() == 0
