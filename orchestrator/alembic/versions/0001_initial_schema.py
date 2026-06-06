"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # jobs
    op.create_table('jobs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=True),
        sa.Column('source_path', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('pending','queued','md_processing','oc_processing',
                                    'completed','failed','duplicate', name='jobstatus'),
                  nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_jobs_file_hash', 'jobs', ['file_hash'])
    op.create_index('ix_jobs_status', 'jobs', ['status'])

    # users
    op.create_table('users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', sa.Enum('admin','operator','viewer', name='userrole'),
                  nullable=False, server_default='viewer'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_users_username', 'users', ['username'])

    # config
    op.create_table('config',
        sa.Column('key', sa.String(128), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('key'),
    )

    # workers
    op.create_table('workers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('worker_id', sa.String(64), nullable=False),
        sa.Column('type', sa.Enum('md','oc', name='workertype'), nullable=False),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('queue_name', sa.String(128), nullable=False),
        sa.Column('status', sa.Enum('online','offline','busy', name='workerstatus'),
                  nullable=False, server_default='offline'),
        sa.Column('model_version', sa.String(64), nullable=True),
        sa.Column('gpu_id', sa.String(16), nullable=True),
        sa.Column('registered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('worker_id'),
    )
    op.create_index('ix_workers_worker_id', 'workers', ['worker_id'])

    # motion_events
    op.create_table('motion_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('frame_index', sa.Integer(), nullable=False),
        sa.Column('timestamp_ms', sa.Integer(), nullable=False),
        sa.Column('bounding_boxes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_motion_events_job_id', 'motion_events', ['job_id'])

    # tracks
    op.create_table('tracks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('class_label', sa.String(128), nullable=True),
        sa.Column('confidence_max', sa.Float(), nullable=True),
        sa.Column('first_frame', sa.Integer(), nullable=True),
        sa.Column('last_frame', sa.Integer(), nullable=True),
        sa.Column('snapshot_path', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tracks_job_id', 'tracks', ['job_id'])
    op.create_index('ix_tracks_class_label', 'tracks', ['class_label'])

    # detections
    op.create_table('detections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('frame_index', sa.Integer(), nullable=False),
        sa.Column('class_label', sa.String(128), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('bbox', sa.JSON(), nullable=True),
        sa.Column('crop_path', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_detections_track_id', 'detections', ['track_id'])
    op.create_index('ix_detections_job_id', 'detections', ['job_id'])

    # Seed default config values
    op.execute("""
        INSERT INTO config (key, value, updated_at) VALUES
        ('lan_trust_enabled', 'false', NOW()),
        ('lan_trust_cidrs', '192.168.1.0/24', NOW()),
        ('ingest_recurse', 'true', NOW()),
        ('ingest_poll_interval', '10', NOW())
    """)


def downgrade() -> None:
    op.drop_table('detections')
    op.drop_table('tracks')
    op.drop_table('motion_events')
    op.drop_table('workers')
    op.drop_table('config')
    op.drop_table('users')
    op.drop_table('jobs')
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS userrole")
    op.execute("DROP TYPE IF EXISTS workertype")
    op.execute("DROP TYPE IF EXISTS workerstatus")
