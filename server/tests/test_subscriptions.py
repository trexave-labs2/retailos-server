from datetime import datetime, timedelta, timezone

from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.subscription_change import SubscriptionChange


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


def test_plan_change_ignores_client_payment_and_entitlement_claims(client):
    store_id = register_and_login(client)
    store = db.session.get(Store, store_id)
    original_status = store.subscription_status

    response = client.post(
        "/subscriptions/change",
        json={
            "store_id": store_id,
            "plan": "pro",
            "billing_interval": "month",
            "payment_status": "paid",
            "payment_confirmed": True,
            "is_paid": True,
            "current_plan": "pro",
            "subscription_status": "active",
            "price_amount": 1,
        },
    )

    assert response.status_code == 202
    assert response.json["data"]["change"]["status"] == "pending"
    assert response.json["data"]["change"]["activation"] == "not_activated"
    assert response.json["data"]["billing"]["status"] == "not_configured"
    assert response.json["data"]["current_subscription"] == {
        "plan": "free",
        "status": original_status,
    }
    db.session.refresh(store)
    assert store.current_plan == "free"
    assert store.subscription_status == original_status


def test_repeated_identical_plan_change_returns_same_pending_request(client):
    store_id = register_and_login(client)
    payload = {
        "store_id": store_id,
        "plan": "pro",
        "billing_interval": "quarter",
    }

    first = client.post("/subscriptions/change", json=payload)
    second = client.post("/subscriptions/change", json=payload)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json["message"] == "PLAN_CHANGE_ALREADY_PENDING"
    assert second.json["data"]["change"]["id"] == first.json["data"]["change"]["id"]
    assert second.json["data"]["change"]["price"]["amount"] == 13500
    assert second.json["data"]["change"]["activation"] == "not_activated"
    assert second.json["data"]["current_subscription"]["plan"] == "free"
    assert SubscriptionChange.query.filter_by(
        store_id=store_id, status="pending"
    ).count() == 1


def test_pending_plan_change_rejects_a_different_billing_interval(client):
    store_id = register_and_login(client)
    first = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro", "billing_interval": "month"},
    )
    second = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro", "billing_interval": "year"},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json["error"]["code"] == "PLAN_CHANGE_PENDING"
    assert second.json["error"]["fields"]["pending_billing_interval"] == "month"
    assert SubscriptionChange.query.filter_by(
        store_id=store_id, status="pending"
    ).count() == 1


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


def test_pending_pro_change_does_not_unlock_advanced_reporting_or_export(client):
    store_id = register_and_login(client)
    change = client.post(
        "/subscriptions/change",
        json={"store_id": store_id, "plan": "pro"},
    )
    assert change.status_code == 202

    rankings = client.get(f"/dashboard/rankings?store_id={store_id}")
    export = client.get(f"/settings/export?store_id={store_id}")

    for response, feature in (
        (rankings, "advanced_reporting"),
        (export, "data_export"),
    ):
        assert response.status_code == 403
        assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
        assert response.json["error"]["fields"]["feature"] == feature


def test_expired_pro_trial_loses_advanced_reporting(client):
    store_id = register_and_login(client)
    trial = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert trial.status_code == 200

    store = db.session.get(Store, store_id)
    store.current_period_end = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.session.commit()

    response = client.get(f"/dashboard/rankings?store_id={store_id}")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert response.json["error"]["fields"]["feature"] == "advanced_reporting"

    db.session.refresh(store)
    assert store.current_plan == "free"
    assert store.subscription_status == "expired"


def test_active_paid_pro_state_grants_advanced_reporting(client):
    store_id = register_and_login(client)
    store = db.session.get(Store, store_id)
    store.current_plan = "pro"
    store.subscription_status = "active"
    store.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
    db.session.commit()

    response = client.get(f"/dashboard/rankings?store_id={store_id}")
    assert response.status_code == 200


def test_stale_pro_plan_with_pending_status_does_not_grant_entitlements(client):
    from datetime import datetime, timedelta, timezone

    store_id = register_and_login(client)
    store = db.session.get(Store, store_id)
    store.current_plan = "pro"
    store.subscription_status = "pending"
    store.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
    db.session.commit()

    response = client.get(f"/dashboard/rankings?store_id={store_id}")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert response.json["error"]["fields"]["feature"] == "advanced_reporting"
