"""persist notification read state and enforce one alert per active condition

Revision ID: 018_automatic_notifications
Revises: 017_subscription_trials_billing
"""
from alembic import op
import sqlalchemy as sa


revision = "018_automatic_notifications"
down_revision = "017_subscription_trials_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "alerts",
        sa.Column("condition_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("alerts", sa.Column("read_at", sa.DateTime(), nullable=True))
    op.add_column("alerts", sa.Column("dismissed_at", sa.DateTime(), nullable=True))

    # Preserve one active alert per product/type before creating the unique index.
    op.execute(sa.text(
        """
        UPDATE alerts
        SET is_resolved = TRUE
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY store_id, product_id, type
                           ORDER BY id
                       ) AS row_number
                FROM alerts
                WHERE is_resolved = FALSE AND product_id IS NOT NULL
            ) ranked
            WHERE row_number > 1
        )
        """
    ))
    op.execute(sa.text(
        "UPDATE alerts SET condition_active = CASE WHEN is_resolved = FALSE THEN TRUE ELSE FALSE END"
    ))
    op.create_check_constraint(
        "check_alert_condition_active_valid",
        "alerts",
        "condition_active IN (TRUE, FALSE)",
    )
    op.create_index(
        "uq_alerts_active_condition",
        "alerts",
        ["store_id", "product_id", "type"],
        unique=True,
        postgresql_where=sa.text("condition_active = TRUE AND product_id IS NOT NULL"),
        sqlite_where=sa.text("condition_active = TRUE AND product_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_alerts_active_condition", table_name="alerts")
    op.drop_constraint("check_alert_condition_active_valid", "alerts", type_="check")
    op.drop_column("alerts", "dismissed_at")
    op.drop_column("alerts", "read_at")
    op.drop_column("alerts", "condition_active")
