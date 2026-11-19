from datetime import datetime, timedelta, timezone

from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.user import PLATFORM_ADMIN_ROLE, User


def register_and_login(client, email, name):
    response = client.post("/auth/register", json={
        "username": name,
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 201
    response = client.post("/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 200


def promote(client, app, email):
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.platform_role = PLATFORM_ADMIN_ROLE
        db.session.commit()
    response = client.post("/auth/logout")
    assert response.status_code == 200
    response = client.post("/auth/login", json={
        "email": email,
        "password": "test-password-123",
    })
    assert response.status_code == 200


ADMIN_ENDPOINTS = (
    "/admin/overview",
    "/admin/users",
    "/admin/users/1",
    "/admin/stores",
    "/admin/stores/1",
)


def test_admin_read_endpoints_require_platform_admin(client):
    for endpoint in ADMIN_ENDPOINTS:
        response = client.get(endpoint)
        assert response.status_code == 401, endpoint

    register_and_login(client, "owner@example.com", "Store Owner")
    for endpoint in ADMIN_ENDPOINTS:
        response = client.get(endpoint)
        assert response.status_code == 403, endpoint


def test_admin_can_read_overview_users_and_stores_without_sensitive_fields(
    client, app
):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    overview = client.get("/admin/overview")
    assert overview.status_code == 200
    metrics = overview.json["data"]["metrics"]
    assert metrics["users_total"] == 1
    assert metrics["stores_total"] == 1
    assert "active_premium_stores" in metrics

    users = client.get("/admin/users")
    assert users.status_code == 200
    user = users.json["data"]["items"][0]
    assert user["email"] == "admin@example.com"
    assert "password" not in user
    assert "password_hash" not in user
    assert "session_version" not in user
    assert "stores" in user and user["stores_count"] == 1

    stores = client.get("/admin/stores")
    assert stores.status_code == 200
    store = stores.json["data"]["items"][0]
    assert store["owner"]["email"] == "admin@example.com"
    assert "password_hash" not in store["owner"]


def test_admin_user_and_store_detail_include_supported_activity(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    user_id = client.get("/admin/users").json["data"]["items"][0]["id"]
    store_id = client.get("/admin/stores").json["data"]["items"][0]["id"]

    user_detail = client.get(f"/admin/users/{user_id}")
    assert user_detail.status_code == 200
    assert user_detail.json["data"]["user"]["stores_count"] == 1
    assert "activity" in user_detail.json["data"]["user"]["stores"][0]

    store_detail = client.get(f"/admin/stores/{store_id}")
    assert store_detail.status_code == 200
    assert store_detail.json["data"]["store"]["owner"]["email"] == "admin@example.com"
    assert "activity" in store_detail.json["data"]["store"]


def test_admin_user_search_filter_and_pagination(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    # Create additional platform users through the public registration flow.
    for index in range(3):
        client.post("/auth/logout")
        register_and_login(client, f"user{index}@example.com", f"User {index}")
        client.post("/auth/logout")
        client.post("/auth/login", json={
            "email": "admin@example.com",
            "password": "test-password-123",
        })

    first_page = client.get("/admin/users?page=1&per_page=2&sort_by=name&sort_order=asc")
    assert first_page.status_code == 200
    data = first_page.json["data"]
    assert len(data["items"]) == 2
    assert data["pagination"]["total"] == 4
    assert data["pagination"]["has_next"] is True

    search = client.get("/admin/users?search=user1%40example.com")
    assert search.status_code == 200
    assert [item["email"] for item in search.json["data"]["items"]] == ["user1@example.com"]

    invalid = client.get("/admin/users?page=0")
    assert invalid.status_code == 400


def test_expired_pro_trial_is_not_counted_as_active_premium(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    with app.app_context():
        store = Store.query.one()
        store.current_plan = "pro"
        store.subscription_status = "trial"
        store.current_period_end = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()

    response = client.get("/admin/overview")
    assert response.status_code == 200
    metrics = response.json["data"]["metrics"]
    assert metrics["active_premium_stores"] == 0
    assert metrics["active_trial_stores"] == 0


def test_admin_detail_returns_not_found_for_missing_records(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    assert client.get("/admin/users/99999").status_code == 404
    assert client.get("/admin/stores/99999").status_code == 404


def test_admin_list_rejects_invalid_filters_and_sorting(client, app):
    register_and_login(client, "admin@example.com", "Platform Admin")
    promote(client, app, "admin@example.com")

    assert client.get("/admin/users?has_store=maybe").status_code == 400
    assert client.get("/admin/users?sort_by=password_hash").status_code == 400
    assert client.get("/admin/stores?subscription_status=unknown").status_code == 400
    assert client.get("/admin/stores?per_page=101").status_code == 400
