"""add SKU, barcode, and cost price to products

Revision ID: 019_product_csv_metadata
Revises: d91f0c2a7b64
"""
from alembic import op
import sqlalchemy as sa


revision = "019_product_csv_metadata"
down_revision = "d91f0c2a7b64"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("sku", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("barcode", sa.String(length=128), nullable=True))
    op.add_column("products", sa.Column("cost_price", sa.Numeric(10, 2), nullable=True))
    op.create_check_constraint(
        "check_cost_price_non_negative",
        "products",
        "cost_price IS NULL OR cost_price >= 0",
    )
    op.create_index(
        "uq_products_store_sku_ci",
        "products",
        ["store_id", sa.text("lower(sku)")],
        unique=True,
        sqlite_where=sa.text("sku IS NOT NULL"),
        postgresql_where=sa.text("sku IS NOT NULL"),
    )
    op.create_index(
        "uq_products_store_barcode",
        "products",
        ["store_id", "barcode"],
        unique=True,
        sqlite_where=sa.text("barcode IS NOT NULL"),
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_products_store_barcode", table_name="products")
    op.drop_index("uq_products_store_sku_ci", table_name="products")
    op.drop_constraint("check_cost_price_non_negative", "products", type_="check")
    op.drop_column("products", "cost_price")
    op.drop_column("products", "barcode")
    op.drop_column("products", "sku")
