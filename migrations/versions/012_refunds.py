"""add refund records and returned item history

Revision ID: 012_refunds
Revises: 011_item_categories
"""
from alembic import op
import sqlalchemy as sa


revision = "012_refunds"
down_revision = "011_item_categories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("refund_type", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("payment_method", sa.String(length=20), nullable=False),
        sa.Column("reference", sa.String(length=40), nullable=False),
        sa.Column("client_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="check_refund_amount_positive"),
        sa.CheckConstraint("refund_type IN ('Full', 'Partial')", name="check_refund_type_valid"),
        sa.CheckConstraint(
            "payment_method IN ('Cash', 'Transfer', 'POS')",
            name="check_refund_payment_method_valid",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_refunds_reference"),
        sa.UniqueConstraint("client_transaction_id", name="uq_refunds_client_transaction_id"),
    )
    op.create_index("ix_refunds_store_id", "refunds", ["store_id"], unique=False)
    op.create_index("ix_refunds_sale_id", "refunds", ["sale_id"], unique=False)

    op.create_table(
        "refund_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("refund_id", sa.Integer(), nullable=False),
        sa.Column("sale_item_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_name_at_sale", sa.String(length=20), nullable=True),
        sa.Column("unit_base_quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="check_refund_item_quantity_positive"),
        sa.ForeignKeyConstraint(["refund_id"], ["refunds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sale_item_id"], ["sale_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refund_id", "sale_item_id", name="uq_refund_item_sale_line"),
    )
    op.create_index("ix_refund_items_refund_id", "refund_items", ["refund_id"], unique=False)
    op.create_index("ix_refund_items_sale_item_id", "refund_items", ["sale_item_id"], unique=False)


def downgrade():
    op.drop_index("ix_refund_items_sale_item_id", table_name="refund_items")
    op.drop_index("ix_refund_items_refund_id", table_name="refund_items")
    op.drop_table("refund_items")
    op.drop_index("ix_refunds_sale_id", table_name="refunds")
    op.drop_index("ix_refunds_store_id", table_name="refunds")
    op.drop_table("refunds")
