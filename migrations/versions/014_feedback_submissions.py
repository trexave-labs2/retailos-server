"""add feedback submission records and join existing migration heads

Revision ID: 014_feedback_submissions
Revises: d91f0c2a7b64, 013_audit_history
"""
from alembic import op
import sqlalchemy as sa


revision = "014_feedback_submissions"
down_revision = ("d91f0c2a7b64", "013_audit_history")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedback_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("contact_name", sa.String(length=100), nullable=True),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("delivery_error", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", name="uq_feedback_submission_id"),
    )
    op.create_index("ix_feedback_status_created_at", "feedback_submissions", ["status", "created_at"])
    op.create_index("ix_feedback_category_created_at", "feedback_submissions", ["category", "created_at"])


def downgrade():
    op.drop_index("ix_feedback_category_created_at", table_name="feedback_submissions")
    op.drop_index("ix_feedback_status_created_at", table_name="feedback_submissions")
    op.drop_table("feedback_submissions")
