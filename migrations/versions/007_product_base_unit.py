"""store inventory quantities in an explicit product base unit"""

from alembic import op
import sqlalchemy as sa


revision = "007_product_base_unit"
down_revision = "006_customer_sale_balances"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "products",
        sa.Column(
            "base_unit",
            sa.String(length=20),
            nullable=False,
            server_default="piece",
        ),
    )

    op.alter_column(
        "products",
        "base_unit",
        server_default=None,
    )


def downgrade():
    op.drop_column("products", "base_unit")
