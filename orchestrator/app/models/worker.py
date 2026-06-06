import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class WorkerType(str, enum.Enum):
    md = "md"
    oc = "oc"


class WorkerStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    busy = "busy"


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    worker_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    type: Mapped[WorkerType] = mapped_column(Enum(WorkerType), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[WorkerStatus] = mapped_column(
        Enum(WorkerStatus), default=WorkerStatus.offline, nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=True)
    gpu_id: Mapped[str] = mapped_column(String(16), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
