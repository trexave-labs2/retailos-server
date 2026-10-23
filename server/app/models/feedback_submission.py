from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.utils.time import now_utc


class FeedbackSubmission(db.Model):
    """Persistent feedback record for delivery tracking and future admin review."""

    __tablename__ = "feedback_submissions"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_feedback_submission_id"),
        Index("ix_feedback_status_created_at", "status", "created_at"),
        Index("ix_feedback_category_created_at", "category", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    delivery_error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
