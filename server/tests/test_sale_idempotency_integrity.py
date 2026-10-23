import pytest

from server.app.extensions import db
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


def create_product(client, store_id, username):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": f"Replay Product {username}",
            "price": "2500.00",
            "opening_stock": 2,
            "low_stock_threshold": 1,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.json["data"]["product"]["id"]


@pytest.mark.parametrize(
    ("endpoint", "username", "transaction_id"),
    [
        ("/sales", "replay-sales-endpoint", "replay-sale-direct-001"),
        ("/sales/checkout", "replay-checkout-endpoint", "replay-sale-checkout-001"),
        ("/sales/sync", "replay-sync-endpoint", "replay-sale-sync-001"),
    ],
)
def test_sale_retry_after_lost_response_does_not_duplicate_transaction(
    client, endpoint, username, transaction_id
):
    """Simulate a committed sale whose response was lost before client confirmation."""
    store_id = register_and_login(client, username)
    product_id = create_product(client, store_id, username)
    item = {"product_id": product_id, "quantity": 2}
    if endpoint == "/sales/checkout":
        item["expected_unit_price"] = "2500.00"

    payload = {
        "store_id": store_id,
        "payment_method": "Cash",
        "client_transaction_id": transaction_id,
        "items": [item],
    }

    # The first response represents a response the client never receives.
    # The persisted server transaction still exists, so the client must retry
    # the exact same identifier and payload.
    first = client.post(endpoint, json=payload)
    assert first.status_code == 201, first.get_json()
    original_sale_id = first.json["data"]["receipt"]["sale_id"]

    retry = client.post(endpoint, json=payload)
    assert retry.status_code == 200, retry.get_json()
    assert retry.json["data"]["receipt"]["sale_id"] == original_sale_id

    if endpoint == "/sales/sync":
        assert first.json["data"]["sync_status"] == "accepted"
        assert retry.json["data"]["sync_status"] == "already_processed"

    # Stock is now zero. A correct idempotent retry returns the saved sale
    # before attempting a second stock deduction.
    product = db.session.get(Product, product_id)
    assert product.stock_quantity == 0
    assert Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id=transaction_id,
    ).count() == 1
    assert SaleItem.query.filter_by(sale_id=original_sale_id).count() == 1
    assert SalePayment.query.filter_by(sale_id=original_sale_id).count() == 1
    assert InventoryMovement.query.filter_by(
        store_id=store_id,
        product_id=product_id,
    ).count() == 1

    listed = client.get(f"/sales?store_id={store_id}")
    assert listed.status_code == 200
    matching_sales = [
        sale for sale in listed.json["data"]["sales"]
        if sale["client_transaction_id"] == transaction_id
    ]
    assert len(matching_sales) == 1


@pytest.mark.parametrize(
    ("endpoint", "username", "transaction_id"),
    [
        ("/sales", "changed-payload-sales", "reused-direct-sale-001"),
        ("/sales/checkout", "changed-payload-checkout", "reused-checkout-sale-001"),
        ("/sales/sync", "changed-payload-sync", "reused-sync-sale-001"),
    ],
)
def test_sale_idempotency_key_cannot_be_reused_for_different_quantity(
    client, endpoint, username, transaction_id
):
    store_id = register_and_login(client, username)
    product_id = create_product(client, store_id, username)
    first_item = {"product_id": product_id, "quantity": 1}
    changed_item = {"product_id": product_id, "quantity": 2}
    if endpoint == "/sales/checkout":
        first_item["expected_unit_price"] = "2500.00"
        changed_item["expected_unit_price"] = "2500.00"

    base_payload = {
        "store_id": store_id,
        "payment_method": "Cash",
        "client_transaction_id": transaction_id,
    }
    first = client.post(endpoint, json={**base_payload, "items": [first_item]})
    assert first.status_code == 201, first.get_json()

    changed = client.post(endpoint, json={**base_payload, "items": [changed_item]})
    assert changed.status_code == 409, changed.get_json()
    assert changed.json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    if endpoint in ("/sales/checkout", "/sales/sync"):
        assert changed.json["error"]["fields"]["sync_status"] == "rejected" if endpoint == "/sales/sync" else changed.json["error"]["fields"]["retryable"] is False

    sale = Sale.query.filter_by(
        store_id=store_id,
        client_transaction_id=transaction_id,
    ).one()
    assert Sale.query.filter_by(store_id=store_id).count() == 1
    assert SaleItem.query.filter_by(sale_id=sale.id).count() == 1
    assert SalePayment.query.filter_by(sale_id=sale.id).count() == 1
    assert db.session.get(Product, product_id).stock_quantity == 1
    assert InventoryMovement.query.filter_by(
        store_id=store_id,
        product_id=product_id,
    ).count() == 1
