from server.app.extensions import db
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.refund import Refund, RefundItem
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


def create_product(client, store_id, name="Refundable item", price=600, stock=3):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": price,
            "opening_stock": stock,
            "low_stock_threshold": 1,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["product"]["id"]


def create_sale(client, store_id, product_id, key="refund-test-sale"):
    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": key,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["receipt"]["sale_id"]


def refund_payload(store_id, sale_id, key="refund-test-key", amount="600.00", **extra):
    return {
        "store_id": store_id,
        "sale_id": sale_id,
        "amount": amount,
        "reason": "Customer returned the item",
        "payment_method": "Cash",
        "client_transaction_id": key,
        **extra,
    }


def test_full_refund_has_unique_reference_receipt_and_restores_explicitly_returned_stock(client):
    store_id = register_and_login(client, "refund-acceptance")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)

    sale_item = SaleItem.query.filter_by(sale_id=sale_id).one()
    response = client.post(
        "/sales/refunds",
        json=refund_payload(
            store_id,
            sale_id,
            returned_items=[{"sale_item_id": sale_item.id, "quantity": 1}],
        ),
    )

    assert response.status_code == 201
    refund = response.json["data"]["refund"]
    assert refund["amount"] == "600.00"
    assert refund["refund_type"] == "Full"
    assert refund["reference"].startswith("RFD-")
    assert refund["sale_id"] == sale_id
    assert refund["reason"] == "Customer returned the item"
    assert refund["payment_method"] == "Cash"
    assert refund["receipt"]["receipt_type"] == "Refund"
    assert refund["receipt"]["original_sale_id"] == sale_id
    assert "Amount refunded: 600.00" in refund["receipt"]["printable_text"]

    product = db.session.get(Product, product_id)
    assert product.stock_quantity == 3
    movement = InventoryMovement.query.filter_by(
        product_id=product_id,
        movement_type="REFUND",
    ).one()
    assert movement.quantity_change == 1
    assert RefundItem.query.filter_by(refund_id=refund["id"]).count() == 1


def test_refund_cannot_exceed_remaining_refundable_amount_and_retry_is_idempotent(client):
    store_id = register_and_login(client, "refund-idempotency")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)
    payload = refund_payload(store_id, sale_id)

    first = client.post("/sales/refunds", json=payload)
    retry = client.post("/sales/refunds", json=payload)
    over_refund = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="second-refund-key", amount="0.01"),
    )

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json["message"] == "REFUND_ALREADY_PROCESSED"
    assert retry.json["data"]["refund"]["reference"] == first.json["data"]["refund"]["reference"]
    assert over_refund.status_code == 400
    assert over_refund.json["error"]["code"] == "REFUND_EXCEEDS_REMAINING_AMOUNT"
    assert Refund.query.filter_by(sale_id=sale_id).count() == 1


def test_partial_refunds_cannot_exceed_remaining_amount(client):
    store_id = register_and_login(client, "refund-partial")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)

    first = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="partial-refund-1", amount="200.00"),
    )
    over = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="partial-refund-2", amount="400.01"),
    )
    second = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="partial-refund-3", amount="400.00"),
    )

    assert first.status_code == 201
    assert first.json["data"]["refund"]["refund_type"] == "Partial"
    assert over.status_code == 400
    assert over.json["error"]["code"] == "REFUND_EXCEEDS_REMAINING_AMOUNT"
    assert second.status_code == 201
    assert second.json["data"]["refund"]["refund_type"] == "Full"
    assert Refund.query.filter_by(sale_id=sale_id).count() == 2


def test_refund_key_reuse_with_different_payload_is_rejected(client):
    store_id = register_and_login(client, "refund-key-mismatch")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)
    first = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, amount="100.00"),
    )
    second = client.post(
        "/sales/refunds",
        json=refund_payload(
            store_id,
            sale_id,
            amount="100.00",
            reason="Different reason",
        ),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert Refund.query.filter_by(sale_id=sale_id).count() == 1


def test_refund_stock_is_not_restored_without_explicit_returned_items(client):
    store_id = register_and_login(client, "refund-no-restock")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)

    response = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="no-restock-refund"),
    )

    assert response.status_code == 201
    assert db.session.get(Product, product_id).stock_quantity == 2
    assert InventoryMovement.query.filter_by(
        product_id=product_id,
        movement_type="REFUND",
    ).count() == 0


def test_refund_updates_sales_and_dashboard_net_financial_summaries(client):
    store_id = register_and_login(client, "refund-financial-summary")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)
    response = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="summary-refund"),
    )
    assert response.status_code == 201

    sales_response = client.get(f"/sales?store_id={store_id}")
    assert sales_response.status_code == 200
    summary = sales_response.json["data"]["summary"]
    assert summary["total_sales"] == "600.00"
    assert summary["total_amount_paid"] == "600.00"
    assert summary["total_refunds"] == "600.00"
    assert summary["net_sales"] == "0.00"
    assert summary["net_amount_paid"] == "0.00"
    assert sales_response.json["data"]["sales"][0]["refunded_amount"] == "600.00"
    assert sales_response.json["data"]["sales"][0]["net_amount"] == "0.00"

    dashboard_response = client.get(f"/dashboard?store_id={store_id}")
    assert dashboard_response.status_code == 200
    assert dashboard_response.json["data"]["today_sales_total"] == "0.00"

    daily_summary = client.get(f"/dashboard/daily-summary?store_id={store_id}")
    assert daily_summary.status_code == 200
    assert daily_summary.json["data"]["gross_sales"] == "600.00"
    assert daily_summary.json["data"]["total_refunds"] == "600.00"
    assert daily_summary.json["data"]["total_sales"] == "0.00"


def test_refund_actions_are_store_authorized(client):
    owner_store = register_and_login(client, "refund-owner")
    product_id = create_product(client, owner_store)
    sale_id = create_sale(client, owner_store, product_id)

    other_client = client.application.test_client()
    other_store = register_and_login(other_client, "refund-outsider")
    response = other_client.post(
        "/sales/refunds",
        json=refund_payload(owner_store, sale_id, key="unauthorized-refund"),
    )

    assert other_store != owner_store
    assert response.status_code == 403
    assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"


def test_refund_receipt_and_history_are_store_scoped(client):
    store_id = register_and_login(client, "refund-history")
    product_id = create_product(client, store_id)
    sale_id = create_sale(client, store_id, product_id)
    created = client.post(
        "/sales/refunds",
        json=refund_payload(store_id, sale_id, key="history-refund"),
    )
    refund_id = created.json["data"]["refund"]["id"]

    history = client.get(f"/sales/refunds?store_id={store_id}&sale_id={sale_id}")
    receipt = client.get(f"/sales/refunds/receipt?store_id={store_id}&refund_id={refund_id}")

    assert history.status_code == 200
    assert history.json["data"]["refund_count"] == 1
    assert history.json["data"]["total_refunded"] == "600.00"
    assert receipt.status_code == 200
    assert receipt.json["data"]["receipt"]["refund_reference"] == created.json["data"]["refund"]["reference"]
