# server/app/services/insight_service.py
from datetime import date, timedelta
from decimal import Decimal

from flask import request

from sqlalchemy import and_, func, or_

from server.app.extensions import db
from server.app.models.alert import Alert
from server.app.models.product import Product
from server.app.models.sale import Sale
from server.app.models.sale_item import SaleItem
from server.app.models.refund import Refund, RefundItem
from server.app.utils.response import Response
from server.app.services.product_service import ProductService
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import LAGOS, current_lagos_date, ensure_utc, utc_bounds_for_lagos_date


class InsightService:
    def _sales_range(self, store_id, start_utc, end_utc):
        return Sale.query.filter(
            Sale.store_id == store_id,
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc
        )

    def _refund_total(self, store_id, start_utc, end_utc):
        return db.session.query(
            func.coalesce(func.sum(Refund.amount), 0)
        ).filter(
            Refund.store_id == store_id,
            Refund.created_at >= start_utc,
            Refund.created_at < end_utc,
        ).scalar()

    def _sales_total(self, store_id, local_date):
        start_utc, end_utc = utc_bounds_for_lagos_date(local_date)
        gross_sales = self._sales_range(store_id, start_utc, end_utc).with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar()
        refunds = self._refund_total(store_id, start_utc, end_utc)
        net_sales = Decimal(str(gross_sales or 0)) - Decimal(str(refunds or 0))
        return net_sales.quantize(Decimal("0.01"))

    def _ranking_period(self):
        today = current_lagos_date()
        start_value = request.args.get("start_date")
        end_value = request.args.get("end_date")

        if start_value is None and end_value is None:
            start_date = end_date = today
        elif not start_value or not end_value:
            return None, None, None, None, (
                Response.error_response(
                    "VALIDATION_ERROR",
                    "Provide both start_date and end_date in YYYY-MM-DD format",
                    {"start_date": start_value, "end_date": end_value},
                ),
                400,
            )
        else:
            try:
                start_date = date.fromisoformat(start_value)
                end_date = date.fromisoformat(end_value)
                if start_date.isoformat() != start_value or end_date.isoformat() != end_value:
                    raise ValueError("Dates must use YYYY-MM-DD")
            except (TypeError, ValueError):
                return None, None, None, None, (
                    Response.error_response(
                        "VALIDATION_ERROR",
                        "Dates must use YYYY-MM-DD format",
                        {"start_date": start_value, "end_date": end_value},
                    ),
                    400,
                )

        if start_date > end_date:
            return None, None, None, None, (
                Response.error_response(
                    "VALIDATION_ERROR",
                    "start_date must be on or before end_date",
                    {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
                ),
                400,
            )

        start_utc, _ = utc_bounds_for_lagos_date(start_date)
        _, end_utc = utc_bounds_for_lagos_date(end_date)
        return start_date, end_date, start_utc, end_utc, None

    def _ranked_physical_products(self, store_id, start_utc, end_utc, limit=10):
        returned_units = db.session.query(
            RefundItem.sale_item_id.label("sale_item_id"),
            func.sum(RefundItem.quantity * RefundItem.unit_base_quantity).label("returned_base_units"),
        ).group_by(RefundItem.sale_item_id).subquery()

        net_units = func.sum(
            SaleItem.base_quantity_deducted - func.coalesce(returned_units.c.returned_base_units, 0)
        )
        query = db.session.query(
            Product.id,
            Product.name,
            Product.base_unit,
            net_units.label("units_sold"),
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).outerjoin(
            returned_units, returned_units.c.sale_item_id == SaleItem.id
        ).filter(
            Product.store_id == store_id,
            Product.product_type == "Physical",
            Sale.store_id == store_id,
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc,
        ).group_by(
            Product.id, Product.name, Product.base_unit
        ).having(
            net_units > 0
        ).order_by(
            net_units.desc(), Product.name.asc(), Product.id.asc()
        )
        if limit is not None:
            query = query.limit(limit)
        rows = query.all()

        return [
            {
                "product_id": row.id,
                "product_name": row.name,
                "units_sold": int(row.units_sold),
                "unit": row.base_unit,
            }
            for row in rows
        ]

    def _ranked_services(self, store_id, start_utc, end_utc):
        refunded_total = db.session.query(
            func.coalesce(func.sum(Refund.amount), 0)
        ).filter(
            Refund.sale_id == Sale.id
        ).correlate(Sale).scalar_subquery()

        fully_refunded = and_(
            refunded_total > 0,
            refunded_total >= Sale.total_amount,
            Sale.amount_paid >= Sale.total_amount,
        )
        rows = db.session.query(
            Product.id,
            Product.name,
            func.count(func.distinct(Sale.id)).label("completed_orders"),
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Product.store_id == store_id,
            Product.product_type == "Service",
            Sale.store_id == store_id,
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc,
            ~fully_refunded,
        ).group_by(
            Product.id, Product.name
        ).order_by(
            func.count(func.distinct(Sale.id)).desc(),
            Product.name.asc(),
            Product.id.asc(),
        ).limit(10).all()

        return [
            {
                "service_id": row.id,
                "service_name": row.name,
                "completed_orders": int(row.completed_orders),
            }
            for row in rows
        ]

    def dashboard_rankings(self, store_id):
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403

        from server.app.services.subscription_service import SubscriptionService
        feature_error = SubscriptionService.check_feature(store_id, "advanced_reporting")
        if feature_error:
            return feature_error

        start_date, end_date, start_utc, end_utc, error = self._ranking_period()
        if error:
            return error

        return Response.success_response(
            {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "timezone": "Africa/Lagos",
                },
                "top_selling_products": self._ranked_physical_products(
                    store_id, start_utc, end_utc
                ),
                "top_services": self._ranked_services(
                    store_id, start_utc, end_utc
                ),
                "ranking_rules": {
                    "products": "Physical products only; ranked by net base units sold. Recorded returned quantities are subtracted.",
                    "services": "Service products only; ranked by distinct completed sales containing each service. Each sale counts once per service.",
                    "refunds": "Product units decrease only when returned quantities are recorded. Fully refunded, fully paid sales are excluded from service order counts; partial refunds do not remove an order.",
                    "cancellations_and_voids": "V1 has no explicit cancellation or void status. Persisted sales cannot be identified as cancelled or voided, so this endpoint does not infer those states.",
                    "empty_results": "Each ranking is an empty list when no qualifying records exist.",
                    "limit": 10,
                },
            },
            "DASHBOARD_RANKINGS_RETRIEVED",
        ), 200

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
                    Product.product_type == "Physical",
                    Product.low_stock_threshold.is_not(None),
                    Product.stock_quantity <= Product.low_stock_threshold
                ).count(),
                "open_alerts_count": Alert.query.outerjoin(
                    Product,
                    Alert.product_id == Product.id,
                ).filter(
                    Alert.store_id == store_id,
                    Alert.is_resolved.is_(False),
                    or_(
                        Alert.product_id.is_(None),
                        Product.product_type == "Physical",
                    ),
                ).count(),
                "today_sales_count": sales.count(),
                "today_sales_total": str(total)
            },
            "DASHBOARD_RETRIEVED"
        ), 200

    def dashboard_time_series(self, store_id):
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403

        from server.app.services.subscription_service import SubscriptionService
        feature_error = SubscriptionService.check_feature(store_id, "advanced_reporting")
        if feature_error:
            return feature_error

        start_date, end_date, start_utc, end_utc, error = self._ranking_period()
        if error:
            return error

        # Aggregate in application code so daily buckets use Africa/Lagos dates
        # consistently on both SQLite (tests) and PostgreSQL (production).
        sales_lines = db.session.query(Sale, SaleItem, Product).join(
            SaleItem, SaleItem.sale_id == Sale.id
        ).join(
            Product, Product.id == SaleItem.product_id
        ).filter(
            Sale.store_id == store_id,
            Sale.created_at >= start_utc,
            Sale.created_at < end_utc,
            Product.store_id == store_id,
        ).order_by(Sale.created_at.asc(), Sale.id.asc(), SaleItem.id.asc()).all()

        refunds = Refund.query.filter(
            Refund.store_id == store_id,
            Refund.created_at >= start_utc,
            Refund.created_at < end_utc,
        ).order_by(Refund.created_at.asc(), Refund.id.asc()).all()

        daily = {}
        for offset in range((end_date - start_date).days + 1):
            day = start_date + timedelta(days=offset)
            daily[day.isoformat()] = {
                "date": day.isoformat(),
                "physical": {
                    "gross_sales": Decimal("0.00"),
                    "units_sold": 0,
                    "_orders": set(),
                },
                "services": {
                    "gross_sales": Decimal("0.00"),
                    "units_sold": 0,
                    "_orders": set(),
                },
                "gross_sales": Decimal("0.00"),
                "refunds": Decimal("0.00"),
            }

        item_totals = {}
        for sale, line, product in sales_lines:
            local_day = ensure_utc(sale.created_at).astimezone(LAGOS).date().isoformat()
            if local_day not in daily:
                continue

            item_type = "services" if product.product_type == "Service" else "physical"
            amount = Decimal(str(line.total or 0))
            units = line.quantity if item_type == "services" else line.base_quantity_deducted
            bucket = daily[local_day][item_type]
            bucket["gross_sales"] += amount
            bucket["units_sold"] += int(units)
            bucket["_orders"].add(sale.id)
            daily[local_day]["gross_sales"] += amount

            key = (product.id, product.name, product.product_type)
            if key not in item_totals:
                item_totals[key] = {
                    "product_id": product.id,
                    "name": product.name,
                    "type": product.product_type,
                    "gross_sales": Decimal("0.00"),
                    "units_sold": 0,
                    "_orders": set(),
                }
            item_totals[key]["gross_sales"] += amount
            item_totals[key]["units_sold"] += int(units)
            item_totals[key]["_orders"].add(sale.id)

        for refund in refunds:
            local_day = ensure_utc(refund.created_at).astimezone(LAGOS).date().isoformat()
            if local_day in daily:
                daily[local_day]["refunds"] += Decimal(str(refund.amount or 0))

        points = []
        for day in daily.values():
            physical = day["physical"]
            services = day["services"]
            gross = day["gross_sales"]
            refund_total = day["refunds"]
            points.append({
                "date": day["date"],
                "physical": {
                    "gross_sales": str(physical["gross_sales"].quantize(Decimal("0.01"))),
                    "units_sold": physical["units_sold"],
                    "orders_count": len(physical["_orders"]),
                },
                "services": {
                    "gross_sales": str(services["gross_sales"].quantize(Decimal("0.01"))),
                    "units_sold": services["units_sold"],
                    "orders_count": len(services["_orders"]),
                },
                "gross_sales": str(gross.quantize(Decimal("0.01"))),
                "refunds": str(refund_total.quantize(Decimal("0.01"))),
                "net_sales": str((gross - refund_total).quantize(Decimal("0.01"))),
            })

        item_breakdown = [{
            "product_id": item["product_id"],
            "name": item["name"],
            "type": item["type"],
            "gross_sales": str(item["gross_sales"].quantize(Decimal("0.01"))),
            "units_sold": item["units_sold"],
            "orders_count": len(item["_orders"]),
        } for item in sorted(
            item_totals.values(),
            key=lambda item: (-item["gross_sales"], item["name"].casefold(), item["product_id"]),
        )]

        return Response.success_response(
            {
                "period": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "timezone": "Africa/Lagos",
                },
                "metrics": {
                    "gross_sales": "Sum of sale-line totals recorded during the selected period, grouped by the item's type.",
                    "refunds": "Refund amounts recorded during the selected period; refunds are reported separately and are not attributed to physical products or services because refund line amounts are not stored.",
                    "net_sales": "Gross sales minus refunds recorded during the same local calendar day.",
                    "units_sold": "Physical products use base quantity deducted; services use sale-line quantity.",
                    "orders_count": "Distinct sales containing at least one item of that type or product.",
                    "date_boundaries": "Inclusive Africa/Lagos calendar dates, converted to UTC half-open query bounds.",
                },
                "series": points,
                "items": item_breakdown,
            },
            "DASHBOARD_TIME_SERIES_RETRIEVED",
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

        gross_total = sum(
            (Decimal(str(sale.total_amount)) for sale in sales),
            Decimal("0.00")
        )
        refunds = Refund.query.filter(
            Refund.store_id == store_id,
            Refund.created_at >= start_utc,
            Refund.created_at < end_utc,
        ).order_by(Refund.created_at.asc(), Refund.id.asc()).all()
        total_refunds = sum(
            (Decimal(str(refund.amount)) for refund in refunds),
            Decimal("0.00")
        )
        total = gross_total - total_refunds

        return Response.success_response(
            {
                "date": today.isoformat(),
                "sales_count": len(sales),
                "gross_sales": str(gross_total),
                "total_refunds": str(total_refunds),
                "total_sales": str(total),
                "refunds": [
                    {
                        "id": refund.id,
                        "sale_id": refund.sale_id,
                        "reference": refund.reference,
                        "amount": str(refund.amount),
                        "created_at": refund.created_at.isoformat(),
                    }
                    for refund in refunds
                ],
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

        alerts = Alert.query.outerjoin(
            Product,
            Alert.product_id == Product.id,
        ).filter(
            Alert.store_id == store_id,
            Alert.is_resolved.is_(False),
            or_(
                Alert.product_id.is_(None),
                Product.product_type == "Physical",
            ),
        ).order_by(Alert.created_at.desc()).all()

        low_stock = Product.query.filter(
            Product.store_id == store_id,
            Product.product_type == "Physical",
            Product.low_stock_threshold.is_not(None),
            Product.stock_quantity <= Product.low_stock_threshold
        ).all()

        today_start, today_end = utc_bounds_for_lagos_date(today)
        today_products = self._ranked_physical_products(
            store_id, today_start, today_end, limit=None
        )
        high_performer = None
        if today_products:
            high_performer = {
                "product_id": today_products[0]["product_id"],
                "product_name": today_products[0]["product_name"],
                "units_sold": today_products[0]["units_sold"],
            }

        yesterday_start, yesterday_end = utc_bounds_for_lagos_date(yesterday)
        yesterday_products = self._ranked_physical_products(
            store_id, yesterday_start, yesterday_end, limit=None
        )
        today_by_product = {
            item["product_id"]: item["units_sold"] for item in today_products
        }
        yesterday_by_product = {
            item["product_id"]: item["units_sold"] for item in yesterday_products
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
