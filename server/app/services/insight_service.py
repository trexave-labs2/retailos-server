# server/app/services/insight_service.py
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func

from server.app.extensions import db
from server.app.models.alert import Alert
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.utils.response import Response
from server.app.services.product_service import ProductService
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import current_lagos_date, utc_bounds_for_lagos_date


class InsightService:
    def _sales_range(self, store_id, start_utc, end_utc):
        return Sale.query.filter(
            Sale.store_id == store_id,
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc
        )

    def _sales_total(self, store_id, local_date):
        start_utc, end_utc = utc_bounds_for_lagos_date(local_date)
        return self._sales_range(store_id, start_utc, end_utc).with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar()

    def dashboard(self, store_id):
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        today = current_lagos_date()
        start_utc, end_utc = utc_bounds_for_lagos_date(today)
        sales = self._sales_range(store_id, start_utc, end_utc)
        total = self._sales_total(store_id, today)

        products_query = ProductService.query_for_store(store_id)

        return Response.success_response(
            {
                "products_count": products_query.count(),
                "low_stock_count": Product.query.filter(
                    Product.store_id == store_id,
                    Product.low_stock_threshold.is_not(None),
                    Product.stock_quantity <= Product.low_stock_threshold
                ).count(),
                "open_alerts_count": Alert.query.filter_by(
                    store_id=store_id,
                    is_resolved=False
                ).count(),
                "today_sales_count": sales.count(),
                "today_sales_total": str(total)
            },
            "DASHBOARD_RETRIEVED"
        ), 200

    def daily_summary(self, store_id):
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        today = current_lagos_date()
        start_utc, end_utc = utc_bounds_for_lagos_date(today)
        sales = self._sales_range(store_id, start_utc, end_utc).order_by(
            Sale.created_at.asc()
        ).all()

        total = sum(
            (sale.total_amount for sale in sales),
            Decimal("0.00")
        )

        return Response.success_response(
            {
                "date": today.isoformat(),
                "sales_count": len(sales),
                "total_sales": str(total),
                "sales": [
                    {
                        "id": sale.id,
                        "total_amount": str(sale.total_amount),
                        "created_at": sale.created_at.isoformat()
                    }
                    for sale in sales
                ]
            },
            "DAILY_SUMMARY_RETRIEVED"
        ), 200

    def daily_brief(self, store_id):
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        today = current_lagos_date()
        yesterday = today - timedelta(days=1)
        today_total = Decimal(str(self._sales_total(store_id, today)))
        yesterday_total = Decimal(str(self._sales_total(store_id, yesterday)))

        comparison_available = yesterday_total != 0

        if comparison_available:
            change = ((today_total - yesterday_total) / yesterday_total) * 100
        else:
            change = None

        alerts = Alert.query.filter_by(
            store_id=store_id,
            is_resolved=False
        ).order_by(Alert.created_at.desc()).all()

        low_stock = Product.query.filter(
            Product.store_id == store_id,
            Product.low_stock_threshold.is_not(None),
            Product.stock_quantity <= Product.low_stock_threshold
        ).all()

        today_start, today_end = utc_bounds_for_lagos_date(today)
        item_totals = SaleItem.query.join(Sale).filter(
            Sale.store_id == store_id,
            Sale.created_at >= today_start,
            Sale.created_at < today_end
        ).with_entities(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("quantity")
        ).group_by(
            SaleItem.product_id
        ).order_by(
            func.sum(SaleItem.quantity).desc()
        ).all()

        high_performer = None

        if item_totals:
            product = db.session.get(Product, item_totals[0].product_id)

            if product:
                high_performer = {
                    "product_id": product.id,
                    "product_name": product.name,
                    "units_sold": int(item_totals[0].quantity)
                }

        yesterday_start, yesterday_end = utc_bounds_for_lagos_date(yesterday)
        yesterday_items = SaleItem.query.join(Sale).filter(
            Sale.store_id == store_id,
            Sale.created_at >= yesterday_start,
            Sale.created_at < yesterday_end
        ).with_entities(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("quantity")
        ).group_by(
            SaleItem.product_id
        ).all()

        today_by_product = {
            item.product_id: int(item.quantity)
            for item in item_totals
        }
        yesterday_by_product = {
            item.product_id: int(item.quantity)
            for item in yesterday_items
        }

        declining_product = None

        for product_id, yesterday_quantity in yesterday_by_product.items():
            today_quantity = today_by_product.get(product_id, 0)

            if yesterday_quantity > today_quantity:
                product = db.session.get(Product, product_id)

                if product:
                    declining_product = {
                        "product_id": product.id,
                        "product_name": product.name,
                        "yesterday_units_sold": yesterday_quantity,
                        "today_units_sold": today_quantity
                    }
                    break

        if today_total > yesterday_total:
            status = "UP"
        elif today_total < yesterday_total:
            status = "DOWN"
        else:
            status = "STEADY"

        return Response.success_response(
            {
                "date": today.isoformat(),
                "sales": {
                    "today_total": str(today_total),
                    "yesterday_total": str(yesterday_total),
                    "change_percent": (
                        str(change.quantize(Decimal("0.01")))
                        if change is not None
                        else None
                    ),
                    "comparison_available": comparison_available,
                    "status": status
                },
                "alerts": [
                    {
                        "id": alert.id,
                        "type": alert.type.value,
                        "message": alert.message,
                        "product_id": alert.product_id
                    }
                    for alert in alerts
                ],
                "insights": {
                    "restock_recommendations": [
                        {
                            "product_id": product.id,
                            "product_name": product.name,
                            "stock_quantity": product.stock_quantity,
                            "low_stock_threshold": product.low_stock_threshold
                        }
                        for product in low_stock
                    ],
                    "high_performer": high_performer,
                    "declining_product": declining_product
                }
            },
            "DAILY_BRIEF_RETRIEVED"
        ), 200
