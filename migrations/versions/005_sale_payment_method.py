"""add validated payment method to sales"""

from alembic import op
import sqlalchemy as sa


revision = "005_sale_payment_method"
down_revision = "004_sale_timestamp_utc"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sales",
        sa.Column(
            "payment_method",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.execute(
        "UPDATE sales SET payment_method = 'Cash' "
        "WHERE payment_method IS NULL"
    )

    op.alter_column(
        "sales",
        "payment_method",
        nullable=False,
    )

    op.create_check_constraint(
        "check_sale_payment_method_valid",
        "sales",
        "payment_method IN ('Cash', 'Transfer', 'POS')",
    )


def downgrade():
    op.drop_constraint(
        "check_sale_payment_method_valid",
        "sales",
        type_="check",
    )
    op.drop_column("sales", "payment_method")
