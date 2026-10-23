"""persist trial state and requested billing interval

Revision ID: 017_subscription_trials_billing
Revises: 016_subscription_plans
"""
from alembic import op
import sqlalchemy as sa

revision = "017_subscription_trials_billing"
down_revision = "016_subscription_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stores",
        sa.Column("trial_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column("sales_limit_grace_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscription_changes",
        sa.Column("billing_interval", sa.String(length=20), nullable=False, server_default="month"),
    )
    op.add_column(
        "subscription_changes",
        sa.Column("price_amount", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("subscription_changes", "price_amount")
    op.drop_column("subscription_changes", "billing_interval")
    op.drop_column("stores", "sales_limit_grace_started_at")
    op.drop_column("stores", "trial_used_at")
