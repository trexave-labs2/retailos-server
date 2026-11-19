from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, event, text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.models.user import User
from server.app.utils.time import now_utc


class PlatformAuditLog(db.Model):
    """Append-only audit events for platform-level administrative actions."""

    __tablename__ = "platform_audit_logs"
    __table_args__ = (
        Index("ix_platform_audit_logs_created_at", "created_at"),
        Index("ix_platform_audit_logs_actor", "actor_user_id"),
        Index(
            "uq_platform_audit_single_bootstrap",
            "action_type",
            unique=True,
            sqlite_where=text("action_type = 'platform_admin.bootstrap'"),
            postgresql_where=text("action_type = 'platform_admin.bootstrap'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


@event.listens_for(PlatformAuditLog, "before_update")
def prevent_platform_audit_update(mapper, connection, target):
    raise ValueError("Platform audit records cannot be modified")


@event.listens_for(PlatformAuditLog, "before_delete")
def prevent_platform_audit_delete(mapper, connection, target):
    raise ValueError("Platform audit records cannot be deleted")
