"""Add jobs.snapshot_path (one representative image per clip) and
jobs.source_deleted (the source video has been purged; fall back to the snap).

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("snapshot_path", sa.String(length=256), nullable=True))
    op.add_column("jobs", sa.Column("source_deleted", sa.Boolean(),
                                    server_default=sa.text("false"), nullable=False))


def downgrade():
    op.drop_column("jobs", "source_deleted")
    op.drop_column("jobs", "snapshot_path")
