# server/tests/test_sales.py
from datetime import timedelta, timezone

import pytest

from server.app.extensions import db
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.utils.time import now_utc


def register_and_login(client, username="salesuser"):
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


def create_product(client, store_id, name="Rice 1kg", stock_quantity=10):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": 2500,
            "opening_stock": stock_quantity,
            "low_stock_threshold": 2
        }
    )

    assert response.status_code == 201

    return response.json["data"]["product"]["id"]


# Check that a sale reduces stock correctly.
def test_sale_updates_inventory_atomically(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 2
                }
            ],
            "client_transaction_id": "test-sale-001"
        }
    )

    assert response.status_code == 201
    assert response.json["data"]["receipt"]["total_amount"] == "5000.00"

    product = db.session.get(Product, product_id)

    assert product.stock_quantity == 8

    movement = InventoryMovement.query.filter_by(
        product_id=product_id
    ).first()

    assert movement.quantity_change == -2
    assert movement.previous_quantity == 10
    assert movement.new_quantity == 8


# Check that all products are updated in one transaction.
def test_sale_rolls_back_everything_when_one_item_fails(client):
    store_id = register_and_login(client)

    first_product_id = create_product(
        client,
        store_id,
        name="Rice",
        stock_quantity=10
    )
    second_product_id = create_product(
        client,
        store_id,
        name="Bread",
        stock_quantity=1
    )

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": first_product_id,
                    "quantity": 2
                },
                {
                    "product_id": second_product_id,
                    "quantity": 5
                }
            ],
            "client_transaction_id": "rollback-sale"
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"

    first_product = db.session.get(Product, first_product_id)
    second_product = db.session.get(Product, second_product_id)

    assert first_product.stock_quantity == 10
    assert second_product.stock_quantity == 1
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert InventoryMovement.query.count() == 0


# Check that the same client transaction is not processed twice.
def test_duplicate_client_transaction_is_not_processed_twice(client):
    store_id = register_and_login(client)
    product_id = create_product(
        client,
        store_id,
        name="Bread",
        stock_quantity=5
    )

    first = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1
                }
            ],
            "client_transaction_id": "same-sale"
        }
    )

    second = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1
                }
            ],
            "client_transaction_id": "same-sale"
        }
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json["data"]["receipt"]["sale_id"] == 1
    assert db.session.get(Product, product_id).stock_quantity == 4


# Check that receipt data can be generated after a sale.
def test_receipt_is_generated_for_a_sale(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 2
                }
            ]
        }
    )

    assert sale.status_code == 201

    sale_id = sale.json["data"]["receipt"]["sale_id"]

    response = client.post(
        "/sales/receipt",
        json={
            "store_id": store_id,
            "id": sale_id
        }
    )

    assert response.status_code == 200

    receipt = response.json["data"]["receipt"]

    assert receipt["sale_id"] == sale_id
    assert receipt["total_amount"] == "5000.00"
    assert "RetailOS Receipt" in receipt["printable_text"]
    assert "Total: 5000.00" in receipt["printable_text"]


# Check that invalid quantities are rejected.
def test_sale_rejects_invalid_quantity(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 0
                }
            ]
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


# Check that empty sales are rejected.
def test_sale_requires_items(client):
    store_id = register_and_login(client)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": []
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


# Check that another store cannot create a sale against this store.
def test_sale_is_store_scoped(client):
    first_store_id = register_and_login(
        client,
        username="firstsalesuser"
    )
    product_id = create_product(client, first_store_id)

    second_store_id = register_and_login(
        client,
        username="secondsalesuser"
    )

    response = client.post(
        "/sales",
        json={
            "store_id": first_store_id,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1
                }
            ]
        }
    )

    assert second_store_id != first_store_id
    assert response.status_code == 403


# Check that a missing product is rejected.
def test_sale_rejects_missing_product(client):
    store_id = register_and_login(client)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "items": [
                {
                    "product_id": 99999,
                    "quantity": 1
                }
            ]
        }
    )

    assert response.status_code == 404
    assert response.json["error"]["code"] == "PRODUCT_NOT_FOUND"


# Check that sales require authentication.
def test_sales_require_authentication(client):
    response = client.post(
        "/sales",
        json={
            "store_id": 1,
            "items": [
                {
                    "product_id": 1,
                    "quantity": 1
                }
            ]
        }
    )

    assert response.status_code == 401


def test_sale_timestamps_are_normalized_to_utc_and_not_future(client):
    store_id = register_and_login(client)

    local_timestamp = now_utc().astimezone(timezone(timedelta(hours=1)))

    sale = Sale(
        store_id=store_id,
        total_amount=1000,
        created_at=local_timestamp
    )

    assert sale.created_at.tzinfo == timezone.utc
    assert sale.created_at == local_timestamp.astimezone(timezone.utc)
    assert Sale.created_at.property.columns[0].type.timezone is True

    future = now_utc() + timedelta(minutes=5)

    with pytest.raises(ValueError, match="cannot be in the future"):
        Sale(
            store_id=store_id,
            total_amount=1000,
            created_at=future
        )
