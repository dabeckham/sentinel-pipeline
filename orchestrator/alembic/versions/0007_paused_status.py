"""Add 'paused' value to jobstatus enum.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres requires ALTER TYPE to add enum values
    op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'paused'")


def downgrade():
    # Removing an enum value in Postgres requires recreating the type —
    # skip for safety; a full rollback would need manual intervention.
    pass
