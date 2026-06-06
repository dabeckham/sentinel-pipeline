import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    md_processing = "md_processing"
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
