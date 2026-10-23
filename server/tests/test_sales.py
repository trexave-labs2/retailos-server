# server/tests/test_sales.py
from datetime import timedelta, timezone
from decimal import Decimal

import pytest

from server.app.extensions import db
from server.app.models.alert import Alert
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_payment import SalePayment
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



def test_sale_updates_customer_balance_in_same_transaction(client):
    store_id = register_and_login(client, username="sale-customer-balance-user")
    product_id = create_product(client, store_id, name="Credit Product", stock_quantity=10)

    customer_response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "Credit Customer",
            "contact": "08022223333",
        },
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "amount_paid": "1000.00",
            "items": [{"product_id": product_id, "quantity": 2}],
            "client_transaction_id": "customer-balance-atomic-001",
        },
    )

    assert response.status_code == 201
    db.session.refresh(db.session.get(Product, product_id))
    from server.app.models.customer import Customer

    customer = db.session.get(Customer, customer_id)
    sale = Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id="customer-balance-atomic-001",
    ).one()

    assert sale.total_amount == 5000
    assert sale.amount_paid == 1000
    assert sale.balance == 4000
    assert customer.outstanding_balance == 4000
    assert db.session.get(Product, product_id).stock_quantity == 8
    assert InventoryMovement.query.filter_by(product_id=product_id).count() == 1


def test_sale_rolls_back_all_records_if_low_stock_alert_creation_fails(client, monkeypatch):
    from server.app.services.alert_service import AlertService

    store_id = register_and_login(client, username="sale-alert-rollback-user")
    product_id = create_product(
        client,
        store_id,
        name="Alert Rollback Product",
        stock_quantity=3,
    )

    def fail_alert_creation(self, store_id, product_ids=None):
        raise RuntimeError("simulated alert persistence failure")

    monkeypatch.setattr(AlertService, "create_low_stock_alerts", fail_alert_creation)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 2}],
            "client_transaction_id": "alert-rollback-001",
        },
    )

    # Flask's test client passes unhandled exceptions through the app's
    # exception handler and returns HTTP 500 instead of re-raising by default.
    assert response.status_code == 500

    db.session.expire_all()
    assert db.session.get(Product, product_id).stock_quantity == 3
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert InventoryMovement.query.count() == 0


@pytest.mark.parametrize("product_id", [None, "not-an-id", True, 1.5])
def test_sale_rejects_malformed_product_ids(client, product_id):
    username = f"bad-product-id-{str(product_id).replace(' ', '-')}"
    store_id = register_and_login(client, username=username)

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )

    assert response.status_code == 400
    assert response.json["success"] is False
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(response.json["error"]["message"], str)
    assert isinstance(response.json["error"]["fields"], dict)
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0


def test_sale_malformed_json_returns_consistent_error_envelope(client):
    register_and_login(client, username="malformed-sale-json-user")

    response = client.post(
        "/sales",
        data='{"store_id":',
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json == {
        "success": False,
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Invalid input data",
            "fields": {},
        },
    }

def test_receipt_malformed_json_returns_400(client):
    register_and_login(client)
    response = client.post(
        "/sales/receipt",
        data='{"store_id":',
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json["success"] is False
    assert response.json["error"]["code"] in {"VALIDATION_ERROR", "BAD_REQUEST"}


def test_additional_payment_updates_sale_and_customer_balance(client):
    store_id = register_and_login(client, username="additional-payment-user")
    customer_response = client.post(
        "/customers",
        json={"store_id": store_id, "name": "Credit Customer", "contact": "08000000000"},
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]
    product_id = create_product(client, store_id, name="Credit Rice", stock_quantity=5)

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "amount_paid": 1000,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 2}],
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]
    assert sale_response.json["data"]["receipt"]["balance"] == "4000.00"

    payment_response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": 1500,
            "payment_method": "Transfer",
            "client_transaction_id": "additional-payment-update-balance-001",
        },
    )

    assert payment_response.status_code == 200
    receipt = payment_response.json["data"]["receipt"]
    assert receipt["amount_paid"] == "2500.00"
    assert receipt["balance"] == "2500.00"
    assert receipt["payment_method"] == "Cash"
    payment = SalePayment.query.filter_by(
        sale_id=sale_id,
        amount=Decimal("1500.00"),
        payment_method="Transfer",
    ).one()
    assert payment.amount == Decimal("1500.00")
    assert payment.payment_method == "Transfer"
    customer = db.session.get(__import__("server.app.models.customer", fromlist=["Customer"]).Customer, customer_id)
    assert customer.outstanding_balance == Decimal("2500.00")


def test_additional_payment_rejects_amount_above_balance(client):
    store_id = register_and_login(client, username="payment-over-balance-user")
    customer_response = client.post(
        "/customers",
        json={"store_id": store_id, "name": "Credit Customer"},
    )
    customer_id = customer_response.json["data"]["customer"]["id"]
    product_id = create_product(client, store_id, name="Credit Bread", stock_quantity=5)
    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "amount_paid": 1000,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": 2000,
            "payment_method": "POS",
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "PAYMENT_EXCEEDS_BALANCE"
    sale = db.session.get(Sale, sale_id)
    assert sale.amount_paid == Decimal("1000.00")
    assert sale.balance == Decimal("1500.00")


def test_additional_payment_cannot_access_another_store_sale(client):
    first_store = register_and_login(client, username="payment-owner")
    customer_response = client.post(
        "/customers",
        json={"store_id": first_store, "name": "Credit Customer"},
    )
    customer_id = customer_response.json["data"]["customer"]["id"]
    product_id = create_product(client, first_store, name="Credit Product", stock_quantity=5)
    sale_response = client.post(
        "/sales",
        json={
            "store_id": first_store,
            "customer_id": customer_id,
            "amount_paid": 0,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    second_store = register_and_login(client, username="payment-outsider")
    response = client.post(
        "/sales/payments",
        json={
            "store_id": first_store,
            "sale_id": sale_id,
            "amount": 1,
            "payment_method": "Cash",
        },
    )

    assert second_store != first_store
    assert response.status_code == 403



# Partial-payment receipts must show the persisted paid amount and remaining balance.
def test_partial_payment_receipt_shows_outstanding_balance(client):
    store_id = register_and_login(client, username="partial-receipt-user")
    product_id = create_product(
        client,
        store_id,
        name="Twenty Thousand Sale Item",
        stock_quantity=10,
    )
    customer_response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "Partial Receipt Customer",
            "contact": "08012345678",
        },
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Transfer",
            "amount_paid": 12000,
            "items": [{"product_id": product_id, "quantity": 8}],
            "client_transaction_id": "partial-receipt-payment",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    response = client.post(
        "/sales/receipt",
        json={"store_id": store_id, "id": sale_id},
    )
    assert response.status_code == 200
    receipt = response.json["data"]["receipt"]

    assert receipt["total_amount"] == "20000.00"
    assert receipt["amount_paid"] == "12000.00"
    assert receipt["balance"] == "8000.00"
    assert receipt["payment_status"] == "Partially paid"
    assert receipt["payment_method"] == "Transfer"
    assert receipt["customer_name"] == "Partial Receipt Customer"
    assert "Total: 20000.00" in receipt["printable_text"]
    assert "Amount paid: 12000.00" in receipt["printable_text"]
    assert "Outstanding balance: 8000.00" in receipt["printable_text"]
    assert "Payment status: Partially paid" in receipt["printable_text"]


def test_payment_history_returns_initial_and_additional_payments(client):
    store_id = register_and_login(client, username="payment-history-user")
    customer_response = client.post(
        "/customers",
        json={"store_id": store_id, "name": "Payment History Customer"},
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]
    product_id = create_product(
        client,
        store_id,
        name="Payment History Rice",
        stock_quantity=5,
    )

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "amount_paid": 1000,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 2}],
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    payment_response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": 1500,
            "payment_method": "Transfer",
            "client_transaction_id": "payment-history-additional-001",
        },
    )
    assert payment_response.status_code == 200

    response = client.get(f"/sales/payments?store_id={store_id}&sale_id={sale_id}")
    assert response.status_code == 200
    data = response.json["data"]
    assert data["sale_id"] == sale_id
    assert data["total_amount"] == "5000.00"
    assert data["amount_paid"] == "2500.00"
    assert data["balance"] == "2500.00"
    assert [payment["amount"] for payment in data["payments"]] == ["1000.00", "1500.00"]
    assert [payment["payment_method"] for payment in data["payments"]] == ["Cash", "Transfer"]
    assert all(payment["created_at"] for payment in data["payments"])

    history_response = client.get(
        f"/customers/history?id={customer_id}&store_id={store_id}"
    )
    assert history_response.status_code == 200
    purchase = history_response.json["data"]["customer"]["purchase_history"][0]
    assert purchase["amount_paid"] == "2500.00"
    assert purchase["balance"] == "2500.00"
    assert len(purchase["payments"]) == 2


def test_payment_history_is_store_scoped(client):
    owner_store = register_and_login(client, username="payment-history-owner")
    product_id = create_product(client, owner_store, name="Private Payment Product")
    sale_response = client.post(
        "/sales",
        json={
            "store_id": owner_store,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    other_store = register_and_login(client, username="payment-history-outsider")
    response = client.get(
        f"/sales/payments?store_id={owner_store}&sale_id={sale_id}"
    )
    assert other_store != owner_store
    assert response.status_code == 403


def test_payment_history_requires_sale_id_and_store_id(client):
    store_id = register_and_login(client, username="payment-history-validation")
    response = client.get(f"/sales/payments?store_id={store_id}")
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"



def _create_sale_for_additional_payment(client, username):
    store_id = register_and_login(client, username=username)
    customer_response = client.post(
        "/customers",
        json={"store_id": store_id, "name": f"{username} Customer"},
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]
    product_id = create_product(
        client,
        store_id,
        name=f"{username} Product",
        stock_quantity=10,
    )
    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "amount_paid": 0,
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": f"{username}-sale",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]
    return store_id, customer_id, sale_id


def test_additional_payment_retry_is_idempotent(client):
    store_id, customer_id, sale_id = _create_sale_for_additional_payment(
        client, "payment-idempotency-user"
    )
    payload = {
        "store_id": store_id,
        "sale_id": sale_id,
        "amount": "1000.00",
        "payment_method": "Transfer",
        "client_transaction_id": "payment-idempotency-001",
    }

    first = client.post("/sales/payments", json=payload)
    retry = client.post("/sales/payments", json=payload)

    assert first.status_code == 200
    assert first.json["message"] == "SALE_PAYMENT_RECORDED"
    assert retry.status_code == 200
    assert retry.json["message"] == "SALE_PAYMENT_ALREADY_PROCESSED"

    payment = SalePayment.query.filter_by(
        client_transaction_id="payment-idempotency-001"
    ).all()
    assert len(payment) == 1
    assert payment[0].amount == Decimal("1000.00")

    sale = Sale.query.filter_by(id=sale_id, store_id=store_id).one()
    assert sale.amount_paid == Decimal("1000.00")
    assert sale.balance == Decimal("1500.00")

    from server.app.models.customer import Customer
    customer = db.session.get(Customer, customer_id)
    assert customer.outstanding_balance == Decimal("1500.00")


def test_additional_payment_rejects_reused_key_with_different_payload(client):
    store_id, _, sale_id = _create_sale_for_additional_payment(
        client, "payment-idempotency-mismatch"
    )
    first_payload = {
        "store_id": store_id,
        "sale_id": sale_id,
        "amount": "500.00",
        "payment_method": "Cash",
        "client_transaction_id": "payment-idempotency-reused",
    }
    first = client.post("/sales/payments", json=first_payload)
    second = client.post(
        "/sales/payments",
        json={**first_payload, "amount": "600.00"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert SalePayment.query.filter_by(
        client_transaction_id="payment-idempotency-reused"
    ).count() == 1
    sale = db.session.get(Sale, sale_id)
    assert sale.amount_paid == Decimal("500.00")
    assert sale.balance == Decimal("2000.00")


@pytest.mark.parametrize(
    "amount",
    [0, -1, "NaN", "Infinity", "1.001", "10000000000.00"],
)
def test_additional_payment_rejects_invalid_amounts(client, amount):
    store_id, _, sale_id = _create_sale_for_additional_payment(
        client, f"invalid-payment-{str(amount).replace('.', '-')}"
    )
    response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": amount,
            "payment_method": "Cash",
            "client_transaction_id": f"invalid-amount-{str(amount)}",
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert SalePayment.query.filter_by(sale_id=sale_id).count() == 0
    sale = db.session.get(Sale, sale_id)
    assert sale.amount_paid == Decimal("0.00")
    assert sale.balance == Decimal("2500.00")


def test_additional_payment_rejects_amount_above_balance(client):
    store_id, _, sale_id = _create_sale_for_additional_payment(
        client, "payment-over-balance"
    )
    response = client.post(
        "/sales/payments",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "2500.01",
            "payment_method": "Cash",
            "client_transaction_id": "payment-over-balance-key",
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "PAYMENT_EXCEEDS_BALANCE"
    assert SalePayment.query.filter_by(sale_id=sale_id).count() == 0


def test_additional_payment_rejects_invalid_method_and_missing_key(client):
    store_id, _, sale_id = _create_sale_for_additional_payment(
        client, "payment-invalid-method"
    )
    base_payload = {
        "store_id": store_id,
        "sale_id": sale_id,
        "amount": "100.00",
        "payment_method": "Cheque",
        "client_transaction_id": "payment-invalid-method-key",
    }
    invalid_method = client.post("/sales/payments", json=base_payload)
    missing_key = client.post(
        "/sales/payments",
        json={**base_payload, "payment_method": "Cash", "client_transaction_id": ""},
    )

    assert invalid_method.status_code == 400
    assert invalid_method.json["error"]["code"] == "VALIDATION_ERROR"
    assert missing_key.status_code == 400
    assert missing_key.json["error"]["code"] == "VALIDATION_ERROR"
    assert SalePayment.query.filter_by(sale_id=sale_id).count() == 0



def test_sales_combined_date_category_and_payment_filters_include_export_data(client):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    store_id = register_and_login(client, username="sales-combined-filter-user")
    category_response = client.post(
        "/categories",
        json={"store_id": store_id, "name": "Grains", "item_type": "Physical"},
    )
    other_category_response = client.post(
        "/categories",
        json={"store_id": store_id, "name": "Household", "item_type": "Physical"},
    )
    assert category_response.status_code == 201
    assert other_category_response.status_code == 201
    category_id = category_response.json["data"]["category"]["id"]
    other_category_id = other_category_response.json["data"]["category"]["id"]

    def make_product(name, assigned_category_id):
        response = client.post(
            "/product/create",
            json={
                "store_id": store_id,
                "name": name,
                "price": 2500,
                "opening_stock": 10,
                "low_stock_threshold": 1,
                "category_id": assigned_category_id,
            },
        )
        assert response.status_code == 201
        return response.json["data"]["product"]["id"]

    grain_id = make_product("Rice", category_id)
    household_id = make_product("Soap", other_category_id)

    for transaction_id, items, method in (
        (
            "combined-grain-transfer",
            [
                {"product_id": grain_id, "quantity": 1},
                {"product_id": household_id, "quantity": 1},
            ],
            "Transfer",
        ),
        ("combined-household-transfer", [{"product_id": household_id, "quantity": 1}], "Transfer"),
        ("combined-grain-cash", [{"product_id": grain_id, "quantity": 1}], "Cash"),
    ):
        response = client.post(
            "/sales",
            json={
                "store_id": store_id,
                "payment_method": method,
                "items": items,
                "client_transaction_id": transaction_id,
            },
        )
        assert response.status_code == 201

    today_lagos = datetime.now(ZoneInfo("Africa/Lagos")).date().isoformat()
    response = client.get(
        f"/sales?store_id={store_id}&start_date={today_lagos}"
        f"&end_date={today_lagos}&category_id={category_id}&payment_method=Transfer"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["summary"] == {
        "transaction_count": 1,
        "total_sales": "5000.00",
        "total_amount_paid": "5000.00",
        "total_balance": "0.00",
        "total_refunds": "0.00",
        "net_sales": "5000.00",
        "net_amount_paid": "5000.00",
    }
    assert len(data["sales"]) == 1
    sale = data["sales"][0]
    assert sale["client_transaction_id"] == "combined-grain-transfer"
    assert sale["payment_method"] == "Transfer"
    assert sale["items"][0]["product_name"] == "Rice"
    assert sale["items"][0]["category_id"] == category_id
    assert sale["items"][0]["category_name"] == "Grains"
    assert sale["total_amount"] == "5000.00"
    assert len(sale["items"]) == 2
    assert sale["items"][0]["line_total"] == "2500.00"
    assert {item["category_id"] for item in sale["items"]} == {category_id, other_category_id}
    assert data["filters"]["category_id"] == category_id


def test_sales_date_filter_rejects_invalid_or_reversed_dates(client):
    store_id = register_and_login(client, username="sales-date-filter-invalid-user")

    invalid = client.get(f"/sales?store_id={store_id}&start_date=22-10-2026")
    reversed_range = client.get(
        f"/sales?store_id={store_id}&start_date=2026-10-23&end_date=2026-10-22"
    )

    assert invalid.status_code == 400
    assert invalid.json["error"]["code"] == "VALIDATION_ERROR"
    assert reversed_range.status_code == 400
    assert reversed_range.json["error"]["code"] == "VALIDATION_ERROR"


def test_sales_category_filter_rejects_category_from_another_store(client):
    first_store_id = register_and_login(client, username="sales-category-owner")
    other_client = client.application.test_client()
    second_store_id = register_and_login(other_client, username="sales-category-outsider")
    category_response = other_client.post(
        "/categories",
        json={"store_id": second_store_id, "name": "Private Category", "item_type": "Physical"},
    )
    assert category_response.status_code == 201
    category_id = category_response.json["data"]["category"]["id"]

    response = client.get(
        f"/sales?store_id={first_store_id}&category_id={category_id}"
    )

    assert response.status_code == 404
    assert response.json["error"]["code"] == "CATEGORY_NOT_FOUND"



def test_offline_sale_sync_retry_does_not_create_duplicate_sale(client):
    store_id = register_and_login(client, username="offline-sync-retry-user")
    product_id = create_product(
        client,
        store_id,
        name="Offline Sync Product",
        stock_quantity=5,
    )
    payload = {
        "store_id": store_id,
        "payment_method": "Cash",
        "items": [{"product_id": product_id, "quantity": 2}],
        "client_transaction_id": "offline-client-sale-0001",
    }

    first = client.post("/sales/sync", json=payload)
    retry = client.post("/sales/sync", json=payload)

    assert first.status_code == 201
    assert first.json["data"]["sync_status"] == "accepted"
    assert first.json["data"]["client_transaction_id"] == "offline-client-sale-0001"
    assert retry.status_code == 200
    assert retry.json["data"]["sync_status"] == "already_processed"
    assert retry.json["data"]["receipt"]["sale_id"] == first.json["data"]["receipt"]["sale_id"]
    assert Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id="offline-client-sale-0001",
    ).count() == 1
    assert db.session.get(Product, product_id).stock_quantity == 3
    assert InventoryMovement.query.filter_by(product_id=product_id).count() == 1


def test_offline_sale_sync_rejects_reused_identifier_with_different_payload(client):
    store_id = register_and_login(client, username="offline-sync-conflict-user")
    product_id = create_product(
        client,
        store_id,
        name="Offline Conflict Product",
        stock_quantity=5,
    )
    first = client.post(
        "/sales/sync",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "offline-client-sale-conflict",
        },
    )
    retry = client.post(
        "/sales/sync",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 2}],
            "client_transaction_id": "offline-client-sale-conflict",
        },
    )

    assert first.status_code == 201
    assert retry.status_code == 409
    assert retry.json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert retry.json["error"]["fields"]["sync_status"] == "rejected"
    assert Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id="offline-client-sale-conflict",
    ).count() == 1
    assert db.session.get(Product, product_id).stock_quantity == 4


def test_offline_sale_sync_rejection_does_not_confirm_or_change_stock(client):
    store_id = register_and_login(client, username="offline-sync-reject-user")
    product_id = create_product(
        client,
        store_id,
        name="Offline Insufficient Stock Product",
        stock_quantity=1,
    )

    response = client.post(
        "/sales/sync",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 2}],
            "client_transaction_id": "offline-client-sale-rejected",
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"
    assert response.json["error"]["fields"]["sync_status"] == "rejected"
    assert Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id="offline-client-sale-rejected",
    ).count() == 0
    assert db.session.get(Product, product_id).stock_quantity == 1
    assert InventoryMovement.query.filter_by(product_id=product_id).count() == 0


def test_offline_sale_sync_requires_client_transaction_id(client):
    store_id = register_and_login(client, username="offline-sync-missing-id-user")
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales/sync",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["fields"]["sync_status"] == "rejected"
    assert Sale.query.count() == 0


def test_checkout_requires_idempotency_key(client):
    store_id = register_and_login(client, username="checkout-key-user")
    product_id = create_product(client, store_id)

    response = client.post(
        "/sales/checkout",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert response.json["error"]["fields"]["retryable"] is False
    assert Sale.query.count() == 0
    assert db.session.get(Product, product_id).stock_quantity == 10


def test_checkout_retry_does_not_duplicate_sale_or_stock_deduction(client):
    store_id = register_and_login(client, username="checkout-retry-user")
    product_id = create_product(client, store_id, stock_quantity=5)
    payload = {
        "store_id": store_id,
        "payment_method": "Cash",
        "items": [{"product_id": product_id, "quantity": 2, "expected_unit_price": "2500.00"}],
        "client_transaction_id": "checkout-retry-001",
    }

    first = client.post("/sales/checkout", json=payload)
    second = client.post("/sales/checkout", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json["data"]["receipt"]["sale_id"] == second.json["data"]["receipt"]["sale_id"]
    assert db.session.get(Product, product_id).stock_quantity == 3
    assert Sale.query.count() == 1
    assert SalePayment.query.count() == 1
    assert InventoryMovement.query.count() == 1


def test_checkout_stale_price_rejects_without_partial_changes(client):
    store_id = register_and_login(client, username="checkout-stale-price-user")
    first_product_id = create_product(client, store_id, name="Checkout Rice", stock_quantity=10)
    second_product_id = create_product(client, store_id, name="Checkout Bread", stock_quantity=10)
    db.session.get(Product, second_product_id).price = Decimal("3000.00")
    db.session.commit()

    response = client.post(
        "/sales/checkout",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "client_transaction_id": "checkout-stale-price-001",
            "items": [
                {"product_id": first_product_id, "quantity": 2, "expected_unit_price": "2500.00"},
                {"product_id": second_product_id, "quantity": 1, "expected_unit_price": "2500.00"},
            ],
        },
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "STALE_PRICE"
    assert response.json["error"]["fields"]["retryable"] is False
    assert response.json["error"]["fields"]["current_unit_price"] == "3000.00"
    assert db.session.get(Product, first_product_id).stock_quantity == 10
    assert db.session.get(Product, second_product_id).stock_quantity == 10
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert SalePayment.query.count() == 0
    assert InventoryMovement.query.count() == 0


def test_checkout_insufficient_stock_is_permanent_until_cart_changes(client):
    store_id = register_and_login(client, username="checkout-stock-user")
    product_id = create_product(client, store_id, stock_quantity=1)

    response = client.post(
        "/sales/checkout",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "client_transaction_id": "checkout-stock-001",
            "items": [{"product_id": product_id, "quantity": 2, "expected_unit_price": "2500.00"}],
        },
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INSUFFICIENT_STOCK"
    assert response.json["error"]["fields"]["retryable"] is False
    assert db.session.get(Product, product_id).stock_quantity == 1
    assert Sale.query.count() == 0
    assert InventoryMovement.query.count() == 0


def test_checkout_authentication_failure_is_structured_and_non_retryable(client):
    response = client.post(
        "/sales/checkout",
        json={
            "store_id": 1,
            "payment_method": "Cash",
            "client_transaction_id": "checkout-unauthenticated",
            "items": [{"product_id": 1, "quantity": 1}],
        },
    )

    assert response.status_code == 401
    assert response.json["error"]["code"] == "SESSION_NOT_FOUND"
    assert response.json["error"]["fields"]["retryable"] is False


def test_checkout_unexpected_failure_rolls_back_and_returns_safe_error(client, monkeypatch):
    store_id = register_and_login(client, username="checkout-unexpected-user")
    product_id = create_product(client, store_id, stock_quantity=5)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("sensitive internal diagnostic must not reach client")

    monkeypatch.setattr("server.app.services.sales_service.AuditService.record", fail_audit)
    response = client.post(
        "/sales/checkout",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "client_transaction_id": "checkout-unexpected-001",
            "items": [{"product_id": product_id, "quantity": 2, "expected_unit_price": "2500.00"}],
        },
    )

    assert response.status_code == 500
    assert response.json["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "sensitive internal diagnostic" not in response.get_data(as_text=True)
    assert response.json["error"]["fields"]["retryable"] is True
    assert db.session.get(Product, product_id).stock_quantity == 5
    assert Sale.query.count() == 0
    assert SaleItem.query.count() == 0
    assert SalePayment.query.count() == 0
    assert InventoryMovement.query.count() == 0
