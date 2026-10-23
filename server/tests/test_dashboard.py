from server.app.extensions import db
from server.app.models.alert import Alert, AlertType
from server.app.models.product import Product
from server.app.models.sale import Sale

from datetime import datetime, timedelta, timezone

from server.app.utils.time import current_lagos_date, utc_bounds_for_lagos_date


def register_and_login(client, username="dashboarduser"):
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


def create_sale(
    client,
    store_id,
    transaction_id,
    product_name=None,
    quantity=2,
    product_id=None
):
    if product_id is None:
        product = client.post(
            "/product/create",
            json={
                "store_id": store_id,
                "name": product_name or f"Product {transaction_id}",
                "price": 2500,
                "opening_stock": 10,
                "low_stock_threshold": 2
            }
        )

        assert product.status_code == 201

        product_id = product.json["data"]["product"]["id"]

    response = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [
                {
                    "product_id": product_id,
                    "quantity": quantity
                }
            ],
            "client_transaction_id": transaction_id
        }
    )

    assert response.status_code == 201

    return response.json["data"]["receipt"]["sale_id"]


def start_pro_trial(client, store_id):
    response = client.post("/subscriptions/trial", json={"store_id": store_id})
    assert response.status_code == 200, response.get_json()


def test_dashboard_returns_store_metrics(client):
    store_id = register_and_login(client)

    create_sale(client, store_id, "dashboard-sale-001")

    response = client.get(
        f"/dashboard?store_id={store_id}"
    )

    assert response.status_code == 200

    data = response.json["data"]

    assert data["products_count"] == 1
    assert data["low_stock_count"] == 0
    assert data["open_alerts_count"] == 0
    assert data["today_sales_count"] == 1
    assert data["today_sales_total"] == "5000.00"


def test_daily_summary_returns_sales(client):
    store_id = register_and_login(client)

    create_sale(client, store_id, "dashboard-sale-002")
    create_sale(client, store_id, "dashboard-sale-003")

    response = client.get(
        f"/dashboard/daily-summary?store_id={store_id}"
    )

    assert response.status_code == 200

    data = response.json["data"]

    assert data["sales_count"] == 2
    assert data["total_sales"] == "10000.00"
    assert len(data["sales"]) == 2


def test_dashboard_product_count_matches_product_list(client):
    store_id = register_and_login(client, username="dashboard-product-count-user")

    for name in ("Product A", "Product B"):
        response = client.post(
            "/product/create",
            json={
                "store_id": store_id,
                "name": name,
                "price": 1000,
                "opening_stock": 10,
                "low_stock_threshold": 2
            }
        )
        assert response.status_code == 201

    products_response = client.get(f"/product/list?store_id={store_id}")
    dashboard_response = client.get(f"/dashboard?store_id={store_id}")

    assert products_response.status_code == 200
    assert dashboard_response.status_code == 200

    products = products_response.json["data"]["products"]
    dashboard = dashboard_response.json["data"]

    assert dashboard["products_count"] == len(products)
    assert dashboard["products_count"] == 2


def test_dashboard_reflects_low_stock_products(client):
    store_id = register_and_login(client)

    response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Low Stock Product",
            "price": 1000,
            "opening_stock": 2,
            "low_stock_threshold": 5
        }
    )

    assert response.status_code == 201

    response = client.get(
        f"/dashboard?store_id={store_id}"
    )

    assert response.status_code == 200

    data = response.json["data"]

    assert data["products_count"] == 1
    assert data["low_stock_count"] == 1


def test_dashboard_requires_authentication(client):
    response = client.get("/dashboard?store_id=1")

    assert response.status_code == 401


def test_daily_summary_requires_authentication(client):
    response = client.get("/dashboard/daily-summary?store_id=1")

    assert response.status_code == 401


# Check that daily brief guards against division by zero.
def test_daily_brief_handles_zero_yesterday_sales(client):
    store_id = register_and_login(client)

    create_sale(client, store_id, "daily-brief-zero")

    response = client.get(
        f"/dashboard/daily-brief?store_id={store_id}"
    )

    assert response.status_code == 200

    sales = response.json["data"]["sales"]

    assert sales["yesterday_total"] == "0.00"
    assert sales["change_percent"] is None
    assert sales["comparison_available"] is False
    assert sales["status"] == "UP"


# Check daily brief percentage change, high performer and declining product.
def test_daily_brief_compares_days_and_builds_insights(client):
    store_id = register_and_login(client)

    high_performer_sale_id = create_sale(
        client, store_id, "daily-brief-high",
        product_name="Fast Product", quantity=3
    )

    declining_product = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Declining Product",
            "price": 2500,
            "opening_stock": 10,
            "low_stock_threshold": 2
        }
    )

    assert declining_product.status_code == 201

    declining_product_id = declining_product.json["data"]["product"]["id"]

    create_sale(
        client, store_id, "daily-brief-decline-today",
        quantity=1, product_id=declining_product_id
    )

    yesterday_decline_sale_id = create_sale(
        client, store_id, "daily-brief-decline-yesterday",
        quantity=2, product_id=declining_product_id
    )

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    yesterday_sale = db.session.get(Sale, yesterday_decline_sale_id)
    yesterday_sale.created_at = yesterday
    db.session.commit()

    response = client.get(
        f"/dashboard/daily-brief?store_id={store_id}"
    )

    assert response.status_code == 200

    data = response.json["data"]

    assert data["sales"]["today_total"] == "10000.00"
    assert data["sales"]["yesterday_total"] == "5000.00"
    assert data["sales"]["change_percent"] == "100.00"
    assert data["sales"]["status"] == "UP"

    assert data["insights"]["high_performer"]["product_name"] == "Fast Product"
    assert data["insights"]["high_performer"]["units_sold"] == 3
    assert data["insights"]["declining_product"]["product_name"] == "Declining Product"
    assert data["insights"]["declining_product"]["yesterday_units_sold"] == 2
    assert data["insights"]["declining_product"]["today_units_sold"] == 1

    assert high_performer_sale_id != yesterday_decline_sale_id



def test_dashboard_uses_africa_lagos_day_boundaries(client):
    store_id = register_and_login(client, username="dashboard-timezone-user")
    start_utc, end_utc = utc_bounds_for_lagos_date(current_lagos_date())

    before_boundary = Sale(
        store_id=store_id,
        total_amount=1000,
        payment_method="Cash",
        created_at=start_utc - timedelta(minutes=1)
    )
    at_boundary = Sale(
        store_id=store_id,
        total_amount=2500,
        payment_method="Cash",
        created_at=start_utc
    )
    db.session.add_all([before_boundary, at_boundary])
    db.session.commit()

    response = client.get(f"/dashboard?store_id={store_id}")

    assert response.status_code == 200
    data = response.json["data"]
    assert data["today_sales_count"] == 1
    assert data["today_sales_total"] == "2500.00"
    assert end_utc > start_utc


def test_daily_summary_uses_africa_lagos_day_boundaries(client):
    store_id = register_and_login(client, username="daily-summary-timezone-user")
    start_utc, _ = utc_bounds_for_lagos_date(current_lagos_date())

    db.session.add_all([
        Sale(store_id=store_id, total_amount=1000, payment_method="Cash", created_at=start_utc - timedelta(minutes=1)),
        Sale(store_id=store_id, total_amount=2500, payment_method="Cash", created_at=start_utc),
    ])
    db.session.commit()

    response = client.get(f"/dashboard/daily-summary?store_id={store_id}")

    assert response.status_code == 200
    data = response.json["data"]
    assert data["sales_count"] == 1
    assert data["total_sales"] == "2500.00"



def test_services_are_excluded_from_inventory_recommendations_and_legacy_alerts(client):
    store_id = register_and_login(client, username="s14-inventory-user")

    physical_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Low Stock Rice",
            "price": 1000,
            "opening_stock": 1,
            "low_stock_threshold": 5,
        },
    )
    assert physical_response.status_code == 201
    physical_id = physical_response.json["data"]["product"]["id"]

    service_response = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Haircut",
            "product_type": "Service",
            "price": 1000,
        },
    )
    assert service_response.status_code == 201
    service_id = service_response.json["data"]["product"]["id"]

    # Simulate a legacy row and alert created before service inventory was forbidden.
    service = db.session.get(Product, service_id)
    service.stock_quantity = 1
    service.low_stock_threshold = 5
    legacy_alert = Alert(
        store_id=store_id,
        product_id=service_id,
        type=AlertType.LOW_STOCK,
        message="Haircut is low on stock",
    )
    db.session.add(legacy_alert)
    db.session.commit()

    dashboard_response = client.get(f"/dashboard?store_id={store_id}")
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json["data"]
    assert dashboard["low_stock_count"] == 1
    assert dashboard["open_alerts_count"] == 1

    brief_response = client.get(f"/dashboard/daily-brief?store_id={store_id}")
    assert brief_response.status_code == 200
    brief = brief_response.json["data"]
    assert [item["product_id"] for item in brief["insights"]["restock_recommendations"]] == [
        physical_id
    ]
    assert all(alert["product_id"] != service_id for alert in brief["alerts"])

    alerts_response = client.get(f"/alerts?store_id={store_id}")
    assert alerts_response.status_code == 200
    assert all(alert["product_id"] != service_id for alert in alerts_response.json["data"]["alerts"])

    service_read = client.get(f"/product/get?store_id={store_id}&id={service_id}")
    assert service_read.status_code == 200
    service_data = service_read.json["data"]["product"]
    assert service_data["stock_quantity"] == 0
    assert service_data["low_stock_threshold"] is None
    assert service_data["stock"]["quantity"] == 0
    assert service_data["stock"]["low_stock_threshold"] is None

    update_response = client.patch(
        "/product/update",
        json={"store_id": store_id, "id": service_id, "name": "Haircut Service"},
    )
    assert update_response.status_code == 200
    db.session.refresh(service)
    db.session.refresh(legacy_alert)
    assert service.stock_quantity == 0
    assert service.low_stock_threshold is None
    assert legacy_alert.is_resolved is True



def test_dashboard_time_series_compares_physical_products_and_services(client):
    store_id = register_and_login(client, username="dashboard-series-user")
    start_pro_trial(client, store_id)

    physical = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Rice",
            "product_type": "Physical",
            "price": 2500,
            "opening_stock": 10,
            "low_stock_threshold": 2,
        },
    )
    service = client.post(
        "/product/create",
        json={
            "store_id": store_id,
            "name": "Delivery",
            "product_type": "Service",
            "price": 1200,
        },
    )
    assert physical.status_code == 201
    assert service.status_code == 201

    physical_id = physical.json["data"]["product"]["id"]
    service_id = service.json["data"]["product"]["id"]
    create_sale(client, store_id, "dashboard-series-physical", quantity=2, product_id=physical_id)

    service_sale = client.post(
        "/sales",
        json={
            "store_id": store_id,
            "payment_method": "Cash",
            "items": [{"product_id": service_id, "quantity": 1}],
            "client_transaction_id": "dashboard-series-service",
        },
    )
    assert service_sale.status_code == 201

    today = current_lagos_date().isoformat()
    response = client.get(
        f"/dashboard/time-series?store_id={store_id}&start_date={today}&end_date={today}"
    )

    assert response.status_code == 200
    data = response.json["data"]
    assert data["period"] == {
        "start_date": today,
        "end_date": today,
        "timezone": "Africa/Lagos",
    }
    assert len(data["series"]) == 1
    point = data["series"][0]
    assert point["physical"]["gross_sales"] == "5000.00"
    assert point["physical"]["units_sold"] == 2
    assert point["physical"]["orders_count"] == 1
    assert point["services"]["gross_sales"] == "1200.00"
    assert point["services"]["units_sold"] == 1
    assert point["services"]["orders_count"] == 1
    assert point["gross_sales"] == "6200.00"
    assert point["refunds"] == "0.00"
    assert point["net_sales"] == "6200.00"
    assert {item["type"] for item in data["items"]} == {"Physical", "Service"}


def test_dashboard_time_series_validates_selected_period(client):
    store_id = register_and_login(client, username="dashboard-series-validation")
    start_pro_trial(client, store_id)

    response = client.get(
        f"/dashboard/time-series?store_id={store_id}&start_date=2026-10-20"
    )
    assert response.status_code == 400

    response = client.get(
        f"/dashboard/time-series?store_id={store_id}&start_date=2026-10-22&end_date=2026-10-20"
    )
    assert response.status_code == 400


def test_dashboard_time_series_requires_authentication(client):
    response = client.get(
        "/dashboard/time-series?store_id=1&start_date=2026-10-20&end_date=2026-10-21"
    )
    assert response.status_code == 401


def test_dashboard_time_series_denies_another_store(client):
    first_store_id = register_and_login(client, username="dashboard-series-owner-a")
    register_and_login(client, username="dashboard-series-owner-b")
    today = current_lagos_date().isoformat()

    response = client.get(
        f"/dashboard/time-series?store_id={first_store_id}&start_date={today}&end_date={today}"
    )
    assert response.status_code == 403



def _record_refund(store_id, sale_id, amount, transaction_id, created_at=None):
    from server.app.models.refund import Refund

    refund = Refund(
        store_id=store_id,
        sale_id=sale_id,
        amount=amount,
        refund_type="Partial",
        reason="Dashboard calculation regression test",
        payment_method="Cash",
        reference=f"RFD-{transaction_id}",
        client_transaction_id=transaction_id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.session.add(refund)
    db.session.commit()
    return refund


def test_dashboard_daily_brief_and_summary_subtract_refunds_in_their_recorded_day(client):
    store_id = register_and_login(client, username="dashboard-net-refund-user")
    today_sale_id = create_sale(client, store_id, "dashboard-net-refund-today")
    yesterday_sale_id = create_sale(client, store_id, "dashboard-net-refund-yesterday")

    today = current_lagos_date()
    today_start, _ = utc_bounds_for_lagos_date(today)
    yesterday_start, _ = utc_bounds_for_lagos_date(today - timedelta(days=1))

    today_sale = db.session.get(Sale, today_sale_id)
    yesterday_sale = db.session.get(Sale, yesterday_sale_id)
    today_sale.created_at = today_start + timedelta(hours=1)
    yesterday_sale.created_at = yesterday_start + timedelta(hours=1)
    db.session.commit()

    _record_refund(
        store_id,
        today_sale_id,
        "1000.00",
        "dashboard-refund-today",
        created_at=today_start + timedelta(hours=2),
    )
    _record_refund(
        store_id,
        yesterday_sale_id,
        "500.00",
        "dashboard-refund-yesterday",
        created_at=yesterday_start + timedelta(hours=2),
    )

    dashboard_response = client.get(f"/dashboard?store_id={store_id}")
    summary_response = client.get(f"/dashboard/daily-summary?store_id={store_id}")
    brief_response = client.get(f"/dashboard/daily-brief?store_id={store_id}")

    assert dashboard_response.status_code == 200
    assert dashboard_response.json["data"]["today_sales_count"] == 1
    assert dashboard_response.json["data"]["today_sales_total"] == "4000.00"

    assert summary_response.status_code == 200
    summary = summary_response.json["data"]
    assert summary["gross_sales"] == "5000.00"
    assert summary["total_refunds"] == "1000.00"
    assert summary["total_sales"] == "4000.00"

    assert brief_response.status_code == 200
    sales = brief_response.json["data"]["sales"]
    assert sales["today_total"] == "4000.00"
    assert sales["yesterday_total"] == "4500.00"
    assert sales["change_percent"] == "-11.11"
    assert sales["status"] == "DOWN"


def test_dashboard_time_series_uses_local_date_for_sale_and_refund_periods(client):
    store_id = register_and_login(client, username="dashboard-series-refund-period-user")
    start_pro_trial(client, store_id)
    sale_id = create_sale(client, store_id, "dashboard-series-refund-period")
    today = current_lagos_date()
    start_utc, _ = utc_bounds_for_lagos_date(today)
    sale = db.session.get(Sale, sale_id)
    sale.created_at = start_utc + timedelta(hours=1)
    db.session.commit()

    _record_refund(
        store_id,
        sale_id,
        "750.00",
        "dashboard-series-refund-period-key",
        created_at=start_utc + timedelta(hours=2),
    )

    response = client.get(
        f"/dashboard/time-series?store_id={store_id}"
        f"&start_date={today.isoformat()}&end_date={today.isoformat()}"
    )

    assert response.status_code == 200
    point = response.json["data"]["series"][0]
    assert point["gross_sales"] == "5000.00"
    assert point["refunds"] == "750.00"
    assert point["net_sales"] == "4250.00"
