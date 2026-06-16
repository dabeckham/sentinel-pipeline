"""Make jobs.file_hash unique so ingest dedup is race-safe.

The watcher's on_created handler and the startup ingest scan both do a
check-then-insert on file_hash in separate sessions. Without a DB-level
unique constraint, a file seen by both in the overlap window produces two
jobs for one file (duplicate tracks/detections). A unique index turns the
losing insert into an IntegrityError, which both paths now catch and treat
as a benign duplicate.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    # Replace the plain btree index with a unique one. Postgres treats NULLs
    # as distinct, so the nullable column still allows multiple NULL hashes.
    op.drop_index("ix_jobs_file_hash", table_name="jobs")
    op.create_index("ix_jobs_file_hash", "jobs", ["file_hash"], unique=True)


def downgrade():
    op.drop_index("ix_jobs_file_hash", table_name="jobs")
    op.create_index("ix_jobs_file_hash", "jobs", ["file_hash"], unique=False)
