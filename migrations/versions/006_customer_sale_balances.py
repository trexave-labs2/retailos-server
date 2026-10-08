"""add sale payment fields and customer outstanding balances"""

from alembic import op
import sqlalchemy as sa


revision = "006_customer_sale_balances"
down_revision = "005_sale_payment_method"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sales",
        sa.Column(
            "amount_paid",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "sales",
        sa.Column(
            "balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )

    op.execute(
        "UPDATE sales SET amount_paid = total_amount, balance = 0"
    )

    op.alter_column(
        "sales",
        "amount_paid",
        server_default=None,
    )
    op.alter_column(
        "sales",
        "balance",
        server_default=None,
    )

    op.add_column(
        "customers",
        sa.Column(
            "outstanding_balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )

    op.execute(
        """
        UPDATE customers
        SET outstanding_balance = (
            SELECT COALESCE(SUM(sales.balance), 0)
            FROM sales
            WHERE sales.customer_id = customers.id
        )
        """
    )

    op.alter_column(
        "customers",
        "outstanding_balance",
        server_default=None,
    )

    op.create_check_constraint(
        "check_sale_amount_paid_non_negative",
        "sales",
        "amount_paid >= 0",
    )
    op.create_check_constraint(
        "check_sale_balance_non_negative",
        "sales",
        "balance >= 0",
    )
    op.create_check_constraint(
        "check_sale_amount_paid_not_above_total",
        "sales",
        "amount_paid <= total_amount",
    )
    op.create_check_constraint(
        "check_customer_outstanding_balance_non_negative",
        "customers",
        "outstanding_balance >= 0",
    )


def downgrade():
    op.drop_constraint(
        "check_customer_outstanding_balance_non_negative",
        "customers",
        type_="check",
    )
    op.drop_constraint(
        "check_sale_amount_paid_not_above_total",
        "sales",
        type_="check",
    )
    op.drop_constraint(
        "check_sale_balance_non_negative",
        "sales",
        type_="check",
    )
    op.drop_constraint(
        "check_sale_amount_paid_non_negative",
        "sales",
        type_="check",
    )
    op.drop_column("customers", "outstanding_balance")
    op.drop_column("sales", "balance")
    op.drop_column("sales", "amount_paid")
