"""add server-side subscription plan state

Revision ID: 016_subscription_plans
Revises: 015_account_security
"""
from alembic import op
import sqlalchemy as sa

revision = "016_subscription_plans"
down_revision = "015_account_security"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stores",
        sa.Column("current_plan", sa.String(length=30), nullable=False, server_default="free"),
    )
    op.add_column(
        "stores",
        sa.Column("subscription_status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column(
        "stores",
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "subscription_changes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("source_plan", sa.String(length=30), nullable=False),
        sa.Column("target_plan", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_changes_store_status",
        "subscription_changes",
        ["store_id", "status"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_subscription_changes_store_status", table_name="subscription_changes")
    op.drop_table("subscription_changes")
    op.drop_column("stores", "current_period_end")
    op.drop_column("stores", "subscription_status")
    op.drop_column("stores", "current_plan")
