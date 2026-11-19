"""create hashed, one-time password reset tokens

Revision ID: 022_password_reset_tokens
Revises: 021_platform_admin_security
"""
from alembic import op
import sqlalchemy as sa


revision = "022_password_reset_tokens"
down_revision = "021_platform_admin_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index(
        "ix_password_reset_user_created",
        "password_reset_tokens",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_password_reset_expiry",
        "password_reset_tokens",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_password_reset_expiry", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_user_created", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
