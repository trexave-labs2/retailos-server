from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.sale import Sale
from server.app.models.store import Store
from server.app.models.user import User
from server.app.utils.time import now_utc


class Refund(db.Model):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount > 0", name="check_refund_amount_positive"),
        CheckConstraint("refund_type IN ('Full', 'Partial')", name="check_refund_type_valid"),
        CheckConstraint(
            "payment_method IN ('Cash', 'Transfer', 'POS')",
            name="check_refund_payment_method_valid",
        ),
        UniqueConstraint("reference", name="uq_refunds_reference"),
        UniqueConstraint("client_transaction_id", name="uq_refunds_client_transaction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="CASCADE"), nullable=False, index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey(Sale.id, ondelete="RESTRICT"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey(User.id, ondelete="SET NULL"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    refund_type: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    reference: Mapped[str] = mapped_column(String(40), nullable=False)
    client_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class RefundItem(db.Model):
    __tablename__ = "refund_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_refund_item_quantity_positive"),
        UniqueConstraint("refund_id", "sale_item_id", name="uq_refund_item_sale_line"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey(Refund.id, ondelete="CASCADE"), nullable=False, index=True)
    sale_item_id: Mapped[int] = mapped_column(ForeignKey("sale_items.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_name_at_sale: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unit_base_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
