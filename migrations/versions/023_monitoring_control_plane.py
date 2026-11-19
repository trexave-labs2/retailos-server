"""add persistent platform monitoring control-plane records

Revision ID: 023_monitoring_control_plane
Revises: 022_admin_feedback_support
"""
from alembic import op
import sqlalchemy as sa


revision = "023_monitoring_control_plane"
down_revision = "022_admin_feedback_support"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monitors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("current_status", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_response_ms", sa.Integer(), nullable=True),
        sa.Column("latest_status_code", sa.Integer(), nullable=True),
        sa.Column("latest_error", sa.String(length=120), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_url", name="uq_monitors_target_url"),
    )
    op.create_index("ix_monitors_enabled_next_check", "monitors", ["enabled", "next_check_at"])
    op.create_index("ix_monitors_status", "monitors", ["current_status"])

    op.create_table(
        "monitor_checks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_monitor_checks_monitor_checked", "monitor_checks", ["monitor_id", "checked_at"])
    op.create_index("ix_monitor_checks_status_checked", "monitor_checks", ["status", "checked_at"])

    op.create_table(
        "monitor_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latest_error", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_monitor_incidents_monitor_opened", "monitor_incidents", ["monitor_id", "opened_at"])
    op.create_index("ix_monitor_incidents_status_opened", "monitor_incidents", ["status", "opened_at"])
    op.create_index(
        "uq_monitor_incidents_one_open_per_monitor", "monitor_incidents", ["monitor_id"],
        unique=True, sqlite_where=sa.text("status = 'open'"),
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade():
    op.drop_index("uq_monitor_incidents_one_open_per_monitor", table_name="monitor_incidents")
    op.drop_index("ix_monitor_incidents_status_opened", table_name="monitor_incidents")
    op.drop_index("ix_monitor_incidents_monitor_opened", table_name="monitor_incidents")
    op.drop_table("monitor_incidents")
    op.drop_index("ix_monitor_checks_status_checked", table_name="monitor_checks")
    op.drop_index("ix_monitor_checks_monitor_checked", table_name="monitor_checks")
    op.drop_table("monitor_checks")
    op.drop_index("ix_monitors_status", table_name="monitors")
    op.drop_index("ix_monitors_enabled_next_check", table_name="monitors")
    op.drop_table("monitors")
