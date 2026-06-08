from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class JobResponse(BaseModel):
    id: int
    file_path: str
    status: str
    camera_name: Optional[str] = None
    recorded_at: Optional[datetime] = None
    created_at: datetime
    # Stage timestamps
    md_started_at:   Optional[datetime] = None
    md_completed_at: Optional[datetime] = None
    oc_started_at:   Optional[datetime] = None
    completed_at:    Optional[datetime] = None
    error_message:   Optional[str] = None
    track_count:     Optional[int] = None   # populated by list endpoint via subquery
    # Worker identity
    md_worker_id:    Optional[str] = None
    oc_worker_id:    Optional[str] = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    page: int
    page_size: int
