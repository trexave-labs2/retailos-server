from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Index
from sqlalchemy.orm import Mapped, mapped_column
from server.app.extensions import db
from server.app.models.store import Store

class SubscriptionChange(db.Model):
    __tablename__ = "subscription_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="CASCADE"), nullable=False)
    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)
    source_plan: Mapped[str] = mapped_column(String(30), nullable=False)
    target_plan: Mapped[str] = mapped_column(String(30), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(20), nullable=False, default="month")
    price_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_subscription_changes_store_status", "store_id", "status"),)
