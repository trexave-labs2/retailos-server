from datetime import timedelta

import pytest

from server.app.extensions import db
from server.app.models.audit_log import AuditLog
from server.app.models.category import Category
from server.app.models.customer import Customer
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.models.store import Store
from server.app.models.user import User
from server.app.services.account_deletion_service import AccountDeletionService
from server.app.utils.time import now_utc


def register_and_login(client, email="owner@example.com", username="owner"):
    response = client.post("/auth/register", json={
        "username": username,
        "email": email,
        "password": "secure-password-123",
    })
    assert response.status_code == 201
    response = client.post("/auth/login", json={
        "email": email,
        "password": "secure-password-123",
    })
    assert response.status_code == 200
    return response.json["data"]["user"]["store_id"]


def request_deletion(client):
    return client.post("/auth/delete-account", json={
        "current_password": "secure-password-123",
        "confirmation": "DELETE MY ACCOUNT",
    })


def test_account_deletion_requires_recent_password_and_exact_confirmation(client):
    register_and_login(client)
    response = client.post("/auth/delete-account", json={
        "current_password": "wrong-password",
        "confirmation": "DELETE MY ACCOUNT",
    })
    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_INVALID_CURRENT_PASSWORD"

    response = client.post("/auth/delete-account", json={
        "current_password": "secure-password-123",
        "confirmation": "delete",
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


def test_account_deletion_deactivates_only_the_requesting_account(client):
    first_store_id = register_and_login(client)
    first_user = User.query.filter_by(email="owner@example.com").first()
    first_user_id = first_user.id

    client.post("/auth/logout")
    second_store_id = register_and_login(
        client, email="other@example.com", username="other-owner"
    )
    second_user = User.query.filter_by(email="other@example.com").first()

    # Log back in as the first owner and request closure.
    client.post("/auth/logout")
    client.post("/auth/login", json={
        "email": "owner@example.com",
        "password": "secure-password-123",
    })
    response = request_deletion(client)

    assert response.status_code == 202
    assert response.json["data"]["account_status"] == "pending_deletion"
    assert response.json["data"]["store_count"] == 1
    assert db.session.get(User, first_user_id).account_status == "pending_deletion"
    assert db.session.get(Store, first_store_id).status == "pending_deletion"
    assert second_user.account_status == "active"
    assert db.session.get(Store, second_store_id).status == "active"

    # The old signed cookie is cleared and a new login is blocked during recovery.
    assert client.get("/auth/me").status_code == 401
    blocked_login = client.post("/auth/login", json={
        "email": "owner@example.com",
        "password": "secure-password-123",
    })
    assert blocked_login.status_code == 403
    assert blocked_login.json["error"]["code"] == "ACCOUNT_CLOSURE_PENDING"


def test_account_deletion_can_be_cancelled_with_credentials_before_deadline(client):
    store_id = register_and_login(client)
    assert request_deletion(client).status_code == 202

    response = client.post("/auth/cancel-account-deletion", json={
        "email": "owner@example.com",
        "password": "secure-password-123",
    })
    assert response.status_code == 200
    assert response.json["data"]["account_status"] == "active"
    assert User.query.filter_by(email="owner@example.com").first().account_status == "active"
    assert db.session.get(Store, store_id).status == "active"

    login = client.post("/auth/login", json={
        "email": "owner@example.com",
        "password": "secure-password-123",
    })
    assert login.status_code == 200


def test_account_deletion_cannot_be_cancelled_with_wrong_password(client):
    register_and_login(client)
    assert request_deletion(client).status_code == 202

    response = client.post("/auth/cancel-account-deletion", json={
        "email": "owner@example.com",
        "password": "incorrect-password",
    })
    assert response.status_code == 401
    assert User.query.filter_by(email="owner@example.com").first().account_status == "pending_deletion"


def test_finalization_anonymizes_personal_data_and_preserves_history(client):
    store_id = register_and_login(client)
    user = User.query.filter_by(email="owner@example.com").first()
    store = db.session.get(Store, store_id)
    customer = Customer(store_id=store_id, name="Customer Personal Name", contact="08000000000")
    category = Category(store_id=store_id, name="Groceries", item_type="Physical")
    db.session.add_all([customer, category])
    db.session.flush()

    product = Product(
        store_id=store_id,
        name="Named Product",
        product_type="Physical",
        base_unit="piece",
        price=100,
        stock_quantity=5,
        low_stock_threshold=1,
        category_id=category.id,
    )
    db.session.add(product)
    db.session.flush()
    sale = Sale(
        store_id=store_id,
        customer_id=customer.id,
        client_transaction_id="DELETE-TEST-SALE",
        total_amount=100,
        amount_paid=100,
        balance=0,
        payment_method="Cash",
        created_at=now_utc(),
    )
    db.session.add(sale)
    db.session.flush()
    db.session.add(SaleItem(
        sale_id=sale.id,
        product_id=product.id,
        quantity=1,
        price_at_sale=100,
        total=100,
    ))
    db.session.add(InventoryMovement(
        store_id=store_id,
        product_id=product.id,
        user_id=user.id,
        movement_type="adjustment",
        quantity_change=5,
        previous_quantity=0,
        new_quantity=5,
        reason="Opening stock",
    ))
    assert request_deletion(client).status_code == 202
    user = db.session.get(User, user.id)
    user.deletion_scheduled_for = now_utc() - timedelta(seconds=1)
    db.session.commit()

    assert AccountDeletionService.finalize_due_accounts() == {"accounts": 1, "stores": 1}

    db.session.expire_all()
    user = db.session.get(User, user.id)
    store = db.session.get(Store, store_id)
    customer = db.session.get(Customer, customer.id)
    product = db.session.get(Product, product.id)

    assert user.account_status == "anonymized"
    assert user.name == "Deleted Account"
    assert user.email == f"deleted-account-{user.id}@invalid.retailos"
    assert user.platform_role is None
    assert store.status == "deleted"
    assert store.name == f"Deleted Store {store_id}"
    assert customer.name == f"Deleted customer {customer.id}"
    assert customer.contact is None
    assert product.name == f"Archived item {product.id}"
    assert product.category_id is None
    assert Sale.query.filter_by(client_transaction_id="DELETE-TEST-SALE").count() == 1
    assert SaleItem.query.filter_by(sale_id=sale.id).count() == 1
    assert InventoryMovement.query.filter_by(product_id=product.id).count() == 1
    assert Category.query.filter_by(store_id=store_id).count() == 0
    assert AuditLog.query.filter_by(store_id=store_id).count() >= 2
    assert AuditLog.query.filter_by(
        store_id=store_id,
        action_type="account_deletion_finalized",
    ).count() == 1

    # Running the scheduled cleanup again is safe and does not duplicate work.
    assert AccountDeletionService.finalize_due_accounts() == {"accounts": 0, "stores": 0}


def test_finalization_rolls_back_all_changes_on_partial_failure(client, monkeypatch):
    store_id = register_and_login(client)
    user = User.query.filter_by(email="owner@example.com").first()
    assert request_deletion(client).status_code == 202
    user = db.session.get(User, user.id)
    user.deletion_scheduled_for = now_utc() - timedelta(seconds=1)
    db.session.commit()

    def fail_after_start(cls, store, completed_at, audit_action):
        store.name = "Partially changed"
        raise RuntimeError("simulated finalization failure")

    monkeypatch.setattr(
        AccountDeletionService,
        "_finalize_store",
        classmethod(fail_after_start),
    )
    with pytest.raises(RuntimeError):
        AccountDeletionService.finalize_due_accounts()

    db.session.expire_all()
    assert db.session.get(User, user.id).account_status == "pending_deletion"
    assert db.session.get(Store, store_id).status == "pending_deletion"
    assert db.session.get(Store, store_id).name == "owner's Store"


def test_account_deletion_requires_an_authenticated_session(client):
    response = client.post("/auth/delete-account", json={
        "current_password": "secure-password-123",
        "confirmation": "DELETE MY ACCOUNT",
    })
    assert response.status_code == 401
    assert response.json["error"]["code"] == "SESSION_NOT_FOUND"


def test_account_deletion_invalidates_other_active_sessions(client):
    store_id = register_and_login(client)
    second_client = client.application.test_client()
    second_login = second_client.post("/auth/login", json={
        "email": "owner@example.com",
        "password": "secure-password-123",
    })
    assert second_login.status_code == 200

    assert request_deletion(client).status_code == 202
    response = second_client.get("/auth/me")
    assert response.status_code == 401
    assert response.json["error"]["code"] == "SESSION_INVALIDATED"
    assert db.session.get(Store, store_id).status == "pending_deletion"



def test_store_deletion_is_owner_scoped_and_does_not_close_other_stores(client):
    first_store_id = register_and_login(client)
    owner = User.query.filter_by(email="owner@example.com").first()
    second_owned_store = Store(user_id=owner.id, name="Second Store")
    db.session.add(second_owned_store)
    db.session.commit()

    other_client = client.application.test_client()
    other_store_id = register_and_login(
        other_client, email="other-store-owner@example.com", username="other-owner"
    )

    payload = {
        "store_id": first_store_id,
        "current_password": "secure-password-123",
        "confirmation": "DELETE STORE",
    }
    response = client.post("/settings/store/delete", json=payload)
    assert response.status_code == 202
    assert db.session.get(Store, first_store_id).status == "pending_deletion"
    assert db.session.get(Store, second_owned_store.id).status == "active"
    assert owner.account_status == "active"

    # The same owner cannot close a different account's store by changing the ID.
    payload["store_id"] = other_store_id
    cross_store = client.post("/settings/store/delete", json=payload)
    assert cross_store.status_code == 403
    assert db.session.get(Store, other_store_id).status == "active"

    # The account remains usable, and the owner can cancel this store request.
    assert client.get("/auth/me").status_code == 200
    restored = client.post("/settings/store/cancel-deletion", json={
        "store_id": first_store_id,
        "current_password": "secure-password-123",
    })
    assert restored.status_code == 200
    assert db.session.get(Store, first_store_id).status == "active"


def test_store_finalization_preserves_account_and_other_stores(client):
    store_id = register_and_login(client)
    owner = User.query.filter_by(email="owner@example.com").first()
    other_store = Store(user_id=owner.id, name="Keep This Store")
    db.session.add(other_store)
    db.session.commit()

    response = client.post("/settings/store/delete", json={
        "store_id": store_id,
        "current_password": "secure-password-123",
        "confirmation": "DELETE STORE",
    })
    assert response.status_code == 202
    store = db.session.get(Store, store_id)
    store.deletion_scheduled_for = now_utc() - timedelta(seconds=1)
    db.session.commit()

    assert AccountDeletionService.finalize_due_accounts() == {"accounts": 0, "stores": 1}
    db.session.expire_all()
    assert db.session.get(Store, store_id).status == "deleted"
    assert db.session.get(Store, other_store.id).status == "active"
    assert db.session.get(User, owner.id).account_status == "active"
    assert AuditLog.query.filter_by(
        store_id=store_id,
        action_type="store_deletion_finalized",
    ).count() == 1



@pytest.mark.parametrize("platform_role", ["admin", "super_admin"])
def test_platform_admin_roles_cannot_use_self_service_deletion(client, platform_role):
    store_id = register_and_login(client)
    user = User.query.filter_by(email="owner@example.com").first()
    user.platform_role = platform_role
    db.session.commit()

    account_response = request_deletion(client)
    store_response = client.post("/settings/store/delete", json={
        "store_id": store_id,
        "current_password": "secure-password-123",
        "confirmation": "DELETE STORE",
    })

    assert account_response.status_code == 403
    assert store_response.status_code == 403
    assert user.account_status == "active"
    assert db.session.get(Store, store_id).status == "active"
