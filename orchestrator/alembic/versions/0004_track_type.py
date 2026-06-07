"""Add track_type column for stationary/moving classification.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tracks",
        sa.Column(
            "track_type",
            sa.String(16),
            nullable=True,
            comment="moving | stationary | null (unclassified/legacy)",
        ),
    )
    op.create_index("ix_tracks_track_type", "tracks", ["track_type"])


def downgrade():
    op.drop_index("ix_tracks_track_type", table_name="tracks")
    op.drop_column("tracks", "track_type")
