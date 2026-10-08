# server/app/services/alert_service.py
from flask import request

from server.app.extensions import db
from server.app.models.alert import Alert, AlertType
from server.app.models.product import Product
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store


class AlertService:
    @staticmethod
    def sync_low_stock_alert(product):
        """Keep the product's open low-stock alert in sync with its threshold."""
        existing_alert = Alert.query.filter_by(
            store_id=product.store_id,
            product_id=product.id,
            type=AlertType.LOW_STOCK,
            is_resolved=False
        ).first()

        is_low_stock = (
            product.product_type == "Physical"
            and product.low_stock_threshold is not None
            and product.stock_quantity <= product.low_stock_threshold
        )

        if is_low_stock and not existing_alert:
            db.session.add(Alert(
                store_id=product.store_id,
                product_id=product.id,
                type=AlertType.LOW_STOCK,
                message=f"{product.name} is low on stock"
            ))
        elif not is_low_stock and existing_alert:
            existing_alert.is_resolved = True

    def list_alerts(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        alerts = Alert.query.filter_by(
            store_id=store_id,
            is_resolved=False
        ).order_by(Alert.created_at.desc()).all()

        return Response.success_response(
            {
                "alerts": [
                    {
                        "id": alert.id,
                        "type": alert.type.value,
                        "message": alert.message,
                        "product_id": alert.product_id,
                        "created_at": alert.created_at.isoformat()
                    }
                    for alert in alerts
                ]
            },
            "ALERTS_RETRIEVED"
        ), 200

    def generate_low_stock_alerts(self):
        store_id = request.args.get("store_id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        products = Product.query.filter(
            Product.store_id == store_id,
            Product.product_type == "Physical",
            Product.low_stock_threshold.is_not(None),
            Product.stock_quantity <= Product.low_stock_threshold
        ).all()

        created_alerts = []

        for product in products:
            existing_alert = Alert.query.filter_by(
                store_id=store_id,
                product_id=product.id,
                type=AlertType.LOW_STOCK,
                is_resolved=False
            ).first()

            if existing_alert:
                continue

            alert = Alert(
                store_id=store_id,
                product_id=product.id,
                type=AlertType.LOW_STOCK,
                message=f"{product.name} is low on stock"
            )

            db.session.add(alert)
            created_alerts.append(product.id)

        db.session.commit()

        return Response.success_response(
            {
                "created_alerts": created_alerts
            },
            "LOW_STOCK_ALERTS_GENERATED"
        ), 200

    def resolve_alert(self):
        store_id = request.args.get("store_id", type=int)
        alert_id = request.args.get("id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        alert = Alert.query.filter_by(
            id=alert_id,
            store_id=store_id
        ).first()

        if not alert:
            return Response.error_response(
                "ALERT_NOT_FOUND",
                "Alert not found",
                {}
            ), 404

        alert.is_resolved = True
        db.session.commit()

        return Response.success_response(
            message="ALERT_RESOLVED"
        ), 200


    def create_low_stock_alerts(self, store_id, product_ids=None):
        query = Product.query.filter(
            Product.store_id == store_id,
            Product.low_stock_threshold.is_not(None),
            Product.stock_quantity <= Product.low_stock_threshold
        )

        if product_ids:
            query = query.filter(Product.id.in_(product_ids))

        products = query.all()
        created_alerts = []

        for product in products:
            existing_alert = Alert.query.filter_by(
                store_id=store_id,
                product_id=product.id,
                type=AlertType.LOW_STOCK,
                is_resolved=False
            ).first()

            if existing_alert:
                continue

            alert = Alert(
                store_id=store_id,
                product_id=product.id,
                type=AlertType.LOW_STOCK,
                message=f"{product.name} is low on stock"
            )

            db.session.add(alert)
            created_alerts.append(product.id)

        return created_alerts

    def delete_alert(self):
        store_id = request.args.get("store_id", type=int)
        alert_id = request.args.get("id", type=int)

        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {}
            ), 403

        alert = Alert.query.filter_by(
            id=alert_id,
            store_id=store_id
        ).first()

        if not alert:
            return Response.error_response(
                "ALERT_NOT_FOUND",
                "Alert not found",
                {}
            ), 404

        db.session.delete(alert)
        db.session.commit()

        return Response.success_response(
            message="ALERT_DELETED"
        ), 200
