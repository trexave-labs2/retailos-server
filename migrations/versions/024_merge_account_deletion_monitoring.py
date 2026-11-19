"""merge account deletion and platform monitoring migration heads

Revision ID: 024_merge_monitoring
Revises: 023_account_store_deletion, 023_monitoring_control_plane
"""

revision = "024_merge_monitoring"
down_revision = ("023_account_store_deletion", "023_monitoring_control_plane")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
