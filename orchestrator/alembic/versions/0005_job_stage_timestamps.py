"""Add md_complete status and stage timestamp columns to jobs.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL ALTER TYPE ADD VALUE must run outside a transaction block.
    # Alembic wraps migrations in a transaction by default; use COMMIT trick.
    op.execute("COMMIT")
    op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'md_complete' AFTER 'md_processing'")
    op.execute("BEGIN")

    # Stage timestamps — all nullable (older jobs won't have them)
    op.add_column("jobs", sa.Column("md_started_at",   sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("md_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("oc_started_at",   sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("jobs", "oc_started_at")
    op.drop_column("jobs", "md_completed_at")
    op.drop_column("jobs", "md_started_at")
    # PostgreSQL does not support removing enum values — leave jobstatus as-is on downgrade
