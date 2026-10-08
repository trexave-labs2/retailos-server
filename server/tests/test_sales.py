# server/tests/test_sales.py
from datetime import timedelta, timezone

import pytest

from server.app.extensions import db
from server.app.models.alert import Alert
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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


# Check that a cart line quantity cannot exceed available stock.
def test_sale_rejects_quantity_above_available_stock(client):
    store_id = register_and_login(client, username="sales-stock-limit-user")
    product_id = create_product(
        client,
        store_id,
        name="Stock Limited Product",
        stock_quantity=3
    )

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 4
                }
            ]
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"
    assert db.session.get(Product, product_id).stock_quantity == 3
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert InventoryMovement.query.count() == 0


# Check that invalid quantities are rejected.
def test_sale_rejects_invalid_quantity(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
            "payment_method": "Cash",
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
        payment_method="Cash",
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
            payment_method="Cash",
            created_at=future
        )


def test_sale_rejects_invalid_payment_method(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cheque",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1
                }
            ]
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("payment_method", ["Cash", "Transfer", "POS"])
def test_sale_accepts_valid_payment_methods(client, payment_method):
    store_id = register_and_login(
        client,
        username=f"payment{payment_method.lower()}user"
    )
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": payment_method,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1
                }
            ]
        }
    )

    assert response.status_code == 201
    assert response.json["data"]["receipt"]["payment_method"] == payment_method


def test_sales_list_merges_history_and_transaction_data(client):
    store_id = register_and_login(client, username="sales-list-merged-user")
    product_id = create_product(client, store_id, name="Merged List Product", stock_quantity=5)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Transfer",
            "items": [{"product_id": product_id, "quantity": 2}],
            "client_transaction_id": "merged-history-transaction-001",
        },
    )

    assert response.status_code == 201
    sale_id = response.json["data"]["receipt"]["sale_id"]

    response = client.get(f"/sales?store_id={store_id}")

    assert response.status_code == 200
    sales = response.json["data"]["sales"]
    assert len(sales) == 1
    assert sales[0]["id"] == sale_id
    assert sales[0]["client_transaction_id"] == "merged-history-transaction-001"
    assert sales[0]["customer_id"] is None
    assert sales[0]["total_amount"] == "5000.00"
    assert sales[0]["payment_method"] == "Transfer"
    assert sales[0]["created_at"]


def test_sales_can_filter_by_payment_method(client):
    store_id = register_and_login(client, username="sales-payment-filter-user")
    product_id = create_product(client, store_id, name="Filter Product", stock_quantity=10)

    for transaction_id, payment_method in (
        ("filter-cash", "Cash"),
        ("filter-transfer", "Transfer"),
        ("filter-pos", "POS"),
    ):
        response = client.post(
            "/sales",
            json={
                "store_id": store_id,
                "payment_method": payment_method,
                "items": [{"product_id": product_id, "quantity": 1}],
                "client_transaction_id": transaction_id,
            },
        )
        assert response.status_code == 201

    response = client.get(
        f"/sales?store_id={store_id}&payment_method=Transfer"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["payment_method"] == "Transfer"
    assert len(data["sales"]) == 1
    assert data["sales"][0]["payment_method"] == "Transfer"


def test_sales_can_group_by_payment_method(client):
    store_id = register_and_login(client, username="sales-payment-group-user")
    product_id = create_product(client, store_id, name="Group Product", stock_quantity=10)

    for transaction_id, payment_method, quantity in (
        ("group-cash-1", "Cash", 1),
        ("group-cash-2", "Cash", 2),
        ("group-pos-1", "POS", 1),
    ):
        response = client.post(
            "/sales",
            json={
                "store_id": store_id,
                "payment_method": payment_method,
                "items": [{"product_id": product_id, "quantity": quantity}],
                "client_transaction_id": transaction_id,
            },
        )
        assert response.status_code == 201

    response = client.get(
        f"/sales?store_id={store_id}&group_by=payment_method"
    )

    assert response.status_code == 200
    grouped = {
        row["payment_method"]: row
        for row in response.json["data"]["sales_by_payment_method"]
    }

    assert grouped["Cash"]["sales_count"] == 2
    assert grouped["Cash"]["total_sales"] == "7500.00"
    assert grouped["POS"]["sales_count"] == 1
    assert grouped["POS"]["total_sales"] == "2500.00"
    assert "Transfer" not in grouped


def test_sales_reject_invalid_payment_method_filter(client):
    store_id = register_and_login(client, username="sales-payment-filter-invalid-user")

    response = client.get(
        f"/sales?store_id={store_id}&payment_method=Cheque"
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


def test_service_sale_does_not_deduct_stock_or_create_inventory_movement(client):
    store_id = register_and_login(client, username="service-sale-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Phone Repair",
            "product_type": "Service",
            "price": 5000,
            "opening_stock": 0,
            "low_stock_threshold": 1,
        },
    )

    assert response.status_code == 201
    product_id = response.json["data"]["product"]["id"]
    assert response.json["data"]["product"]["product_type"] == "Service"
    assert Alert.query.count() == 0

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 3}],
            "client_transaction_id": "service-sale-001",
        },
    )

    assert response.status_code == 201
    assert response.json["data"]["receipt"]["total_amount"] == "15000.00"
    assert db.session.get(Product, product_id).stock_quantity == 0
    assert InventoryMovement.query.count() == 0
    assert Alert.query.count() == 0
    assert Sale.query.count() == 1
    assert SaleItem.query.count() == 1


def test_service_sale_can_exceed_zero_stock(client):
    store_id = register_and_login(client, username="service-stock-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Delivery Service",
            "product_type": "Service",
            "price": 1000,
            "opening_stock": 0,
        },
    )

    assert response.status_code == 201
    product_id = response.json["data"]["product"]["id"]

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Transfer",
            "items": [{"product_id": product_id, "quantity": 100}],
        },
    )

    assert response.status_code == 201
    assert db.session.get(Product, product_id).stock_quantity == 0


def test_product_rejects_invalid_product_type(client):
    store_id = register_and_login(client, username="invalid-product-type-user")

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Invalid Type",
            "product_type": "Bundle",
            "price": 1000,
            "opening_stock": 1,
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert "product_type" in response.json["error"]["fields"]



def test_sales_defaults_return_frequent_products_services_and_recent_customers(client):
    store_id = register_and_login(client, username="sales-defaults-user")

    frequent_product = create_product(
        client,
        store_id,
        name="Frequent Product",
        stock_quantity=20,
    )
    other_product = create_product(
        client,
        store_id,
        name="Other Product",
        stock_quantity=20,
    )

    service_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Phone Repair",
            "product_type": "Service",
            "price": 5000,
            "opening_stock": 0,
        },
    )
    assert service_response.status_code == 201
    service_id = service_response.json["data"]["product"]["id"]

    customer_response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "Recent Customer",
            "contact": "08012345678",
        },
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]

    for transaction_id, product_id, quantity in (
        ("defaults-frequent-1", frequent_product, 1),
        ("defaults-other", other_product, 1),
        ("defaults-frequent-2", frequent_product, 2),
    ):
        response = client.post(
            "/sales",
            json={
                "store_id": store_id,
                "customer_id": customer_id,
                "payment_method": "Cash",
                "items": [{"product_id": product_id, "quantity": quantity}],
                "client_transaction_id": transaction_id,
            },
        )
        assert response.status_code == 201

    service_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Transfer",
            "items": [{"product_id": service_id, "quantity": 1}],
            "client_transaction_id": "defaults-service",
        },
    )
    assert service_sale.status_code == 201

    response = client.get(f"/sales/defaults?store_id={store_id}")

    assert response.status_code == 200
    data = response.json["data"]

    assert data["frequent_products"][0]["id"] == frequent_product
    assert data["frequent_products"][0]["sale_count"] == 2
    assert data["frequent_products"][0]["quantity_sold"] == 3
    assert data["frequent_products"][0]["default_unit"]["name"] == "piece"
    assert data["frequent_products"][0]["default_unit"]["base_quantity"] == 1

    assert [item["id"] for item in data["quick_add_services"]] == [service_id]
    assert data["quick_add_services"][0]["product_type"] == "Service"
    assert data["quick_add_services"][0]["default_unit"]["name"] == "piece"

    assert data["recent_customers"][0]["id"] == customer_id
    assert data["recent_customers"][0]["name"] == "Recent Customer"
    assert data["recent_customers"][0]["last_purchase_at"]


def test_sales_defaults_are_store_scoped_and_require_authentication(client):
    response = client.get("/sales/defaults?store_id=1")
    assert response.status_code == 401

    first_store_id = register_and_login(client, username="defaults-first-store")
    second_store_id = register_and_login(client, username="defaults-second-store")

    response = client.get(f"/sales/defaults?store_id={first_store_id}")

    assert second_store_id != first_store_id
    assert response.status_code == 403


# Check that Quick Add returns the product from the most recently recorded sale item.
def test_quick_add_returns_latest_recorded_product(client):
    store_id = register_and_login(client, username="quick-add-user")
    first_product_id = create_product(
        client,
        store_id,
        name="First Product",
        stock_quantity=10,
    )
    second_product_id = create_product(
        client,
        store_id,
        name="Second Product",
        stock_quantity=10,
    )

    first_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": first_product_id, "quantity": 1}],
        },
    )
    assert first_sale.status_code == 201

    second_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Transfer",
            "items": [{"product_id": second_product_id, "quantity": 2}],
        },
    )
    assert second_sale.status_code == 201
    second_sale_id = second_sale.json["data"]["receipt"]["sale_id"]

    response = client.get(f"/sales/quick-add?store_id={store_id}")

    assert response.status_code == 200
    data = response.json["data"]
    assert data["product"]["id"] == second_product_id
    assert data["product"]["name"] == "Second Product"
    assert data["source_sale"]["sale_id"] == second_sale_id
    assert data["source_sale"]["quantity"] == 2
    assert data["source_sale"]["price_at_sale"] == "2500.00"


def test_quick_add_is_store_scoped(client):
    first_store_id = register_and_login(client, username="quick-add-first-store")
    product_id = create_product(
        client,
        first_store_id,
        name="Private Product",
    )

    sale = client.post(
        "/sales",
        json={
            "store_id": first_store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert sale.status_code == 201

    second_store_id = register_and_login(client, username="quick-add-second-store")

    response = client.get(f"/sales/quick-add?store_id={first_store_id}")

    assert second_store_id != first_store_id
    assert response.status_code == 403

    response = client.get(f"/sales/quick-add?store_id={second_store_id}")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "QUICK_ADD_NOT_FOUND"


def test_quick_add_requires_authentication(client):
    response = client.get("/sales/quick-add?store_id=1")

    assert response.status_code == 401


def test_sale_quantity_and_inventory_movement_use_product_base_unit(client):
    store_id = register_and_login(client, username="sale-base-unit-user")
    product_id = create_product(
        client,
        store_id,
        name="Rice",
        stock_quantity=10,
    )

    product = db.session.get(Product, product_id)
    product.base_unit = "gram"
    db.session.commit()

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 3}],
        },
    )

    assert response.status_code == 201
    receipt = response.json["data"]["receipt"]
    assert receipt["items"][0]["quantity"] == 3
    assert receipt["items"][0]["base_unit"] == "gram"

    movement = InventoryMovement.query.one()
    assert movement.quantity_change == -3
    assert movement.previous_quantity == 10
    assert movement.new_quantity == 7

    response = client.get(f"/product/movements?store_id={store_id}")
    assert response.status_code == 200
    assert response.json["data"]["movements"][0]["base_unit"] == "gram"
