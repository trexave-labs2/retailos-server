from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.models.refund import Refund
from server.app.models.sale import Sale
from server.app.models.user import User
from server.app.services.audit_service import _safe_values


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


def test_price_change_and_refund_are_traceable_to_actor_and_business_records(client):
    store_id = register_and_login(client, "audit-acceptance")
    user = User.query.filter_by(email="audit-acceptance@example.com").one()

    product_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Audit item",
            "price": "600.00",
            "opening_stock": 4,
        },
    )
    assert product_response.status_code == 201
    product_id = product_response.json["data"]["product"]["id"]

    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "audit-sale-001",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    update = client.patch(
        "/product/update",
        json={"store_id": store_id, "id": product_id, "price": "650.00"},
    )
    assert update.status_code == 200

    refund_response = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "600.00",
            "reason": "Audit acceptance return",
            "payment_method": "Cash",
            "client_transaction_id": "audit-refund-001",
        },
    )
    assert refund_response.status_code == 201
    refund_id = refund_response.json["data"]["refund"]["id"]

    price_log = AuditLog.query.filter_by(
        store_id=store_id, action_type="PRODUCT_PRICE_CHANGED", entity_id=str(product_id)
    ).one()
    refund_log = AuditLog.query.filter_by(
        store_id=store_id, action_type="REFUND_PROCESSED", entity_id=str(refund_id)
    ).one()

    assert price_log.user_id == user.id
    assert price_log.before_values == {"price": "600.00"}
    assert price_log.after_values == {"price": "650.00"}
    assert refund_log.user_id == user.id
    assert refund_log.after_values["sale_id"] == sale_id
    assert refund_log.after_values["amount"] == "600.00"

    history = client.get(f"/audit?store_id={store_id}&action_type=REFUND_PROCESSED&user_id={user.id}")
    assert history.status_code == 200
    rows = history.json["data"]["audit_logs"]
    assert len(rows) == 1
    assert rows[0]["entity_id"] == str(refund_id)
    assert rows[0]["user_id"] == user.id
    assert rows[0]["created_at"]


def test_failed_refund_does_not_create_success_audit_record(client):
    store_id = register_and_login(client, "audit-failed-refund")
    product_response = client.post(
        "/product/create",
        json={"store_id": store_id, "name": "Failed refund item", "price": 100, "opening_stock": 2},
    )
    product_id = product_response.json["data"]["product"]["id"]
    sale_response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "audit-failed-sale",
        },
    )
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    response = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": sale_id,
            "amount": "999.00",
            "reason": "Must fail",
            "payment_method": "Cash",
            "client_transaction_id": "audit-failed-refund-001",
        },
    )
    assert response.status_code == 400
    assert AuditLog.query.filter_by(action_type="REFUND_PROCESSED").count() == 0


def test_audit_query_rejects_unauthorized_store(client):
    store_id = register_and_login(client, "audit-owner")
    other_client = client.application.test_client()
    response = other_client.post(
        "/auth/register",
        json={"username": "audit-other", "email": "audit-other@example.com", "password": "test123"},
    )
    assert response.status_code == 201
    response = other_client.post(
        "/auth/login",
        json={"email": "audit-other@example.com", "password": "test123"},
    )
    assert response.status_code == 200
    response = other_client.get(f"/audit?store_id={store_id}")
    assert response.status_code == 403


def test_sensitive_credentials_are_removed_from_audit_values():
    safe = _safe_values({
        "price": "10.00",
        "password": "do-not-store",
        "password_hash": "hash",
        "nested": {"access_token": "secret", "quantity": 2},
    })
    assert safe == {"price": "10.00", "nested": {"quantity": 2}}


def test_audit_records_cannot_be_modified_or_deleted(app, client):
    store_id = register_and_login(client, "audit-immutable")
    response = client.post(
        "/product/create",
        json={"store_id": store_id, "name": "Immutable item", "price": 10, "opening_stock": 1},
    )
    assert response.status_code == 201
    entry = AuditLog.query.filter_by(action_type="PRODUCT_PRICE_CHANGED").first()
    if entry is None:
        entry = AuditLog(
            store_id=store_id,
            action_type="TEST_ACTION",
            entity_type="test",
            entity_id="1",
            details={"safe": True},
        )
        db.session.add(entry)
        db.session.commit()

    entry.details = {"changed": True}
    try:
        db.session.commit()
        assert False, "Updating an audit record should be blocked"
    except ValueError:
        db.session.rollback()

    entry = AuditLog.query.filter_by(action_type="TEST_ACTION").first()
    if entry is not None:
        db.session.delete(entry)
        try:
            db.session.commit()
            assert False, "Deleting an audit record should be blocked"
        except ValueError:
            db.session.rollback()
