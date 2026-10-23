import io

from server.app.models.product import Product


def register_and_login(client, username):
    email = f"{username}@example.com"
    assert client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "test123"},
    ).status_code == 201
    login = client.post("/auth/login", json={"email": email, "password": "test123"})
    assert login.status_code == 200
    return login.json["data"]["user"]["store_id"]


def upload_csv(client, store_id, text):
    return client.post(
        "/product/import",
        data={
            "store_id": str(store_id),
            "file": (io.BytesIO(text.encode("utf-8")), "metadata.csv"),
        },
        content_type="multipart/form-data",
    )


def test_csv_metadata_is_validated_and_persisted(client):
    store_id = register_and_login(client, "metadataimport")
    response = upload_csv(
        client,
        store_id,
        "name,price,sku,barcode,cost_price\n"
        "Rice,2500,RICE-1,000123,1800.50\n"
        "Beans,3000,rice-1,000124,2000\n"
        "Sugar,1000,SUGAR-1,000123,-1\n",
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["created_count"] == 1
    assert data["failed_count"] == 2
    assert data["errors"][0]["row"] == 3
    assert data["errors"][0]["field"] == "sku"
    assert data["errors"][1]["row"] == 4
    assert data["errors"][1]["field"] == "cost_price"

    product = Product.query.filter_by(store_id=store_id, name="Rice").one()
    assert product.sku == "RICE-1"
    assert product.barcode == "000123"
    assert str(product.cost_price) == "1800.50"


def test_csv_metadata_blank_values_remain_null(client):
    store_id = register_and_login(client, "blankmetadata")
    response = upload_csv(client, store_id, "name,price,sku,barcode,cost_price\nSoap,250,, ,\n")

    assert response.status_code == 200
    assert response.json["data"]["created_count"] == 1
    product = Product.query.filter_by(store_id=store_id, name="Soap").one()
    assert product.sku is None
    assert product.barcode is None
    assert product.cost_price is None


def test_csv_rejects_both_category_columns(client):
    store_id = register_and_login(client, "categoryheaders")
    response = upload_csv(
        client, store_id,
        "name,price,category,category_id\nItem,10,Groceries,1\n",
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "VALIDATION_ERROR"
