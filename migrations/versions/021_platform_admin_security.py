"""add platform-admin identity and append-only platform audit history

Revision ID: 021_platform_admin_security
Revises: 020_merge_prod_meta
"""
from alembic import op
import sqlalchemy as sa


revision = "021_platform_admin_security"
down_revision = "020_merge_prod_meta"
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN is supported directly by SQLite and avoids rebuilding users,
    # which could otherwise discard its existing expression-based email index.
    op.add_column(
        "users",
        sa.Column("platform_role", sa.String(length=30), nullable=True),
    )

    op.create_table(
        "platform_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_audit_logs_created_at", "platform_audit_logs", ["created_at"])
    op.create_index("ix_platform_audit_logs_actor", "platform_audit_logs", ["actor_user_id"])
    op.create_index(
        "uq_platform_audit_single_bootstrap",
        "platform_audit_logs",
        ["action_type"],
        unique=True,
        sqlite_where=sa.text("action_type = 'platform_admin.bootstrap'"),
        postgresql_where=sa.text("action_type = 'platform_admin.bootstrap'"),
    )


def downgrade():
    op.drop_index("uq_platform_audit_single_bootstrap", table_name="platform_audit_logs")
    op.drop_index("ix_platform_audit_logs_actor", table_name="platform_audit_logs")
    op.drop_index("ix_platform_audit_logs_created_at", table_name="platform_audit_logs")
    op.drop_table("platform_audit_logs")
    op.drop_column("users", "platform_role")
