from datetime import timedelta

from server.app.models.refund import RefundItem
from server.app.models.sale_item import SaleItem
from server.app.utils.time import current_lagos_date


def register_and_login(client):
    response = client.post(
        "/auth/register",
        json={
            "username": "rankings-user",
            "email": "rankings-user@example.com",
            "password": "test123",
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/auth/login",
        json={"email": "rankings-user@example.com", "password": "test123"},
    )
    assert response.status_code == 200
    return response.json["data"]["user"]["store_id"]


def create_product(client, store_id, name, product_type="Physical"):
    payload = {
        "store_id": store_id,
        "name": name,
        "product_type": product_type,
        "base_unit": "piece",
        "price": 2500,
    }
    if product_type == "Physical":
        payload.update({"opening_stock": 20, "low_stock_threshold": 2})

    response = client.post("/product/create", json=payload)
    assert response.status_code == 201, response.get_json()
    return response.json["data"]["product"]["id"]


def create_sale(client, store_id, product_id, transaction_id, quantity=1):
    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": product_id, "quantity": quantity}],
            "client_transaction_id": transaction_id,
        },
    )
    assert response.status_code == 201
    return response.json["data"]["receipt"]["sale_id"]


def test_rankings_separate_physical_products_and_services_for_selected_period(client):
    store_id = register_and_login(client)
    rice_id = create_product(client, store_id, "Rice")
    eggs_id = create_product(client, store_id, "Eggs")
    haircut_id = create_product(client, store_id, "Haircut", "Service")

    create_sale(client, store_id, rice_id, "rank-rice-sale", quantity=5)
    create_sale(client, store_id, eggs_id, "rank-eggs-sale", quantity=3)
    create_sale(client, store_id, haircut_id, "rank-haircut-sale-1")
    create_sale(client, store_id, haircut_id, "rank-haircut-sale-2", quantity=4)

    today = current_lagos_date().isoformat()
    response = client.get(
        f"/dashboard/rankings?store_id={store_id}&start_date={today}&end_date={today}"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["period"] == {
        "start_date": today,
        "end_date": today,
        "timezone": "Africa/Lagos",
    }
    assert data["top_selling_products"] == [
        {"product_id": rice_id, "product_name": "Rice", "units_sold": 5, "unit": "piece"},
        {"product_id": eggs_id, "product_name": "Eggs", "units_sold": 3, "unit": "piece"},
    ]
    assert data["top_services"] == [
        {"service_id": haircut_id, "service_name": "Haircut", "completed_orders": 2}
    ]


def test_rankings_subtract_recorded_product_returns_and_exclude_fully_refunded_service(client):
    store_id = register_and_login(client)
    physical_id = create_product(client, store_id, "Soap")
    service_id = create_product(client, store_id, "Repair", "Service")

    physical_sale_id = create_sale(client, store_id, physical_id, "rank-refund-physical", quantity=5)
    service_sale_id = create_sale(client, store_id, service_id, "rank-refund-service")
    physical_item = SaleItem.query.filter_by(sale_id=physical_sale_id).one()

    refund_response = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": physical_sale_id,
            "amount": "5000.00",
            "reason": "Two items returned",
            "payment_method": "Cash",
            "client_transaction_id": "rank-refund-physical-key",
            "returned_items": [{"sale_item_id": physical_item.id, "quantity": 2}],
        },
    )
    assert refund_response.status_code == 201
    assert RefundItem.query.filter_by(sale_item_id=physical_item.id).count() == 1

    service_refund = client.post(
        "/sales/refunds",
        json={
            "store_id": store_id,
            "sale_id": service_sale_id,
            "amount": "2500.00",
            "reason": "Service fully refunded",
            "payment_method": "Cash",
            "client_transaction_id": "rank-refund-service-key",
        },
    )
    assert service_refund.status_code == 201

    today = current_lagos_date().isoformat()
    response = client.get(
        f"/dashboard/rankings?store_id={store_id}&start_date={today}&end_date={today}"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["top_selling_products"] == [
        {"product_id": physical_id, "product_name": "Soap", "units_sold": 3, "unit": "piece"}
    ]
    assert data["top_services"] == []


def test_rankings_return_empty_lists_when_period_has_no_qualifying_sales(client):
    store_id = register_and_login(client)
    today = current_lagos_date()
    empty_period = (today - timedelta(days=10)).isoformat()

    response = client.get(
        f"/dashboard/rankings?store_id={store_id}&start_date={empty_period}&end_date={empty_period}"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["top_selling_products"] == []
    assert data["top_services"] == []
    assert data["ranking_rules"]["limit"] == 10


def test_rankings_reject_incomplete_or_reversed_period(client):
    store_id = register_and_login(client)

    incomplete = client.get(
        f"/dashboard/rankings?store_id={store_id}&start_date=2026-10-01"
    )
    assert incomplete.status_code == 400

    reversed_period = client.get(
        f"/dashboard/rankings?store_id={store_id}&start_date=2026-10-22&end_date=2026-10-01"
    )
    assert reversed_period.status_code == 400


def test_rankings_require_authentication(client):
    response = client.get("/dashboard/rankings?store_id=1")
    assert response.status_code == 401
