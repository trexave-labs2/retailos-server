# server/tests/test_alert.py
from server.app.extensions import db
from server.app.models.alert import Alert


def register_and_login(client, username="alertuser"):
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


def create_product(client, store_id, stock_quantity=2, low_stock_threshold=5):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice 1kg",
            "price": 2500,
            "opening_stock": stock_quantity,
            "low_stock_threshold": low_stock_threshold,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["product"]["id"]


def adjust_stock(client, store_id, product_id, quantity_change):
    return client.post(
        "/product/adjust",
        json={
            "id": product_id,
            "store_id": store_id,
            "quantity_change": quantity_change,
            "reason": "Notification test stock adjustment",
        },
    )


def test_low_stock_alert_is_created_automatically(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    alert = Alert.query.filter_by(
        store_id=store_id, product_id=product_id, condition_active=True
    ).first()
    assert alert is not None
    assert alert.type.value == "low_stock"
    assert alert.is_resolved is False


def test_restock_resolves_low_stock_alert_automatically(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = adjust_stock(client, store_id, product_id, 5)
    assert response.status_code == 200

    alert = Alert.query.filter_by(store_id=store_id, product_id=product_id).first()
    assert alert is not None
    assert alert.is_resolved is True
    assert alert.condition_active is False


def test_stock_adjustment_creates_low_stock_alert_automatically(client):
    store_id = register_and_login(client)
    product_id = create_product(
        client, store_id, stock_quantity=10, low_stock_threshold=5
    )

    response = adjust_stock(client, store_id, product_id, -5)
    assert response.status_code == 200

    alert = Alert.query.filter_by(
        store_id=store_id, product_id=product_id, condition_active=True
    ).first()
    assert alert is not None


def test_repeated_low_stock_events_do_not_duplicate_same_condition(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = adjust_stock(client, store_id, product_id, -1)
    assert response.status_code == 200

    alerts = Alert.query.filter_by(store_id=store_id, product_id=product_id).all()
    assert len(alerts) == 1


def test_a_new_low_stock_episode_creates_a_new_alert(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    assert adjust_stock(client, store_id, product_id, 10).status_code == 200
    assert adjust_stock(client, store_id, product_id, -10).status_code == 200

    alerts = Alert.query.filter_by(store_id=store_id, product_id=product_id).order_by(Alert.id).all()
    assert len(alerts) == 2
    assert alerts[0].condition_active is False
    assert alerts[1].condition_active is True
    assert alerts[1].is_resolved is False


def test_list_alerts_returns_persisted_open_alerts(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.get(f"/alerts?store_id={store_id}")
    assert response.status_code == 200
    alerts = response.json["data"]["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["product_id"] == product_id
    assert alerts[0]["is_read"] is False


def test_alert_can_be_marked_read(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)
    alert = Alert.query.filter_by(store_id=store_id, product_id=product_id).first()

    response = client.patch(f"/alerts/read?store_id={store_id}&id={alert.id}")
    assert response.status_code == 200
    assert response.json["data"]["alert"]["is_read"] is True

    db.session.refresh(alert)
    assert alert.read_at is not None


def test_dismissed_alert_is_not_duplicated_until_condition_recovers(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)
    alert = Alert.query.filter_by(store_id=store_id, product_id=product_id).first()

    response = client.post(f"/alerts/dismiss?store_id={store_id}&id={alert.id}")
    assert response.status_code == 200
    assert response.json["data"]["alert"]["is_dismissed"] is True

    # Stock remains low, so further stock changes must not recreate the same alert.
    assert adjust_stock(client, store_id, product_id, -1).status_code == 200
    assert Alert.query.filter_by(store_id=store_id, product_id=product_id).count() == 1

    # Recovery ends that condition episode; a later low-stock episode may notify again.
    assert adjust_stock(client, store_id, product_id, 10).status_code == 200
    assert adjust_stock(client, store_id, product_id, -10).status_code == 200
    assert Alert.query.filter_by(store_id=store_id, product_id=product_id).count() == 2


def test_alerts_are_store_scoped(client, app):
    first_store_id = register_and_login(client, username="firstalertuser")
    product_id = create_product(client, first_store_id)
    alert = Alert.query.filter_by(store_id=first_store_id, product_id=product_id).first()

    second_client = app.test_client()
    second_store_id = register_and_login(second_client, username="secondalertuser")

    assert second_store_id != first_store_id
    assert second_client.get(f"/alerts?store_id={first_store_id}").status_code == 403
    assert second_client.patch(
        f"/alerts/read?store_id={second_store_id}&id={alert.id}"
    ).status_code == 404
    assert second_client.post(
        f"/alerts/dismiss?store_id={second_store_id}&id={alert.id}"
    ).status_code == 404


def test_missing_alert_returns_not_found(client):
    store_id = register_and_login(client)
    response = client.post(f"/alerts/dismiss?store_id={store_id}&id=99999")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "ALERT_NOT_FOUND"


def test_alerts_require_authentication(client):
    assert client.get("/alerts?store_id=1").status_code == 401
    assert client.patch("/alerts/read?store_id=1&id=1").status_code == 401
