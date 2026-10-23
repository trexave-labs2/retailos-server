"""Cross-store authorization regression tests.

Every endpoint that accepts a store identifier must reject a session that does
not own that store. These tests deliberately use a valid foreign store ID so
they exercise authorization rather than malformed-input validation.
"""

import pytest


def register_and_login(client, username):
    email = f"{username}@example.com"
    registered = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "test123",
        },
    )
    assert registered.status_code == 201

    logged_in = client.post(
        "/auth/login",
        json={"email": email, "password": "test123"},
    )
    assert logged_in.status_code == 200
    return logged_in.json["data"]["user"]["store_id"]


@pytest.fixture()
def two_store_clients(app):
    owner_client = app.test_client()
    attacker_client = app.test_client()
    owner_store_id = register_and_login(owner_client, "authorization-owner")
    attacker_store_id = register_and_login(attacker_client, "authorization-attacker")
    assert owner_store_id != attacker_store_id
    return owner_client, attacker_client, owner_store_id, attacker_store_id


@pytest.mark.parametrize(
    "path",
    [
        "/product/list",
        "/product/movements",
        "/product/receivings",
        "/categories",
        "/customers",
        "/customers/owes",
        "/sales",
        "/sales/refunds",
        "/alerts",
        "/audit",
        "/dashboard",
        "/dashboard/rankings",
        "/dashboard/time-series",
        "/dashboard/daily-summary",
        "/dashboard/daily-brief",
    ],
)
def test_store_scoped_list_endpoints_reject_another_stores_id(two_store_clients, path):
    _, attacker, owner_store_id, _ = two_store_clients

    response = attacker.get(f"{path}?store_id={owner_store_id}")

    assert response.status_code == 403, (
        f"{path} must reject a valid store ID owned by another account; "
        f"got {response.status_code}: {response.get_json()}"
    )
    assert response.json["success"] is False
    assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/product/create",
            {
                "name": "Foreign store product",
                "price": 100,
                "opening_stock": 1,
                "low_stock_threshold": 1,
            },
        ),
        (
            "post",
            "/customers",
            {"name": "Foreign store customer", "contact": "08000000000"},
        ),
        (
            "post",
            "/categories",
            {"name": "Foreign store category", "item_type": "Physical"},
        ),
        (
            "post",
            "/sales",
            {
                "payment_method": "Cash",
                "items": [{"product_id": 1, "quantity": 1}],
                "client_transaction_id": "foreign-store-sale",
            },
        ),
        (
            "post",
            "/product/receivings",
            {
                "product_id": 1,
                "quantity": 1,
                "unit_cost": "10.00",
                "total_cost": "10.00",
                "client_transaction_id": "foreign-store-receiving",
            },
        ),
    ],
)
def test_store_scoped_create_endpoints_reject_another_stores_id(
    two_store_clients, method, path, payload
):
    _, attacker, owner_store_id, _ = two_store_clients
    request_payload = {"store_id": owner_store_id, **payload}

    response = getattr(attacker, method)(path, json=request_payload)

    if path == "/product/create":
        # Product creation currently maps the store-ownership validation failure
        # into the shared validation envelope instead of a 403 response.
        assert response.status_code == 400, response.get_json()
        assert response.json["success"] is False
        assert response.json["error"]["code"] == "VALIDATION_ERROR"
        assert (
            response.json["error"]["fields"]["store_id"]
            == "You do not have access to this store"
        )
        return

    assert response.status_code == 403, (
        f"{path} must reject a write targeting another account's store; "
        f"got {response.status_code}: {response.get_json()}"
    )
    assert response.json["success"] is False
    assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"


def test_store_settings_cannot_be_changed_by_another_store_owner(two_store_clients):
    _, attacker, owner_store_id, _ = two_store_clients

    response = attacker.patch(
        "/settings/store",
        json={"store_id": owner_store_id, "name": "Hijacked store"},
    )

    assert response.status_code == 403
    assert response.json["success"] is False
    assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"



def test_foreign_store_resources_cannot_be_read_by_id(two_store_clients):
    owner, attacker, owner_store_id, _ = two_store_clients

    product_response = owner.post(
        "/product/create",
        json={
            "store_id": owner_store_id,
            "name": "Private owner product",
            "price": 100,
            "opening_stock": 5,
            "low_stock_threshold": 1,
        },
    )
    assert product_response.status_code == 201
    product_id = product_response.json["data"]["product"]["id"]

    customer_response = owner.post(
        "/customers",
        json={
            "store_id": owner_store_id,
            "name": "Private owner customer",
            "contact": "08011111111",
        },
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["data"]["customer"]["id"]

    sale_response = owner.post(
        "/sales",
        json={
            "store_id": owner_store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "owner-private-sale",
        },
    )
    assert sale_response.status_code == 201
    sale_id = sale_response.json["data"]["receipt"]["sale_id"]

    foreign_reads = [
        attacker.get(f"/product/get?id={product_id}&store_id={owner_store_id}"),
        attacker.get(
            f"/customers/history?id={customer_id}&store_id={owner_store_id}"
        ),
        attacker.get(f"/sales/get?id={sale_id}&store_id={owner_store_id}"),
        attacker.get(
            f"/sales/payments?store_id={owner_store_id}&sale_id={sale_id}"
        ),
        attacker.get(
            f"/product/movements?store_id={owner_store_id}&product_id={product_id}"
        ),
    ]

    for response in foreign_reads:
        assert response.status_code == 403, (
            f"Cross-store resource lookup leaked or failed to reject access: "
            f"{response.status_code}: {response.get_json()}"
        )
        assert response.json["error"]["code"] == "STORE_ACCESS_DENIED"


def test_checkout_and_offline_sync_reject_foreign_store_id(two_store_clients):
    _, attacker, owner_store_id, _ = two_store_clients
    common = {
        "store_id": owner_store_id,
        "client_transaction_id": "foreign-store-transaction",
        "payment_method": "Cash",
        "items": [{"product_id": 1, "quantity": 1, "expected_unit_price": "10.00"}],
    }

    checkout = attacker.post("/sales/checkout", json=common)
    sync = attacker.post(
        "/sales/sync",
        json={**common, "client_transaction_id": "foreign-store-sync"},
    )

    assert checkout.status_code == 403
    assert checkout.json["error"]["code"] == "STORE_ACCESS_DENIED"
    assert sync.status_code == 403
    assert sync.json["error"]["code"] == "STORE_ACCESS_DENIED"
