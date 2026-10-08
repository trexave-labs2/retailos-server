# server/tests/test_product.py
import pytest

from server.app.extensions import db
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product


def register_and_login(client, username="productuser", **kwargs):
    username = kwargs.get("username", username)
    email = f"{username}@example.com"

    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "test123"
        }
    )

    assert response.status_code == 201

    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "test123"
        }
    )

    assert response.status_code == 200

    return response.json["data"]["user"]["store_id"]


def create_product(
    client,
    store_id,
    name="Rice 1kg",
    price=2500,
    stock_quantity=10,
    low_stock_threshold=2
):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": price,
            "opening_stock": stock_quantity,
            "low_stock_threshold": low_stock_threshold
        }
    )

    assert response.status_code == 201

    return response.json["data"]["product"]["id"]


# Check that a product can be created and retrieved.
def test_create_and_get_product(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.get(
        f"/product/get?store_id={store_id}&id={product_id}"
    )

    assert response.status_code == 200

    product = response.json["data"]["product"]

    assert product["id"] == product_id
    assert product["name"] == "Rice 1kg"
    assert product["price"] == "2500.00"
    assert product["stock_quantity"] == 10
    assert product["stock"]["quantity"] == 10
    assert product["stock"]["low_stock_threshold"] == 2


# Check that products can be listed.
def test_list_products(client):
    store_id = register_and_login(client)

    create_product(
        client,
        store_id,
        name="Rice"
    )
    create_product(
        client,
        store_id,
        name="Bread",
        price=1000,
        stock_quantity=5
    )

    response = client.get(
        f"/product/list?store_id={store_id}"
    )

    assert response.status_code == 200

    products = response.json["data"]["products"]

    assert len(products) == 2
    assert products[0]["name"] == "Bread"
    assert products[0]["stock"]["quantity"] == 5
    assert products[1]["name"] == "Rice"
    assert products[1]["stock"]["quantity"] == 10


# Check that a product can be updated.
def test_update_product(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.patch(
        "/product/update",
        json={
            "store_id": store_id,
            "id": product_id,
            "name": "Rice 2kg",
            "price": 4500,
            "stock_quantity": 20,
            "low_stock_threshold": 5
        }
    )

    assert response.status_code == 200

    product = response.json["data"]["product"]

    assert product["name"] == "Rice 2kg"
    assert product["price"] == "4500.00"
    assert product["stock_quantity"] == 20
    assert product["low_stock_threshold"] == 5
    assert product["stock"]["quantity"] == 20
    assert product["stock"]["low_stock_threshold"] == 5


# Check that invalid product data is rejected.
def test_product_validation_rejects_invalid_data(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "",
            "price": -100,
            "opening_stock": -5,
            "low_stock_threshold": 2
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


# Check that stock changes create movement history.
def test_adjust_stock_creates_inventory_movement(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        "/product/adjust",
        json={
            "store_id": store_id,
            "id": product_id,
            "quantity_change": 5,
            "reason": "Restocking"
        }
    )

    assert response.status_code == 200
    assert response.json["data"]["product"]["stock_quantity"] == 15

    response = client.get(
        f"/product/movements?store_id={store_id}&product_id={product_id}"
    )

    assert response.status_code == 200

    movements = response.json["data"]["movements"]

    assert len(movements) == 1
    assert movements[0]["quantity_change"] == 5
    assert movements[0]["previous_quantity"] == 10
    assert movements[0]["new_quantity"] == 15
    assert movements[0]["reason"] == "Restocking"


# Check that stock cannot become negative.
def test_adjust_stock_rejects_negative_result(client):
    store_id = register_and_login(client)
    product_id = create_product(
        client,
        store_id,
        stock_quantity=5
    )

    response = client.post(
        "/product/adjust",
        json={
            "store_id": store_id,
            "id": product_id,
            "quantity_change": -6
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"

    product = db.session.get(Product, product_id)

    assert product.stock_quantity == 5
    assert InventoryMovement.query.count() == 0


# Check that another store cannot change this store's product.
def test_product_write_actions_are_store_scoped(client):
    first_store_id = register_and_login(
        client,
        username="firstproductuser"
    )
    product_id = create_product(
        client,
        first_store_id,
        name="Private Product"
    )

    second_store_id = register_and_login(
        client,
        username="secondproductuser"
    )

    response = client.patch(
        "/product/update",
        json={
            "store_id": first_store_id,
            "id": product_id,
            "name": "Changed Product"
        }
    )

    assert response.status_code == 403

    response = client.post(
        "/product/adjust",
        json={
            "store_id": first_store_id,
            "id": product_id,
            "quantity_change": 5
        }
    )

    assert response.status_code == 403

    response = client.delete(
        "/product/delete",
        json={
            "store_id": first_store_id,
            "id": product_id
        }
    )

    assert response.status_code == 403

    assert second_store_id != first_store_id


# Check that a product movement query cannot cross stores.
def test_inventory_movements_are_store_scoped(client):
    first_store_id = register_and_login(
        client,
        username="firstmovementuser"
    )
    product_id = create_product(client, first_store_id)

    second_store_id = register_and_login(
        client,
        username="secondmovementuser"
    )

    response = client.get(
        f"/product/movements?store_id={first_store_id}&product_id={product_id}"
    )

    assert second_store_id != first_store_id
    assert response.status_code == 403


# Check that products require authentication.
def test_products_require_authentication(client):
    response = client.get("/product/list?store_id=1")

    assert response.status_code == 401

# Check that a product can be created.
def test_create_product(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 2500,
            "opening_stock": 10,
            "low_stock_threshold": 3
        }
    )

    assert response.status_code == 201
    assert response.json["data"]["product"]["name"] == "Rice"
    assert response.json["data"]["product"]["stock_quantity"] == 10


def test_create_product_rejects_ambiguous_stock_field(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 2500,
            "stock_quantity": 10
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "opening_stock" in response.json["error"]["fields"]


# Check that negative price is rejected.
def test_create_product_rejects_negative_price(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": -1,
            "opening_stock": 10
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


# Check that an omitted low stock threshold disables the low stock rule.
def test_create_product_allows_no_low_stock_threshold(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 2500,
            "opening_stock": 10
        }
    )

    assert response.status_code == 201
    assert response.json["data"]["product"]["low_stock_threshold"] is None


# Check that products are limited to the user's store.
def test_list_products_requires_store_access(client):
    first_store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": first_store_id,
            "name": "Rice",
            "price": 2500,
            "opening_stock": 10
        }
    )

    assert response.status_code == 201

    response = client.get(
        f"/product/list?store_id={first_store_id}"
    )

    assert response.status_code == 200
    assert len(response.json["data"]["products"]) == 1


def test_product_stores_stock_in_explicit_base_unit(client):
    store_id = register_and_login(client, username="base-unit-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "price": 2500,
            "opening_stock": 50,
            "base_unit": "gram",
            "low_stock_threshold": 10,
        },
    )

    assert response.status_code == 201
    product = response.json["data"]["product"]

    assert product["base_unit"] == "gram"
    assert product["stock_quantity"] == 50
    assert product["stock"]["quantity"] == 50
    assert product["stock"]["base_unit"] == "gram"
    assert product["stock"]["low_stock_threshold"] == 10

    response = client.patch(
        "/product/update",
        json={
            "id": product["id"],
            "store_id": store_id,
            "base_unit": "kilogram",
        },
    )

    assert response.status_code == 200
    assert response.json["data"]["product"]["base_unit"] == "kilogram"


def test_product_defaults_to_piece_base_unit(client):
    store_id = register_and_login(client, username="default-base-unit-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Bottle",
            "price": 500,
            "opening_stock": 12,
        },
    )

    assert response.status_code == 201
    assert response.json["data"]["product"]["base_unit"] == "piece"


@pytest.mark.parametrize(
    "base_unit",
    ["", " "],
)
def test_product_rejects_empty_base_unit(client, base_unit):
    store_id = register_and_login(client, username=f"empty-base-{len(base_unit)}")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Invalid Unit",
            "price": 1000,
            "opening_stock": 1,
            "base_unit": base_unit,
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "base_unit" in response.json["error"]["fields"]


def test_product_rejects_base_unit_longer_than_20_characters(client):
    store_id = register_and_login(client, username="long-base-unit-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Invalid Unit",
            "price": 1000,
            "opening_stock": 1,
            "base_unit": "x" * 21,
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "base_unit" in response.json["error"]["fields"]
