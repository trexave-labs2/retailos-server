"""add physical and service product types"""

from alembic import op
import sqlalchemy as sa


revision = "004_product_type"
down_revision = "003_model_constraints"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "products",
        sa.Column(
            "product_type",
            sa.String(length=20),
            nullable=False,
            server_default="Physical",
        ),
    )
    op.create_check_constraint(
        "check_product_type_valid",
        "products",
        "product_type IN ('Physical', 'Service')",
    )
    op.alter_column(
        "products",
        "product_type",
        server_default=None,
    )


def downgrade():
    op.drop_constraint(
        "check_product_type_valid",
        "products",
        type_="check",
    )
    op.drop_column("products", "product_type")
