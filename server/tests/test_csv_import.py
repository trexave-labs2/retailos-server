import io

from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit


def register_and_login(client, username="csvuser"):
    email = f"{username}@example.com"
    registered = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "test123"},
    )
    assert registered.status_code == 201
    logged_in = client.post(
        "/auth/login",
        json={"email": email, "password": "test123"},
    )
    assert logged_in.status_code == 200
    return logged_in.json["data"]["user"]["store_id"]


def upload_csv(client, store_id, text, filename="catalog.csv"):
    return client.post(
        "/product/import",
        data={
            "store_id": str(store_id),
            "file": (io.BytesIO(text.encode("utf-8")), filename),
        },
        content_type="multipart/form-data",
    )


def test_csv_import_creates_physical_products_and_services(client):
    store_id = register_and_login(client)
    response = upload_csv(
        client,
        store_id,
        "name,price,product_type,base_unit,opening_stock,low_stock_threshold\n"
        "Rice,2500,Physical,kg,12,3\n"
        "Consultation,5000,Service,session,,\n",
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["created_count"] == 2
    assert data["failed_count"] == 0
    assert [item["product_type"] for item in data["created"]] == ["Physical", "Service"]

    physical = Product.query.filter_by(store_id=store_id, name="Rice").one()
    service = Product.query.filter_by(store_id=store_id, name="Consultation").one()
    assert physical.stock_quantity == 12
    assert physical.low_stock_threshold == 3
    assert physical.base_unit == "kg"
    assert service.stock_quantity == 0
    assert service.low_stock_threshold is None
    assert service.base_unit == "session"
    assert ProductUnit.query.filter_by(product_id=service.id).one().base_quantity == 1


def test_csv_import_reports_invalid_rows_and_keeps_valid_rows(client):
    store_id = register_and_login(client)
    response = upload_csv(
        client,
        store_id,
        "name,price,product_type,opening_stock,low_stock_threshold\n"
        "Valid item,12.50,Physical,4,2\n"
        "Missing price,,Service,,\n"
        "Service with stock,20,Service,1,\n"
        "Negative price,-1,Physical,0,\n",
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["created_count"] == 1
    assert data["failed_count"] == 3
    assert [error["row"] for error in data["errors"]] == [3, 4, 5]
    assert Product.query.filter_by(store_id=store_id).count() == 1


def test_csv_import_rejects_unsupported_headers_and_file_types(client):
    store_id = register_and_login(client)
    headers = upload_csv(client, store_id, "name,price,secret\nItem,10,x\n")
    assert headers.status_code == 400
    assert headers.json["error"]["code"] == "VALIDATION_ERROR"

    file_type = upload_csv(client, store_id, "name,price\nItem,10\n", "catalog.txt")
    assert file_type.status_code == 400


def test_csv_import_is_store_scoped(client):
    first_store_id = register_and_login(client, "firstcsvuser")
    second_store_id = register_and_login(client, "secondcsvuser")
    response = upload_csv(
        client,
        first_store_id,
        "name,price,product_type\nPrivate service,100,Service\n",
    )
    assert response.status_code == 403
    assert Product.query.count() == 0
    assert second_store_id != first_store_id


def test_csv_import_requires_authentication(client):
    response = client.post(
        "/product/import",
        data={
            "store_id": "1",
            "file": (io.BytesIO(b"name,price\nItem,10\n"), "catalog.csv"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code in (401, 403)
