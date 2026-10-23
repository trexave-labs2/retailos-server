from server.app.models.inventory_movement import InventoryMovement
from server.app.models.product import Product
from server.app.models.stock_receiving import StockReceiving
from server.app.models.sale_item import SaleItem


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


def create_product(client, store_id, username):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": f"Rice {username}",
            "price": 500,
            "opening_stock": 0,
            "base_unit": "piece",
            "units": [
                {"name": "piece", "base_quantity": 1, "price": 500},
                {"name": "bag", "base_quantity": 10, "price": 4800},
            ],
        },
    )
    assert response.status_code == 201
    return response.json["data"]["product"]


def test_receiving_records_cost_and_updates_converted_stock(client):
    store_id = register_and_login(client, "receiving-happy-path")
    product = create_product(client, store_id, "receiving-happy-path")
    bag = next(unit for unit in product["units"] if unit["name"] == "bag")

    response = client.post(
        "/product/receivings",
        json={
            "store_id": store_id,
            "product_id": product["id"],
            "unit_id": bag["id"],
            "quantity": 10,
            "unit_cost": "5000.00",
            "total_cost": "50000.00",
            "supplier_or_source": "Market supplier",
            "client_transaction_id": "receive-rice-001",
            "receiving_date": "2026-10-20T09:30:00+01:00",
        },
    )

    assert response.status_code == 201
    data = response.json["data"]
    receiving = data["receiving"]
    assert receiving["quantity"] == 10
    assert receiving["unit"] == "bag"
    assert receiving["unit_base_quantity"] == 10
    assert receiving["base_quantity_received"] == 100
    assert receiving["unit_cost"] == "5000.00"
    assert receiving["total_cost"] == "50000.00"
    assert receiving["supplier_or_source"] == "Market supplier"
    assert data["product_stock_quantity"] == 100

    stored_product = Product.query.filter_by(id=product["id"]).one()
    assert stored_product.stock_quantity == 100
    assert str(stored_product.price) == "500.00"
    assert StockReceiving.query.count() == 1
    movement = InventoryMovement.query.filter_by(
        product_id=product["id"], movement_type="RECEIVING"
    ).one()
    assert movement.quantity_change == 100
    assert movement.previous_quantity == 0
    assert movement.new_quantity == 100

    history = client.get(f"/product/receivings?store_id={store_id}&product_id={product['id']}")
    assert history.status_code == 200
    assert len(history.json["data"]["receivings"]) == 1

    deletion = client.delete(
        "/product/delete",
        json={"store_id": store_id, "id": product["id"]},
    )
    assert deletion.status_code == 409
    assert StockReceiving.query.count() == 1


def test_receiving_retry_does_not_increase_stock_twice(client):
    store_id = register_and_login(client, "receiving-idempotency")
    product = create_product(client, store_id, "receiving-idempotency")
    bag = next(unit for unit in product["units"] if unit["name"] == "bag")
    payload = {
        "store_id": store_id,
        "product_id": product["id"],
        "unit_id": bag["id"],
        "quantity": 10,
        "unit_cost": "5000.00",
        "client_transaction_id": "receive-rice-retry",
    }

    first = client.post("/product/receivings", json=payload)
    retry = client.post("/product/receivings", json=payload)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json["data"]["duplicate"] is True
    assert Product.query.get(product["id"]).stock_quantity == 100
    assert StockReceiving.query.count() == 1
    assert InventoryMovement.query.filter_by(
        product_id=product["id"], movement_type="RECEIVING"
    ).count() == 1

    conflict = client.post(
        "/product/receivings",
        json={**payload, "quantity": 11},
    )
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert Product.query.get(product["id"]).stock_quantity == 100


def test_receiving_rejects_invalid_quantity_cost_and_total(client):
    store_id = register_and_login(client, "receiving-validation")
    product = create_product(client, store_id, "receiving-validation")
    bag = next(unit for unit in product["units"] if unit["name"] == "bag")
    base = {
        "store_id": store_id,
        "product_id": product["id"],
        "unit_id": bag["id"],
        "quantity": 10,
        "unit_cost": "5000.00",
        "client_transaction_id": "receive-invalid-001",
    }

    invalid_quantity = client.post(
        "/product/receivings", json={**base, "quantity": 0}
    )
    invalid_cost = client.post(
        "/product/receivings",
        json={**base, "client_transaction_id": "receive-invalid-002", "unit_cost": "-1"},
    )
    invalid_total = client.post(
        "/product/receivings",
        json={**base, "client_transaction_id": "receive-invalid-003", "total_cost": "49999.99"},
    )

    assert invalid_quantity.status_code == 400
    assert invalid_cost.status_code == 400
    assert invalid_total.status_code == 400
    assert Product.query.get(product["id"]).stock_quantity == 0
    assert StockReceiving.query.count() == 0
    assert InventoryMovement.query.filter_by(
        product_id=product["id"], movement_type="RECEIVING"
    ).count() == 0



def test_custom_unit_conversion_is_snapshotted_across_receiving_and_sale(client):
    store_id = register_and_login(client, "custom-unit-stock-sale")
    created = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Custom Feed",
            "price": 1200,
            "opening_stock": 0,
            "base_unit": "kg",
            "units": [
                {"name": "kg", "base_quantity": 1, "price": 1200},
                {"name": "feed sack", "base_quantity": 25, "price": 28000},
            ],
        },
    )
    assert created.status_code == 201
    product = created.json["data"]["product"]
    sack = next(unit for unit in product["units"] if unit["name"] == "feed sack")

    received = client.post(
        "/product/receivings",
        json={
            "store_id": store_id,
            "product_id": product["id"],
            "unit_id": sack["id"],
            "quantity": 2,
            "unit_cost": "28000.00",
            "client_transaction_id": "custom-feed-receive-001",
        },
    )
    assert received.status_code == 201
    receiving = received.json["data"]["receiving"]
    assert receiving["unit"] == "feed sack"
    assert receiving["unit_base_quantity"] == 25
    assert receiving["base_quantity_received"] == 50
    assert received.json["data"]["product_stock_quantity"] == 50

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product["id"], "unit_id": sack["id"], "quantity": 1}],
            "client_transaction_id": "custom-feed-sale-001",
        },
    )
    assert sale.status_code == 201
    saved_line = SaleItem.query.filter_by(product_id=product["id"]).one()
    assert saved_line.unit_name_at_sale == "feed sack"
    assert saved_line.unit_base_quantity == 25
    assert saved_line.base_quantity_deducted == 25
    assert Product.query.get(product["id"]).stock_quantity == 25

    history = client.get(
        f"/product/receivings?store_id={store_id}&product_id={product['id']}"
    )
    assert history.status_code == 200
    saved_receiving = history.json["data"]["receivings"][0]
    assert saved_receiving["unit"] == "feed sack"
    assert saved_receiving["unit_base_quantity"] == 25
    assert saved_receiving["base_quantity_received"] == 50
