"""add feedback support workflow status

Revision ID: 022_admin_feedback_support
Revises: 022_password_reset_tokens
"""
from alembic import op
import sqlalchemy as sa


revision = "022_admin_feedback_support"
down_revision = "022_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "feedback_submissions",
        sa.Column("support_status", sa.String(length=20), nullable=False, server_default="new"),
    )
    op.create_index(
        "ix_feedback_support_status_created_at",
        "feedback_submissions",
        ["support_status", "created_at"],
    )


def downgrade():
    op.drop_index("ix_feedback_support_status_created_at", table_name="feedback_submissions")
    op.drop_column("feedback_submissions", "support_status")
