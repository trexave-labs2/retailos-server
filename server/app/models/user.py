from datetime import datetime
from server.app.extensions import db
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime, Integer, Index, CheckConstraint, func
from server.app.utils.time import now_utc

PLATFORM_ADMIN_ROLE = "admin"
SUPER_ADMIN_ROLE = "super_admin"
PLATFORM_ADMIN_ROLES = (PLATFORM_ADMIN_ROLE, SUPER_ADMIN_ROLE)


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    platform_role: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    account_status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    __table_args__ = (
        CheckConstraint(
            "account_status IN ('active', 'pending_deletion', 'anonymized')",
            name="check_users_account_status",
        ),
        Index("uq_users_email_lower", func.lower(email), unique=True),
        Index("ix_users_account_deletion_schedule", "account_status", "deletion_scheduled_for"),
    )
