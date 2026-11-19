"""add staged account and store deletion state

Revision ID: 023_account_store_deletion
Revises: 022_admin_feedback_support
"""
from alembic import op
import sqlalchemy as sa


revision = "023_account_store_deletion"
down_revision = "022_admin_feedback_support"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("account_status", sa.String(length=24), nullable=False, server_default="active"),
    )
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deletion_scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "check_users_account_status",
        "users",
        "account_status IN ('active', 'pending_deletion', 'anonymized')",
    )
    op.create_index(
        "ix_users_account_deletion_schedule",
        "users",
        ["account_status", "deletion_scheduled_for"],
    )

    op.add_column(
        "stores",
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
    )
    op.add_column("stores", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stores", sa.Column("deletion_scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stores", sa.Column("deletion_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "check_stores_status",
        "stores",
        "status IN ('active', 'pending_deletion', 'deleted')",
    )
    op.create_index(
        "ix_stores_deletion_schedule",
        "stores",
        ["status", "deletion_scheduled_for"],
    )


def downgrade():
    op.drop_index("ix_stores_deletion_schedule", table_name="stores")
    op.drop_constraint("check_stores_status", "stores", type_="check")
    op.drop_column("stores", "deletion_completed_at")
    op.drop_column("stores", "deletion_scheduled_for")
    op.drop_column("stores", "deletion_requested_at")
    op.drop_column("stores", "status")

    op.drop_index("ix_users_account_deletion_schedule", table_name="users")
    op.drop_constraint("check_users_account_status", "users", type_="check")
    op.drop_column("users", "anonymized_at")
    op.drop_column("users", "deletion_scheduled_for")
    op.drop_column("users", "deletion_requested_at")
    op.drop_column("users", "account_status")
