from datetime import datetime
from decimal import Decimal

from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.customer import Customer
from server.app.utils.time import ensure_utc, is_future
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy import CheckConstraint, ForeignKey, DateTime, Integer, Numeric, String


class Sale(db.Model):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint(
            "payment_method IN ('Cash', 'Transfer', 'POS')",
            name="check_sale_payment_method_valid",
        ),
        CheckConstraint(
            "amount_paid >= 0",
            name="check_sale_amount_paid_non_negative",
        ),
        CheckConstraint(
            "balance >= 0",
            name="check_sale_balance_non_negative",
        ),
        CheckConstraint(
            "amount_paid <= total_amount",
            name="check_sale_amount_paid_not_above_total",
        ),
    )

    PAYMENT_METHODS = ("Cash", "Transfer", "POS")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="CASCADE"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey(Customer.id, ondelete="SET NULL"))
    client_transaction_id: Mapped[str | None] = mapped_column(String, unique=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ensure_utc)

    @validates("payment_method")
    def validate_payment_method(self, key, value):
        if value not in self.PAYMENT_METHODS:
            raise ValueError(
                "Payment method must be one of: Cash, Transfer, POS"
            )

        return value

    @validates("created_at")
    def validate_created_at(self, key, value):
        value = ensure_utc(value)

        if is_future(value):
            raise ValueError("Sale timestamp cannot be in the future")

        return value
