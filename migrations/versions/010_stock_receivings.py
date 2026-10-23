"""add stock receiving records

Revision ID: 010_stock_receivings
Revises: d91f0c2a7b64
"""
from alembic import op
import sqlalchemy as sa


revision = "010_stock_receivings"
down_revision = "d91f0c2a7b64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stock_receivings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_name", sa.String(length=20), nullable=False),
        sa.Column("unit_base_quantity", sa.Integer(), nullable=False),
        sa.Column("base_quantity_received", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("supplier_or_source", sa.String(length=160), nullable=True),
        sa.Column("client_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("receiving_date", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="check_stock_receiving_quantity_positive"),
        sa.CheckConstraint("unit_base_quantity > 0", name="check_stock_receiving_unit_base_quantity_positive"),
        sa.CheckConstraint("base_quantity_received > 0", name="check_stock_receiving_base_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="check_stock_receiving_unit_cost_non_negative"),
        sa.CheckConstraint("total_cost >= 0", name="check_stock_receiving_total_cost_non_negative"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["unit_id"], ["product_units.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "client_transaction_id", name="uq_stock_receiving_store_transaction"),
    )


def downgrade():
    op.drop_table("stock_receivings")
