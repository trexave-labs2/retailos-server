"""add session version for password-change invalidation

Revision ID: 015_account_security
Revises: 014_feedback_submissions
"""
from alembic import op
import sqlalchemy as sa

revision = "015_account_security"
down_revision = "014_feedback_submissions"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="0", nullable=False),
    )

def downgrade():
    op.drop_column("users", "session_version")
