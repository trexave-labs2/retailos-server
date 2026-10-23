from server.app.models.product import Product


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


def create_product(client, store_id, name):
    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "price": 100,
            "opening_stock": 5,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["product"]["id"]


def test_store_authorization_rejects_boolean_and_invalid_ids(client):
    store_id = register_and_login(client, "tenant_validation_user")

    for invalid_store_id in (True, False, 0, -1, "1", None):
        response = client.post(
            "/product/create",
            json={
                "store_id": invalid_store_id,
                "name": "Invalid Store Product",
                "price": 100,
                "opening_stock": 1,
            },
        )
        assert response.status_code == 400

    response = client.get(f"/product/list?store_id={store_id}")
    assert response.status_code == 200


def test_users_cannot_access_another_stores_products_or_sales(app, client):
    store_a = register_and_login(client, "tenant_owner_a")
    product_a = create_product(client, store_a, "Owner A Product")

    client_b = app.test_client()
    store_b = register_and_login(client_b, "tenant_owner_b")
    product_b = create_product(client_b, store_b, "Owner B Product")

    # Knowing a sequential product ID does not grant access to that record.
    response = client.get(f"/product/get?id={product_b}&store_id={store_a}")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "PRODUCT_NOT_FOUND"

    response = client.patch(
        "/product/update",
        json={"store_id": store_a, "id": product_b, "name": "Stolen Product"},
    )
    assert response.status_code == 404

    response = client.post(
        "/product/adjust",
        json={"store_id": store_a, "id": product_b, "quantity_change": 1},
    )
    assert response.status_code == 404

    response = client.get(
        f"/product/movements?store_id={store_a}&product_id={product_b}"
    )
    assert response.status_code == 404

    response = client.post(
        "/sales",
        json={
            "store_id": store_a,
            "payment_method": "Cash",
            "items": [{"product_id": product_b, "quantity": 1}],
            "client_transaction_id": "cross-tenant-product-attempt",
        },
    )
    assert response.status_code == 404
    assert response.json["error"]["code"] == "PRODUCT_NOT_FOUND"

    # Verify the failed cross-tenant attempts did not mutate either product.
    with app.app_context():
        product_a_record = Product.query.filter_by(id=product_a, store_id=store_a).first()
        product_b_record = Product.query.filter_by(id=product_b, store_id=store_b).first()
        assert product_a_record is not None
        assert product_b_record is not None
        assert product_b_record.name == "Owner B Product"
        assert product_b_record.stock_quantity == 5


def test_users_cannot_use_another_stores_customer_or_store_id(app, client):
    store_a = register_and_login(client, "tenant_customer_a")

    client_b = app.test_client()
    store_b = register_and_login(client_b, "tenant_customer_b")
    created = client_b.post(
        "/customers",
        json={"store_id": store_b, "name": "Private Customer"},
    )
    assert created.status_code == 201
    customer_b = created.json["data"]["customer"]["id"]

    response = client.get(f"/customers/history?id={customer_b}&store_id={store_a}")
    assert response.status_code == 404

    response = client.get(f"/customers?store_id={store_b}")
    assert response.status_code == 403

    response = client.get(f"/dashboard?store_id={store_b}")
    assert response.status_code == 403

    response = client.post(
        "/customers",
        json={"store_id": store_b, "name": "Unauthorized Customer"},
    )
    assert response.status_code == 403
