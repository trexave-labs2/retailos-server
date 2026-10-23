"""add immutable audit history

Revision ID: 013_audit_history
Revises: 012_refunds
"""
from alembic import op
import sqlalchemy as sa


revision = "013_audit_history"
down_revision = "012_refunds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("before_values", sa.JSON(), nullable=True),
        sa.Column("after_values", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_store_created_at", "audit_logs", ["store_id", "created_at"])
    op.create_index("ix_audit_logs_store_action", "audit_logs", ["store_id", "action_type"])
    op.create_index("ix_audit_logs_store_user", "audit_logs", ["store_id", "user_id"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])


def downgrade():
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_store_user", table_name="audit_logs")
    op.drop_index("ix_audit_logs_store_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_store_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
