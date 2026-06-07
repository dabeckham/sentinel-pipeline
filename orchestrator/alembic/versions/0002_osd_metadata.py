"""Add OSD metadata fields: camera_name/recorded_at on jobs, started_at/ended_at on tracks

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # jobs — camera name and wall-clock start time from OSD OCR
    op.add_column('jobs', sa.Column('camera_name', sa.String(128), nullable=True))
    op.add_column('jobs', sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_jobs_camera_name', 'jobs', ['camera_name'])

    # tracks — actual wall-clock start/end times interpolated from recorded_at + frame offset
    op.add_column('tracks', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tracks', sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('tracks', 'ended_at')
    op.drop_column('tracks', 'started_at')
    op.drop_index('ix_jobs_camera_name', table_name='jobs')
    op.drop_column('jobs', 'recorded_at')
    op.drop_column('jobs', 'camera_name')
