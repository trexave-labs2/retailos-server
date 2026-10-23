from server.app.models.category import Category
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


def create_category(client, store_id, name, item_type):
    response = client.post(
        "/categories",
        json={"store_id": store_id, "name": name, "item_type": item_type},
    )
    assert response.status_code == 201
    return response.json["data"]["category"]["id"]


def create_item(client, store_id, name, item_type, category_id):
    return client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": name,
            "product_type": item_type,
            "category_id": category_id,
            "base_unit": "piece",
            "price": 100,
            "opening_stock": 0,
        },
    )


def test_categories_keep_physical_and_service_items_separate(client):
    store_id = register_and_login(client, "category-separation")
    beverages = create_category(client, store_id, "Beverages", "Physical")
    provisions = create_category(client, store_id, "Provisions", "Physical")
    repairs = create_category(client, store_id, "Repairs", "Service")

    drink = create_item(client, store_id, "Malt", "Physical", beverages)
    provision = create_item(client, store_id, "Rice", "Physical", provisions)
    repair = create_item(client, store_id, "Phone repair", "Service", repairs)

    assert drink.status_code == 201
    assert provision.status_code == 201
    assert repair.status_code == 201

    drink_id = drink.json["data"]["product"]["id"]
    provision_id = provision.json["data"]["product"]["id"]
    repair_id = repair.json["data"]["product"]["id"]

    for category_id, expected_id in (
        (beverages, drink_id),
        (provisions, provision_id),
        (repairs, repair_id),
    ):
        response = client.get(
            f"/product/list?store_id={store_id}&category_id={category_id}"
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json["data"]["products"]] == [expected_id]
        assert response.json["data"]["products"][0]["category_id"] == category_id


def test_category_names_are_case_insensitively_unique_per_store_and_type(client):
    store_id = register_and_login(client, "category-duplicates")
    create_category(client, store_id, "Beverages", "Physical")

    response = client.post(
        "/categories",
        json={"store_id": store_id, "name": "  beverages ", "item_type": "Physical"},
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "CATEGORY_ALREADY_EXISTS"


def test_category_assignment_rejects_wrong_type_and_other_store(client):
    first_store = register_and_login(client, "category-owner")
    category_id = create_category(client, first_store, "Repairs", "Service")
    wrong_type = create_item(client, first_store, "Soda", "Physical", category_id)
    assert wrong_type.status_code == 400
    assert wrong_type.json["error"]["code"] == "VALIDATION_ERROR"

    second_store = register_and_login(client, "category-other")
    response = create_item(client, second_store, "Soda", "Physical", category_id)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"

    own_category = create_category(client, second_store, "Beverages", "Physical")
    response = create_item(client, second_store, "Soda", "Physical", own_category)
    assert response.status_code == 201


def test_category_rename_preserves_assignments_and_delete_requires_empty_category(client):
    store_id = register_and_login(client, "category-lifecycle")
    category_id = create_category(client, store_id, "Beverages", "Physical")
    item = create_item(client, store_id, "Juice", "Physical", category_id)
    assert item.status_code == 201
    item_id = item.json["data"]["product"]["id"]

    rename = client.patch(
        "/categories",
        json={"store_id": store_id, "id": category_id, "name": "Cold Drinks"},
    )
    assert rename.status_code == 200
    assert rename.json["data"]["category"]["name"] == "Cold Drinks"

    listed = client.get(
        f"/product/list?store_id={store_id}&category_id={category_id}"
    )
    assert listed.status_code == 200
    assert [product["id"] for product in listed.json["data"]["products"]] == [item_id]

    blocked = client.delete(
        "/categories",
        json={"store_id": store_id, "id": category_id},
    )
    assert blocked.status_code == 409
    assert blocked.json["error"]["code"] == "CATEGORY_IN_USE"

    empty_category = create_category(client, store_id, "Unused", "Physical")
    deleted = client.delete(
        "/categories",
        json={"store_id": store_id, "id": empty_category},
    )
    assert deleted.status_code == 200
