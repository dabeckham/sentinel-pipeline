"""Add detections.timestamp_ms — the frame's offset into the clip in
milliseconds (frame_index / fps). Lets the UI seek the playback rendition
straight to a detection's moment so moving objects can be scrubbed on the
video timeline instead of per-detection stills.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("detections", sa.Column("timestamp_ms", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("detections", "timestamp_ms")
