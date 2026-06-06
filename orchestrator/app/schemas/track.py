from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TrackResponse(BaseModel):
    id: int
    job_id: int
    track_id: int
    class_label: Optional[str] = None
    confidence_max: Optional[float] = None
    first_frame: Optional[int] = None
    last_frame: Optional[int] = None
    snapshot_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TrackListResponse(BaseModel):
    items: list[TrackResponse]
    total: int
    page: int
    page_size: int
