# server/app/services/alert_service.py
from flask import request
from sqlalchemy import or_

from server.app.extensions import db
from server.app.models.alert import Alert, AlertType
from server.app.models.product import Product
from server.app.utils.response import Response
from server.app.utils.store_authorization import get_authorized_store
from server.app.utils.time import now_utc


class AlertService:
    @staticmethod
    def sync_low_stock_alert(product):
        """Persist one alert per low-stock episode and close it when stock recovers."""
        is_low_stock = (
            product.product_type == "Physical"
            and product.low_stock_threshold is not None
            and product.stock_quantity <= product.low_stock_threshold
        )

        active_condition = Alert.query.filter_by(
            store_id=product.store_id,
            product_id=product.id,
            type=AlertType.LOW_STOCK,
            condition_active=True,
        ).first()

        if is_low_stock:
            if active_condition:
                # This includes dismissed alerts: dismissal must not cause the same
                # still-active business condition to generate another notification.
                return False

            db.session.add(
                Alert(
                    store_id=product.store_id,
                    product_id=product.id,
                    type=AlertType.LOW_STOCK,
                    message=f"{product.name} is low on stock",
                    condition_active=True,
                )
            )
            return True

        if active_condition:
            active_condition.condition_active = False
            active_condition.is_resolved = True
        return False

    def list_alerts(self):
        store_id = request.args.get("store_id", type=int)
        if not get_authorized_store(store_id):
            return Response.error_response(
                "STORE_ACCESS_DENIED",
                "You do not have access to this store",
                {},
            ), 403

        alerts = Alert.query.outerjoin(
            Product, Alert.product_id == Product.id
        ).filter(
            Alert.store_id == store_id,
            Alert.is_resolved.is_(False),
            or_(
                Alert.product_id.is_(None),
                Product.product_type == "Physical",
            ),
        ).order_by(Alert.created_at.desc(), Alert.id.desc()).all()

        return Response.success_response(
            {
                "alerts": [
                    {
                        "id": alert.id,
                        "type": alert.type.value,
                        "message": alert.message,
                        "product_id": alert.product_id,
                        "is_read": alert.read_at is not None,
                        "read_at": alert.read_at.isoformat() if alert.read_at else None,
                        "created_at": alert.created_at.isoformat(),
                    }
                    for alert in alerts
                ]
            },
            "ALERTS_RETRIEVED",
        ), 200

    def _authorized_alert(self):
        store_id = request.args.get("store_id", type=int)
        alert_id = request.args.get("id", type=int)

        if not get_authorized_store(store_id):
            return None, (
                Response.error_response(
                    "STORE_ACCESS_DENIED",
                    "You do not have access to this store",
                    {},
                ),
                403,
            )

        if not isinstance(alert_id, int) or alert_id <= 0:
            return None, (
                Response.error_response(
                    "VALIDATION_ERROR",
                    "A valid alert ID is required",
                    {"id": "Alert ID must be a positive integer"},
                ),
                400,
            )

        alert = Alert.query.filter_by(id=alert_id, store_id=store_id).first()
        if not alert:
            return None, (
                Response.error_response("ALERT_NOT_FOUND", "Alert not found", {}),
                404,
            )
        return alert, None

    def mark_alert_read(self):
        alert, error = self._authorized_alert()
        if error:
            return error

        if alert.read_at is None:
            alert.read_at = now_utc()
            db.session.commit()

        return Response.success_response(
            {"alert": {"id": alert.id, "is_read": True, "read_at": alert.read_at.isoformat()}},
            "ALERT_MARKED_READ",
        ), 200

    def dismiss_alert(self):
        alert, error = self._authorized_alert()
        if error:
            return error

        alert.is_resolved = True
        if alert.dismissed_at is None:
            alert.dismissed_at = now_utc()
        # condition_active intentionally stays true until the stock condition ends.
        # Repeated stock changes during the same episode therefore cannot spam alerts.
        db.session.commit()

        return Response.success_response(
            {"alert": {"id": alert.id, "is_dismissed": True}},
            "ALERT_DISMISSED",
        ), 200

    def resolve_alert(self):
        """Compatibility alias for existing clients; resolution is a dismissal."""
        return self.dismiss_alert()

    def create_low_stock_alerts(self, store_id, product_ids=None):
        """Synchronize qualifying products inside an existing business transaction."""
        query = Product.query.filter_by(store_id=store_id, product_type="Physical")
        if product_ids:
            query = query.filter(Product.id.in_(product_ids))

        created_product_ids = []
        for product in query.all():
            if self.sync_low_stock_alert(product):
                created_product_ids.append(product.id)
        return created_product_ids
