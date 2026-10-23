from datetime import datetime, timedelta, timezone

from server.app.extensions import db
from server.app.models.store import Store


def register_and_login(client):
    response = client.post(
        "/auth/register",
        json={
            "username": "subscriptionuser",
            "email": "subscriptionuser@example.com",
            "password": "test123",
        },
    )
    assert response.status_code == 201
    store_id = response.json["data"]["user"]["store_id"]
    response = client.post(
        "/auth/login",
        json={"email": "subscriptionuser@example.com", "password": "test123"},
    )
    assert response.status_code == 200
    return store_id


def test_plan_catalog_has_authoritative_prices_and_limits(client):
    response = client.get("/subscriptions/plans")
    assert response.status_code == 200
    plans = {plan["id"]: plan for plan in response.json["data"]["plans"]}
    assert plans["free"]["price"] == {
        "amount": 0, "currency": "NGN", "interval": "month"
    }
    assert plans["free"]["limits"] == {
        "products": 100, "customers": 100, "sales_per_month": 500,
        "staff_accounts": 0,
    }
    assert plans["pro"]["price"] == {
        "amount": 4999, "currency": "NGN", "interval": "month"
    }
    assert plans["pro"]["billing_options"] == [
        {"interval": "month", "amount": 4999, "currency": "NGN"},
        {"interval": "quarter", "amount": 13500, "currency": "NGN"},
        {"interval": "year", "amount": 49990, "currency": "NGN"},
    ]
    assert plans["pro"]["limits"] == {
        "products": None, "customers": None, "sales_per_month": None,
        "staff_accounts": 5,
    }
    assert plans["free"]["limits"]["staff_accounts"] == 0
    assert plans["pro"]["features"]["data_export"] is True
    assert plans["pro"]["reporting"]["profit_per_product"] is True
    assert plans["free"]["reporting"]["profit_per_product"] is False
    assert plans["free"]["trial"] == {
        "pro_trial_available_once": True, "duration_days": 14
    }


def test_subscription_state_requires_authenticated_store_access(client):
    response = client.get("/subscriptions?store_id=1")
    assert response.status_code == 401

    store_id = register_and_login(client)
    response = client.get(f"/subscriptions?store_id={store_id}")
    assert response.status_code == 200
    subscription = response.json["data"]["subscription"]
    assert subscription["plan"]["id"] == "free"
    assert subscription["subscription_status"] == "active"


def test_upgrade_request_stays_pending_and_does_not_grant_pro(client):
    store_id = register_and_login(client)
    response = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro"},
    )
    assert response.status_code == 202
    change = response.json["data"]["change"]
    assert change["target_plan"] == "pro"
    assert change["status"] == "pending"
    assert change["activation"] == "not_activated"
    assert change["price"]["amount"] == 4999
    assert response.json["data"]["billing"]["status"] == "not_configured"

    state_response = client.get(f"/subscriptions?store_id={store_id}")
    assert state_response.status_code == 200
    subscription = state_response.json["data"]["subscription"]
    assert subscription["plan"]["id"] == "free"
    assert subscription["pending_change"]["status"] == "pending"


def test_pending_change_can_be_cancelled(client):
    store_id = register_and_login(client)
    response = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro"},
    )
    change_id = response.json["data"]["change"]["id"]

    response = client.post(
        "/subscriptions/change/cancel",
        json={"store_id": store_id, "change_id": change_id},
    )
    assert response.status_code == 200
    assert response.json["data"]["status"] == "cancelled"
    assert Store.query.get(store_id).current_plan == "free"


def test_unknown_plan_is_rejected(client):
    store_id = register_and_login(client)
    response = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "enterprise"},
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_PLAN"



def test_free_plan_product_limit_is_enforced_by_server(client, monkeypatch):
    from server.app.services.subscription_service import PLAN_CATALOG

    store_id = register_and_login(client)
    monkeypatch.setitem(PLAN_CATALOG["free"]["limits"], "products", 0)
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Limit test product",
            "price": 100,
            "opening_stock": 1,
        },
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "PLAN_LIMIT_REACHED"


def test_free_plan_customer_limit_is_enforced_by_server(client, monkeypatch):
    from server.app.services.subscription_service import PLAN_CATALOG

    store_id = register_and_login(client)
    monkeypatch.setitem(PLAN_CATALOG["free"]["limits"], "customers", 0)
    response = client.post(
        "/customers",
        json={"store_id": store_id, "name": "Limit test customer"},
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "PLAN_LIMIT_REACHED"



def test_store_can_start_only_one_fourteen_day_pro_trial(client):
    store_id = register_and_login(client)
    response = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert response.status_code == 200
    assert response.json["data"]["trial"]["duration_days"] == 14
    assert response.json["data"]["subscription"]["plan"]["id"] == "pro"
    assert response.json["data"]["subscription"]["subscription_status"] == "trial"

    response = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRIAL_ALREADY_USED"


def test_quarterly_plan_change_uses_catalog_price_snapshot(client):
    store_id = register_and_login(client)
    response = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro", "billing_interval": "quarter"},
    )
    assert response.status_code == 202
    change = response.json["data"]["change"]
    assert change["billing_interval"] == "quarter"
    assert change["price"] == {
        "amount": 13500, "currency": "NGN", "interval": "quarter"
    }


def test_unsupported_billing_interval_is_rejected(client):
    store_id = register_and_login(client)
    response = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro", "billing_interval": "weekly"},
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_BILLING_INTERVAL"



def test_free_plan_monthly_sales_limit_is_enforced_after_grace_period(client, monkeypatch):
    from server.app.services.subscription_service import PLAN_CATALOG
    from server.app.models.store import Store

    store_id = register_and_login(client)
    product = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Monthly limit product",
            "price": 100,
            "opening_stock": 5,
        },
    )
    assert product.status_code == 201
    product_id = product.json["data"]["product"]["id"]

    monkeypatch.setitem(PLAN_CATALOG["free"]["limits"], "sales_per_month", 0)
    store = db.session.get(Store, store_id)
    store.sales_limit_grace_started_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.session.commit()

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}],
            "client_transaction_id": "monthly-sales-limit",
        },
    )

    assert response.status_code == 403
    assert response.json["error"]["code"] == "PLAN_LIMIT_REACHED"
    assert response.json["error"]["fields"]["resource"] == "sales_per_month"


def test_free_plan_cannot_access_advanced_reporting(client):
    store_id = register_and_login(client)

    rankings = client.get(f"/dashboard/rankings?store_id={store_id}")
    time_series = client.get(f"/dashboard/time-series?store_id={store_id}")

    for response in (rankings, time_series):
        assert response.status_code == 403
        assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
        assert response.json["error"]["fields"]["feature"] == "advanced_reporting"


def test_pro_trial_can_access_advanced_reporting(client):
    store_id = register_and_login(client)
    trial = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert trial.status_code == 200

    rankings = client.get(f"/dashboard/rankings?store_id={store_id}")
    time_series = client.get(f"/dashboard/time-series?store_id={store_id}")

    assert rankings.status_code == 200
    assert time_series.status_code == 200
