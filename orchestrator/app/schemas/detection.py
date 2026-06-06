from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class DetectionResponse(BaseModel):
    id: int
    track_id: int
    job_id: int
    frame_index: int
    class_label: Optional[str] = None
    confidence: Optional[float] = None
    bbox: Optional[dict] = None
    crop_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DetectionListResponse(BaseModel):
    items: list[DetectionResponse]
    total: int
    page: int
    page_size: int
