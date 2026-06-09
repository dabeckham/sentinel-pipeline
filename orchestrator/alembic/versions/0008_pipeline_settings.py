"""Add pipeline_settings key-value table for persistent orchestrator state.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pipeline_settings",
        sa.Column("key",   sa.String(), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(),   nullable=True),
    )


def downgrade():
    op.drop_table("pipeline_settings")
