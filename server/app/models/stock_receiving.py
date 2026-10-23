from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.product import Product
from server.app.models.product_unit import ProductUnit
from server.app.models.store import Store
from server.app.models.user import User
from server.app.utils.time import now_utc


class StockReceiving(db.Model):
    __tablename__ = "stock_receivings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey(Store.id, ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey(Product.id, ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL")
    )
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey(ProductUnit.id, ondelete="SET NULL")
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_name: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_base_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    base_quantity_received: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    supplier_or_source: Mapped[str | None] = mapped_column(String(160))
    client_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    receiving_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_stock_receiving_quantity_positive"),
        CheckConstraint("unit_base_quantity > 0", name="check_stock_receiving_unit_base_quantity_positive"),
        CheckConstraint("base_quantity_received > 0", name="check_stock_receiving_base_quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="check_stock_receiving_unit_cost_non_negative"),
        CheckConstraint("total_cost >= 0", name="check_stock_receiving_total_cost_non_negative"),
        UniqueConstraint(
            "store_id",
            "client_transaction_id",
            name="uq_stock_receiving_store_transaction",
        ),
    )
