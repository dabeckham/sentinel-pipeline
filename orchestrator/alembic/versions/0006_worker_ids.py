"""Add md_worker_id and oc_worker_id to jobs table.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("md_worker_id", sa.String(128), nullable=True))
    op.add_column("jobs", sa.Column("oc_worker_id", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("jobs", "oc_worker_id")
    op.drop_column("jobs", "md_worker_id")
