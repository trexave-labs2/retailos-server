"""record base quantity deducted on sale lines"""

from alembic import op
import sqlalchemy as sa


revision = "009_sale_base_quantity_deducted"
down_revision = "008_product_units"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sale_items",
        sa.Column(
            "base_quantity_deducted",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE sale_items
            SET base_quantity_deducted = quantity * unit_base_quantity
            """
        )
    )

    op.alter_column(
        "sale_items",
        "base_quantity_deducted",
        server_default=None,
    )

    op.create_check_constraint(
        "check_sale_item_base_quantity_deducted_positive",
        "sale_items",
        "base_quantity_deducted > 0",
    )


def downgrade():
    op.drop_constraint(
        "check_sale_item_base_quantity_deducted_positive",
        "sale_items",
        type_="check",
    )
    op.drop_column("sale_items", "base_quantity_deducted")
