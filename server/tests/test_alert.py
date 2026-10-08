# server/tests/test_alert.py
from server.app.extensions import db
from server.app.models.alert import Alert


def register_and_login(client, username="alertuser", **kwargs):
    username = kwargs.get("username", username)
    email = f"{username}@example.com"

    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "test123"
        }
    )

    assert response.status_code == 201

    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "test123"
        }
    )

    assert response.status_code == 200

    return response.json["data"]["user"]["store_id"]


def create_product(
    client,
    store_id,
    stock_quantity=2,
    low_stock_threshold=5
):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice 1kg",
            "price": 2500,
            "opening_stock": stock_quantity,
            "low_stock_threshold": low_stock_threshold
        }
    )

    assert response.status_code == 201

    return response.json["data"]["product"]["id"]


# Check that low-stock alerts are generated.
def test_generate_low_stock_alert(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        f"/alerts/generate-low-stock?store_id={store_id}"
    )

    assert response.status_code == 200
    assert response.json["data"]["created_alerts"] == [product_id]

    alert = Alert.query.filter_by(
        store_id=store_id,
        product_id=product_id
    ).first()

    assert alert is not None
    assert alert.type.value == "low_stock"
    assert alert.is_resolved is False


# Check that the same open low-stock alert is not duplicated.
def test_low_stock_alert_is_not_duplicated(client):
    store_id = register_and_login(client)
    create_product(client, store_id)

    first = client.post(
        f"/alerts/generate-low-stock?store_id={store_id}"
    )
    second = client.post(
        f"/alerts/generate-low-stock?store_id={store_id}"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(second.json["data"]["created_alerts"]) == 0
    assert Alert.query.count() == 1


# Check that open alerts can be listed.
def test_list_alerts_returns_open_alerts(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        f"/alerts/generate-low-stock?store_id={store_id}"
    )

    assert response.status_code == 200

    response = client.get(
        f"/alerts?store_id={store_id}"
    )

    assert response.status_code == 200

    alerts = response.json["data"]["alerts"]

    assert len(alerts) == 1
    assert alerts[0]["product_id"] == product_id


# Check that an alert can be resolved.
def test_resolve_alert(client):
    store_id = register_and_login(client)
    product_id = create_product(client, store_id)

    response = client.post(
        f"/alerts/generate-low-stock?store_id={store_id}"
    )

    assert response.status_code == 200

    alert = Alert.query.filter_by(
        store_id=store_id,
        product_id=product_id
    ).first()

    response = client.post(
        f"/alerts/resolve?store_id={store_id}&id={alert.id}"
    )

    assert response.status_code == 200

    db.session.refresh(alert)

    assert alert.is_resolved is True


# Check that another store cannot access this store's alerts.
def test_alerts_are_store_scoped(client):
    first_store_id = register_and_login(
        client,
        username="firstalertuser"
    )
    create_product(client, first_store_id)

    second_store_id = register_and_login(
        client,
        username="secondalertuser"
    )

    response = client.get(
        f"/alerts?store_id={first_store_id}"
    )

    assert second_store_id != first_store_id
    assert response.status_code == 403


# Check that a missing alert returns not found.
def test_missing_alert_returns_not_found(client):
    store_id = register_and_login(client)

    response = client.post(
        f"/alerts/resolve?store_id={store_id}&id=99999"
    )

    assert response.status_code == 404
    assert response.json["error"]["code"] == "ALERT_NOT_FOUND"


# Check that alerts require authentication.
def test_alerts_require_authentication(client):
    response = client.get("/alerts?store_id=1")

    assert response.status_code == 401
