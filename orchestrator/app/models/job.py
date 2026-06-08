import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    paused = "paused"             # manually held; will not be processed until resumed
    md_processing = "md_processing"
    md_complete = "md_complete"   # MD finished queuing frames; waiting for OC
    oc_processing = "oc_processing"
    completed = "completed"
    failed = "failed"
    duplicate = "duplicate"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.pending, nullable=False, index=True
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    # OSD metadata extracted from first frame via OCR (may be None if OSD not found)
    camera_name: Mapped[str] = mapped_column(String(128), nullable=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Stage timestamps — populated as each phase begins/ends
    md_started_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    md_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    oc_started_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    # Worker identity — hostname-type-pid of the worker that handled each stage
    md_worker_id:    Mapped[str] = mapped_column(String(128), nullable=True)
    oc_worker_id:    Mapped[str] = mapped_column(String(128), nullable=True)
