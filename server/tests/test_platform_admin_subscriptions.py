from datetime import datetime, timedelta, timezone

from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.subscription_change import SubscriptionChange
from server.app.models.user import PLATFORM_ADMIN_ROLE, User


def register_and_login(client, email, name):
    response = client.post("/auth/register", json={
        "username": name,
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 201
    store_id = response.json["data"]["user"]["store_id"]
    response = client.post("/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 200
    return store_id


def promote(client, app, email):
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        db.session.commit()
    client.post("/auth/logout")
    response = client.post("/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 200


def test_admin_subscription_endpoints_require_platform_admin(client):
    assert client.get("/admin/subscriptions").status_code == 401
    assert client.get("/admin/subscription-requests").status_code == 401

    register_and_login(client, "owner@example.com", "Store Owner")
    assert client.get("/admin/subscriptions").status_code == 403
    assert client.get("/admin/subscription-requests").status_code == 403


def test_admin_can_list_subscription_entitlements_and_pending_requests(client, app):
    store_id = register_and_login(client, "admin@example.com", "Platform Admin")
    request_response = client.post("/subscriptions/change", json={
        "store_id": store_id,
        "plan": "pro",
        "billing_interval": "quarter",
    })
    assert request_response.status_code == 202
    change_id = request_response.json["data"]["change"]["id"]
    promote(client, app, "admin@example.com")

    subscriptions = client.get("/admin/subscriptions?state=free&page=1&per_page=1")
    assert subscriptions.status_code == 200
    subscription_data = subscriptions.json["data"]
    assert len(subscription_data["items"]) == 1
    assert subscription_data["pagination"]["total"] == 1
    item = subscription_data["items"][0]
    assert item["store"]["id"] == store_id
    assert item["subscription"]["state"] == "free"
    assert item["subscription"]["plan"] == "free"
    assert "password_hash" not in item["account"]

    requests = client.get("/admin/subscription-requests?status=pending")
    assert requests.status_code == 200
    request_item = requests.json["data"]["items"][0]
    assert request_item["id"] == change_id
    assert request_item["status"] == "pending"
    assert request_item["current_plan"]["id"] == "free"
    assert request_item["requested_plan"]["id"] == "pro"
    assert request_item["requested_plan"]["billing_interval"] == "quarter"
    assert request_item["requested_plan"]["price"] == {"amount": 13500, "currency": "NGN"}
    assert request_item["activation"] == "not_activated"
    assert "payment" not in request_item
    assert "payment_status" not in request_item
    assert "password_hash" not in request_item["account"]


def test_admin_subscription_state_filters_distinguish_trial_active_and_expired(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    with app.app_context():
        store = Store.query.one()
        now = datetime.now(timezone.utc)
        store.current_plan = "pro"
        store.subscription_status = "trial"
        store.current_period_end = now + timedelta(days=5)
        db.session.commit()

    trial = client.get("/admin/subscriptions?state=trial")
    assert trial.status_code == 200
    assert trial.json["data"]["pagination"]["total"] == 1
    assert trial.json["data"]["items"][0]["subscription"]["state"] == "trial"

    with app.app_context():
        store = Store.query.one()
        store.subscription_status = "active"
        store.current_period_end = datetime.now(timezone.utc) + timedelta(days=5)
        db.session.commit()

    active = client.get("/admin/subscriptions?state=active")
    assert active.status_code == 200
    assert active.json["data"]["pagination"]["total"] == 1
    assert active.json["data"]["items"][0]["subscription"]["state"] == "active"

    with app.app_context():
        store = Store.query.one()
        store.current_period_end = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()

    expired = client.get("/admin/subscriptions?state=expired")
    assert expired.status_code == 200
    assert expired.json["data"]["pagination"]["total"] == 1
    assert expired.json["data"]["items"][0]["subscription"]["state"] == "expired"


def test_admin_subscription_endpoints_validate_filters_and_pagination(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    assert client.get("/admin/subscriptions?state=unknown").status_code == 400
    assert client.get("/admin/subscriptions?sort_by=password_hash").status_code == 400
    assert client.get("/admin/subscription-requests?status=paid").status_code == 400
    assert client.get("/admin/subscription-requests?per_page=101").status_code == 400
