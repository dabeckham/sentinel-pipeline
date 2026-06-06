from datetime import datetime, timezone
from sqlalchemy import Integer, String, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    class_label: Mapped[str] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    bbox: Mapped[dict] = mapped_column(JSON, nullable=True)   # {x, y, w, h}
    crop_path: Mapped[str] = mapped_column(Text, nullable=True)  # MinIO path
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
