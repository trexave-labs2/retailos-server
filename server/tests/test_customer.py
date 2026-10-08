# server/tests/test_customer.py
def register_and_login(client, username="customeruser"):
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


def create_customer(client, store_id, name="John Customer"):
    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": name,
            "contact": "08012345678"
        }
    )

    assert response.status_code == 201

    return response.json["data"]["customer"]["id"]


# Check that a customer can be created.
def test_create_customer(client):
    store_id = register_and_login(client)

    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "John Customer",
            "contact": "08012345678"
        }
    )

    assert response.status_code == 201
    assert response.json["data"]["customer"]["name"] == "John Customer"
    assert response.json["data"]["customer"]["contact"] == "08012345678"


# Check that customers can be listed.
def test_list_customers(client):
    store_id = register_and_login(client)

    create_customer(client, store_id, "John Customer")
    create_customer(client, store_id, "Jane Customer")

    response = client.get(
        f"/customers?store_id={store_id}"
    )

    assert response.status_code == 200

    customers = response.json["data"]["customers"]

    assert len(customers) == 2
    assert customers[0]["name"] == "Jane Customer"
    assert customers[1]["name"] == "John Customer"


# Check that customer purchase history is returned.
def test_customer_history(client):
    store_id = register_and_login(client)
    customer_id = create_customer(client, store_id)

    product = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice 1kg",
            "price": 2500,
            "opening_stock": 10,
            "low_stock_threshold": 2
        }
    )

    assert product.status_code == 201

    product_id = product.json["data"]["product"]["id"]

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 2
                }
            ],
            "client_transaction_id": "customer-history-sale"
        }
    )

    assert sale.status_code == 201

    response = client.get(
        f"/customers/history?store_id={store_id}&id={customer_id}"
    )

    assert response.status_code == 200

    customer = response.json["data"]["customer"]

    assert customer["id"] == customer_id
    assert len(customer["purchase_history"]) == 1
    assert customer["purchase_history"][0]["total_amount"] == "5000.00"
    assert customer["purchase_history"][0]["items"][0]["quantity"] == 2


# Check that a customer name is required.
def test_create_customer_requires_name(client):
    store_id = register_and_login(client)

    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "contact": "08012345678"
        }
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"


# Check that another store cannot access this customer's history.
def test_customer_history_is_store_scoped(client):
    first_store_id = register_and_login(
        client,
        username="firstcustomeruser"
    )
    customer_id = create_customer(client, first_store_id)

    second_store_id = register_and_login(
        client,
        username="secondcustomeruser"
    )

    response = client.get(
        f"/customers/history?store_id={first_store_id}&id={customer_id}"
    )

    assert second_store_id != first_store_id
    assert response.status_code == 403


# Check that customers require authentication.
def test_customers_require_authentication(client):
    response = client.get("/customers?store_id=1")

    assert response.status_code == 401


# Check that customer search matches name and contact.
def test_search_customers(client):
    store_id = register_and_login(client, username="customer-search")
    create_customer(client, store_id, "John Customer")

    response = client.post(
        "/customers",
        json={
            "store_id": store_id,
            "name": "Mary Smith",
            "contact": "08055555555"
        }
    )
    assert response.status_code == 201

    response = client.get(
        f"/customers?store_id={store_id}&search=john"
    )
    assert response.status_code == 200
    assert [c["name"] for c in response.json["data"]["customers"]] == ["John Customer"]

    response = client.get(
        f"/customers?store_id={store_id}&search=0805"
    )
    assert response.status_code == 200
    assert [c["name"] for c in response.json["data"]["customers"]] == ["Mary Smith"]


# Check that a customer can be updated.
def test_update_customer(client):
    store_id = register_and_login(client, username="customer-update")
    customer_id = create_customer(client, store_id)

    response = client.patch(
        "/customers",
        json={
            "id": customer_id,
            "store_id": store_id,
            "name": "John Updated",
            "contact": "08111111111"
        }
    )

    assert response.status_code == 200
    customer = response.json["data"]["customer"]
    assert customer["name"] == "John Updated"
    assert customer["contact"] == "08111111111"


# Check that a customer can be deleted when no sales are linked.
def test_delete_customer_without_sales(client):
    store_id = register_and_login(client, username="customer-delete")
    customer_id = create_customer(client, store_id)

    response = client.delete(
        "/customers",
        json={"id": customer_id, "store_id": store_id}
    )

    assert response.status_code == 200
    assert response.json["data"]["customer_id"] == customer_id

    response = client.get(
        f"/customers/history?store_id={store_id}&id={customer_id}"
    )
    assert response.status_code == 404


# Deletion is blocked when a customer has sales, preserving purchase history.
def test_delete_customer_with_sales_is_blocked(client):
    store_id = register_and_login(client, username="customer-delete-sales")
    customer_id = create_customer(client, store_id)
    product_id = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice 1kg",
            "price": 2500,
            "opening_stock": 10,
            "low_stock_threshold": 2
        }
    ).json["data"]["product"]["id"]

    sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "customer_id": customer_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": 1}]
        }
    )
    assert sale.status_code == 201

    response = client.delete(
        "/customers",
        json={"id": customer_id, "store_id": store_id}
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "CUSTOMER_HAS_SALES"
    assert response.json["error"]["fields"]["sale_count"] == 1


# Update and delete must remain store-scoped.
def test_customer_update_and_delete_are_store_scoped(client):
    first_store_id = register_and_login(client, username="customer-crud-first")
    customer_id = create_customer(client, first_store_id)

    second_store_id = register_and_login(client, username="customer-crud-second")

    update = client.patch(
        "/customers",
        json={
            "id": customer_id,
            "store_id": first_store_id,
            "name": "Hacked Customer"
        }
    )
    delete = client.delete(
        "/customers",
        json={"id": customer_id, "store_id": first_store_id}
    )

    assert second_store_id != first_store_id
    assert update.status_code == 403
    assert delete.status_code == 403


# An update must contain at least one editable field.
def test_update_customer_requires_fields(client):
    store_id = register_and_login(client, username="customer-update-validation")
    customer_id = create_customer(client, store_id)

    response = client.patch(
        "/customers",
        json={"id": customer_id, "store_id": store_id}
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
