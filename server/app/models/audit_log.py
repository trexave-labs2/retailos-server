from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, event
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.store import Store
from server.app.models.user import User
from server.app.utils.time import now_utc


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_store_created_at", "store_id", "created_at"),
        Index("ix_audit_logs_store_action", "store_id", "action_type"),
        Index("ix_audit_logs_store_user", "store_id", "user_id"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey(Store.id, ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey(User.id, ondelete="SET NULL"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


@event.listens_for(AuditLog, "before_update")
def prevent_audit_update(mapper, connection, target):
    raise ValueError("Audit records cannot be modified")


@event.listens_for(AuditLog, "before_delete")
def prevent_audit_delete(mapper, connection, target):
    raise ValueError("Audit records cannot be deleted")
