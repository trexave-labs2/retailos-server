# server/app/models/alert.py
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.product import Product
from server.app.models.store import Store
from server.app.utils.time import now_utc


class AlertType(enum.Enum):
    LOW_STOCK = "low_stock"


class Alert(db.Model):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey(Store.id, ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey(Product.id, ondelete="SET NULL")
    )
    type: Mapped[AlertType] = mapped_column(
        Enum(
            AlertType,
            values_callable=lambda alert_types: [
                alert_type.value for alert_type in alert_types
            ],
            native_enum=False,
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(String, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    condition_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('low_stock')",
            name="check_alert_type_valid",
        ),
        CheckConstraint(
            "is_resolved IN (TRUE, FALSE)",
            name="check_alert_is_resolved_valid",
        ),
        CheckConstraint(
            "condition_active IN (TRUE, FALSE)",
            name="check_alert_condition_active_valid",
        ),
        Index(
            "uq_alerts_active_condition",
            "store_id",
            "product_id",
            "type",
            unique=True,
            postgresql_where=text("condition_active = TRUE AND product_id IS NOT NULL"),
            sqlite_where=text("condition_active = TRUE AND product_id IS NOT NULL"),
        ),
    )
