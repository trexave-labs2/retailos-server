"""add product units with independent prices"""

from alembic import op
import sqlalchemy as sa


revision = "008_product_units"
down_revision = "007_product_base_unit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("base_quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.CheckConstraint(
            "base_quantity > 0",
            name="check_product_unit_base_quantity_positive",
        ),
        sa.CheckConstraint(
            "price >= 0",
            name="check_product_unit_price_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "name",
            name="uq_product_unit_name",
        ),
    )

    # Existing products keep their current base-unit price as their first unit.
    op.execute(
        sa.text(
            """
            INSERT INTO product_units (product_id, name, base_quantity, price)
            SELECT id, base_unit, 1, price
            FROM products
            """
        )
    )

    op.add_column(
        "sale_items",
        sa.Column("unit_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sale_items",
        sa.Column("unit_name_at_sale", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "sale_items",
        sa.Column("unit_base_quantity", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_foreign_key(
        "fk_sale_items_unit_id",
        "sale_items",
        "product_units",
        ["unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "sale_items",
        "unit_base_quantity",
        server_default=None,
    )
    op.create_check_constraint(
        "check_sale_item_unit_base_quantity_positive",
        "sale_items",
        "unit_base_quantity > 0",
    )


def downgrade():
    op.drop_constraint(
        "check_sale_item_unit_base_quantity_positive",
        "sale_items",
        type_="check",
    )
    op.drop_constraint("fk_sale_items_unit_id", "sale_items", type_="foreignkey")
    op.drop_column("sale_items", "unit_base_quantity")
    op.drop_column("sale_items", "unit_name_at_sale")
    op.drop_column("sale_items", "unit_id")
    op.drop_table("product_units")
