# server\\app\\models\\store.py
from datetime import datetime
from server.app.extensions import db
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, ForeignKey, DateTime, Integer
from server.app.models.user import User
from server.app.utils.time import now_utc

class Store(db.Model):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String)
    current_plan: Mapped[str] = mapped_column(String(30), nullable=False, default="free", server_default="free")
    subscription_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sales_limit_grace_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
