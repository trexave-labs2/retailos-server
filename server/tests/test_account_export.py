import json

import pytest

from server.app.extensions import db
from server.app.models.alert import Alert, AlertType
from server.app.models.category import Category
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.models.sale_payment import SalePayment
from server.app.models.stock_receiving import StockReceiving
from server.app.models.store import Store
from server.app.models.user import User
from server.app.services import account_export_service


def register_and_login(client, username, email):
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "test123"},
    )
    assert response.status_code == 201
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "test123"},
    )
    assert login.status_code == 200
    return login.json["data"]["user"]["store_id"]


def start_pro_trial(client, store_id):
    response = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert response.status_code == 200


@pytest.fixture()
def two_export_owners(app):
    owner = app.test_client()
    attacker = app.test_client()
    owner_store_id = register_and_login(
        owner, "export-owner", "export-owner@example.com"
    )
    attacker_store_id = register_and_login(
        attacker, "export-attacker", "export-attacker@example.com"
    )
    assert owner_store_id != attacker_store_id
    return owner, attacker, owner_store_id, attacker_store_id


def test_export_requires_authenticated_session(client):
    response = client.get("/settings/export?store_id=1")
    assert response.status_code == 401
    assert response.json["error"]["code"] == "SESSION_NOT_FOUND"


def test_export_rejects_foreign_store(two_export_owners):
    _, attacker, owner_store_id, _ = two_export_owners
    response = attacker.get(f"/settings/export?store_id={owner_store_id}")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"


def test_owner_export_contains_store_records_and_preserves_relationships(
    two_export_owners,
):
    owner_client, _, store_id, _ = two_export_owners
    start_pro_trial(owner_client, store_id)
    store = Store.query.filter_by(id=store_id).first()
    user = User.query.filter_by(id=store.user_id).first()

    category = Category(store_id=store_id, name="Drinks", item_type="Physical")
    db.session.add(category)
    db.session.flush()

    product = Product(
        store_id=store_id, name="Water", product_type="Physical",
        base_unit="piece", price="100.00", stock_quantity=8,
        low_stock_threshold=2, category_id=category.id,
    )
    customer = Customer(
        store_id=store_id, name="Ada Customer", contact="08000000000",
        outstanding_balance="50.00",
    )
    db.session.add_all([product, customer])
    db.session.flush()

    unit = ProductUnit(
        product_id=product.id, name="pack", base_quantity=6, price="600.00"
    )
    sale = Sale(
        store_id=store_id, customer_id=customer.id,
        client_transaction_id="export-sale-001", total_amount="200.00",
        amount_paid="150.00", balance="50.00", payment_method="Cash",
    )
    db.session.add_all([unit, sale])
    db.session.flush()

    sale_item = SaleItem(
        sale_id=sale.id, product_id=product.id, unit_id=unit.id,
        unit_name_at_sale="pack", unit_base_quantity=6,
        base_quantity_deducted=2, quantity=1,
        price_at_sale="200.00", total="200.00",
    )
    payment = SalePayment(
        sale_id=sale.id, amount="150.00", payment_method="Cash",
        client_transaction_id="export-payment-001",
    )
    movement = InventoryMovement(
        store_id=store_id, product_id=product.id, user_id=user.id,
        movement_type="sale", quantity_change=-2, previous_quantity=10,
        new_quantity=8, reason="Sale completed",
    )
    receiving = StockReceiving(
        store_id=store_id, product_id=product.id, user_id=user.id,
        unit_id=unit.id, quantity=1, unit_name="pack",
        unit_base_quantity=6, base_quantity_received=6,
        unit_cost="400.00", total_cost="400.00",
        supplier_or_source="Supplier", client_transaction_id="export-receive-001",
        receiving_date=product.created_at,
    )
    alert = Alert(
        store_id=store_id, product_id=product.id, type=AlertType.LOW_STOCK,
        message="Water is low on stock", is_resolved=False,
        condition_active=True,
    )
    db.session.add_all([sale_item, payment, movement, receiving, alert])
    db.session.commit()

    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert "attachment;" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store, private"

    exported = json.loads(response.get_data(as_text=True))
    assert exported["format"] == "retailos-account-export"
    assert exported["version"] == 1
    assert exported["store"]["id"] == store_id
    assert exported["owner"]["email"] == "export-owner@example.com"
    assert exported["data"]["categories"][0]["name"] == "Drinks"
    assert exported["data"]["products"][0]["name"] == "Water"
    assert exported["data"]["product_units"][0]["product_id"] == product.id
    assert exported["data"]["customers"][0]["name"] == "Ada Customer"
    assert exported["data"]["sales"][0]["customer_id"] == customer.id
    assert exported["data"]["sale_items"][0]["sale_id"] == sale.id
    assert exported["data"]["sale_payments"][0]["sale_id"] == sale.id
    assert exported["data"]["inventory_movements"][0]["product_id"] == product.id
    assert exported["data"]["stock_receivings"][0]["product_id"] == product.id
    assert exported["data"]["alerts"][0]["product_id"] == product.id
    assert exported["limits"]["records_exported"] >= 10

    body = response.get_data(as_text=True).lower()
    for forbidden in (
        "password_hash", "session_version", "password_reset_tokens",
        "secret_key", "cookie", "session_id",
    ):
        assert forbidden not in body


def test_empty_store_export_returns_all_empty_collections(client):
    store_id = register_and_login(
        client, "empty-export-owner", "empty-export@example.com"
    )
    start_pro_trial(client, store_id)
    response = client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 200
    exported = response.get_json()
    assert exported["limits"]["records_exported"] == 0
    assert exported["data"]
    assert all(records == [] for records in exported["data"].values())


def test_export_rejects_invalid_store_id(client):
    store_id = register_and_login(
        client, "invalid-export-owner", "invalid-export@example.com"
    )
    response = client.get(f"/settings/export?store_id={store_id}abc")
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


def test_export_has_a_hard_record_limit(two_export_owners, monkeypatch):
    owner_client, _, store_id, _ = two_export_owners
    start_pro_trial(owner_client, store_id)
    db.session.add_all([
        Category(store_id=store_id, name=f"Category {index}", item_type="Physical")
        for index in range(2)
    ])
    db.session.commit()
    monkeypatch.setattr(account_export_service, "MAX_EXPORT_RECORDS", 1)

    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 413
    assert response.json["error"]["code"] == "EXPORT_TOO_LARGE"
    assert response.json["error"]["fields"]["maximum_records"] == 1


def test_free_plan_export_is_denied(two_export_owners):
    owner_client, _, store_id, _ = two_export_owners
    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert response.json["error"]["fields"]["feature"] == "data_export"


def test_active_pro_trial_can_export_account(two_export_owners):
    owner_client, _, store_id, _ = two_export_owners
    trial = owner_client.post("/subscriptions/trial", json={"store_id": store_id})
    assert trial.status_code == 200

    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 200
    assert response.json["format"] == "retailos-account-export"


def test_active_paid_pro_can_export_account(two_export_owners):
    from datetime import datetime, timedelta, timezone

    owner_client, _, store_id, _ = two_export_owners
    store = db.session.get(Store, store_id)
    store.current_plan = "pro"
    store.subscription_status = "active"
    store.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
    db.session.commit()

    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 200
    assert response.json["format"] == "retailos-account-export"


def test_expired_pro_trial_cannot_export_account(two_export_owners):
    from datetime import datetime, timedelta, timezone

    owner_client, _, store_id, _ = two_export_owners
    store = db.session.get(Store, store_id)
    now = datetime.now(timezone.utc)
    store.current_plan = "pro"
    store.subscription_status = "trial"
    store.trial_used_at = now - timedelta(days=15)
    store.current_period_end = now - timedelta(seconds=1)
    db.session.commit()

    response = owner_client.get(f"/settings/export?store_id={store_id}")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert response.json["error"]["fields"]["feature"] == "data_export"

    db.session.refresh(store)
    assert store.current_plan == "free"
    assert store.subscription_status == "expired"
