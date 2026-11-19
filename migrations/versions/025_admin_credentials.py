"""Add independent platform-admin credentials and session version.

Revision ID: 025_admin_credentials
Revises: 024_merge_monitoring
"""
from alembic import op
import sqlalchemy as sa

revision = "025_admin_credentials"
down_revision = "024_merge_monitoring"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("admin_password_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("admin_session_version", sa.Integer(), server_default="0", nullable=False))
    # Preserve access for already-provisioned administrators. Their first Admin
    # password is migrated from the former shared password and can then be
    # changed independently without touching their RetailOS Client password.
    op.execute("UPDATE users SET admin_password_hash = password_hash WHERE platform_role IN ('admin', 'super_admin')")


def downgrade():
    op.drop_column("users", "admin_session_version")
    op.drop_column("users", "admin_password_hash")
