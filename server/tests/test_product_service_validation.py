from server.app.extensions import db
from server.app.models.alert import Alert, AlertType
from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product


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


def create_service(client, store_id, **overrides):
    payload = {
        "store_id": store_id,
        "name": "Haircut",
        "product_type": "Service",
        "price": 1500,
    }
    payload.update(overrides)
    return client.post("/product/create", json=payload)


def test_service_sale_does_not_change_inventory_or_create_stock_alerts(client):
    store_id = register_and_login(client, "service-inventory-user")
    created = create_service(client, store_id)
    assert created.status_code == 201
    service_id = created.json["data"]["product"]["id"]
    assert created.json["data"]["product"]["stock_quantity"] == 0
    assert created.json["data"]["product"]["low_stock_threshold"] is None

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": service_id, "quantity": 2}],
        },
    )
    assert sale.status_code == 201
    assert sale.json["data"]["receipt"]["total_amount"] == "3000.00"
    assert db.session.get(Product, service_id).stock_quantity == 0
    assert InventoryMovement.query.filter_by(product_id=service_id).count() == 0

    generated = client.post(f"/alerts/generate-low-stock?store_id={store_id}")
    assert generated.status_code == 200
    assert generated.json["data"]["created_alerts"] == []
    assert Alert.query.filter_by(product_id=service_id, is_resolved=False).count() == 0


def test_service_rejects_inventory_configuration_on_creation(client):
    store_id = register_and_login(client, "service-config-user")
    response = create_service(
        client,
        store_id,
        opening_stock=5,
        low_stock_threshold=2,
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
    assert Product.query.filter_by(store_id=store_id).count() == 0


def test_service_legacy_stock_values_do_not_create_alerts_or_decrement_on_sale(client):
    store_id = register_and_login(client, "legacy-service-user")
    created = create_service(client, store_id)
    assert created.status_code == 201
    service_id = created.json["data"]["product"]["id"]

    # Simulate a legacy row. Keep the stored value and its history intact for review.
    service = db.session.get(Product, service_id)
    service.stock_quantity = 7
    service.low_stock_threshold = 3
    db.session.add(Alert(
        store_id=store_id,
        product_id=service_id,
        type=AlertType.LOW_STOCK,
        message="Legacy service stock alert",
    ))
    db.session.commit()

    listed = client.get(f"/alerts?store_id={store_id}")
    assert listed.status_code == 200
    assert all(alert["product_id"] != service_id for alert in listed.json["data"]["alerts"])

    generated = client.post(f"/alerts/generate-low-stock?store_id={store_id}")
    assert generated.status_code == 200
    assert generated.json["data"]["created_alerts"] == []
    legacy_alert = Alert.query.filter_by(product_id=service_id).one()
    assert legacy_alert.is_resolved is True

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": service_id, "quantity": 1}],
        },
    )
    assert sale.status_code == 201
    assert db.session.get(Product, service_id).stock_quantity == 7
    assert InventoryMovement.query.filter_by(product_id=service_id).count() == 0


def test_product_type_conversion_requires_safe_inventory_state(client):
    store_id = register_and_login(client, "product-type-transition-user")
    physical = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "product_type": "Physical",
            "price": 500,
            "opening_stock": 4,
            "low_stock_threshold": 1,
        },
    )
    assert physical.status_code == 201
    product_id = physical.json["data"]["product"]["id"]

    rejected = client.patch(
        "/product/update",
        json={"store_id": store_id, "id": product_id, "product_type": "Service"},
    )
    assert rejected.status_code == 400
    assert db.session.get(Product, product_id).product_type == "Physical"

    service = create_service(client, store_id, name="Consultation")
    assert service.status_code == 201
    service_id = service.json["data"]["product"]["id"]

    rejected_conversion = client.patch(
        "/product/update",
        json={"store_id": store_id, "id": service_id, "product_type": "Physical"},
    )
    assert rejected_conversion.status_code == 400

    converted = client.patch(
        "/product/update",
        json={
            "store_id": store_id,
            "id": service_id,
            "product_type": "Physical",
            "stock_quantity": 0,
            "base_unit": "session",
        },
    )
    assert converted.status_code == 200
    product = db.session.get(Product, service_id)
    assert product.product_type == "Physical"
    assert product.stock_quantity == 0
    assert product.base_unit == "session"


def test_service_cannot_be_manually_stock_adjusted(client):
    store_id = register_and_login(client, "service-stock-adjust-user")
    created = create_service(client, store_id)
    service_id = created.json["data"]["product"]["id"]

    response = client.post(
        "/product/adjust",
        json={
            "store_id": store_id,
            "id": service_id,
            "quantity_change": 5,
            "reason": "Should not apply to a service",
        },
    )
    assert response.status_code == 400
    assert InventoryMovement.query.filter_by(product_id=service_id).count() == 0
