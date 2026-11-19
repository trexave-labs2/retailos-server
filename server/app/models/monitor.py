from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.extensions import db
from server.app.utils.time import now_utc


class Monitor(db.Model):
    __tablename__ = "monitors"
    __table_args__ = (
        UniqueConstraint("target_url", name="uq_monitors_target_url"),
        Index("ix_monitors_enabled_next_check", "enabled", "next_check_at"),
        Index("ix_monitors_status", "current_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600, server_default="600")
    current_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown", server_default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class MonitorCheck(db.Model):
    __tablename__ = "monitor_checks"
    __table_args__ = (
        Index("ix_monitor_checks_monitor_checked", "monitor_id", "checked_at"),
        Index("ix_monitor_checks_status_checked", "status", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitor_id: Mapped[int] = mapped_column(db.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)


class MonitorIncident(db.Model):
    __tablename__ = "monitor_incidents"
    __table_args__ = (
        Index("ix_monitor_incidents_monitor_opened", "monitor_id", "opened_at"),
        Index("ix_monitor_incidents_status_opened", "status", "opened_at"),
        Index(
            "uq_monitor_incidents_one_open_per_monitor", "monitor_id", unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    monitor_id: Mapped[int] = mapped_column(db.ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    latest_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
