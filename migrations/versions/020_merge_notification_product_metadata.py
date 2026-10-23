"""merge automatic notifications and product CSV metadata heads

Revision ID: 020_merge_prod_meta
Revises: 018_automatic_notifications, 019_product_csv_metadata
"""
from alembic import op


revision = "020_merge_prod_meta"
down_revision = ("018_automatic_notifications", "019_product_csv_metadata")
branch_labels = None
depends_on = None


def upgrade():
    # Merge-only migration: both parent migrations have already applied their
    # schema changes. This revision joins their histories without changing schema.
    pass


def downgrade():
    # Alembic cannot safely choose which parent history to retain on downgrade.
    # Downgrade the two parent migrations explicitly if a full rollback is needed.
    pass
