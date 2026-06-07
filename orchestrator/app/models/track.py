from datetime import datetime, timezone
from sqlalchemy import Integer, String, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)  # ByteTrack assigned ID
    class_label: Mapped[str] = mapped_column(String(128), nullable=True, index=True)
    confidence_max: Mapped[float] = mapped_column(Float, nullable=True)
    first_frame: Mapped[int] = mapped_column(Integer, nullable=True)
    last_frame: Mapped[int] = mapped_column(Integer, nullable=True)
    snapshot_path: Mapped[str] = mapped_column(Text, nullable=True)  # MinIO path
    snapshot_bbox: Mapped[dict] = mapped_column(JSON, nullable=True)  # bbox from best-shot frame
    track_type: Mapped[str] = mapped_column(String(16), nullable=True, index=True)  # moving | stationary
    # Wall-clock timestamps derived from OSD recorded_at + frame offset
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
