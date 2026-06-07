"""Add snapshot_bbox to tracks table for best-frame bbox storage.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tracks",
        sa.Column("snapshot_bbox", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("tracks", "snapshot_bbox")
