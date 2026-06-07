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
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    # Joined from jobs table
    camera_name: Optional[str] = None
    # Computed
    detection_count: Optional[int] = None

    model_config = {"from_attributes": True}


class TrackListResponse(BaseModel):
    items: list[TrackResponse]
    total: int
    page: int
    page_size: int


class DetectionInTrack(BaseModel):
    id: int
    frame_index: int
    class_label: Optional[str] = None
    confidence: Optional[float] = None
    bbox: Optional[dict] = None
    crop_path: Optional[str] = None

    model_config = {"from_attributes": True}


class TrackDetailResponse(TrackResponse):
    detections: list[DetectionInTrack] = []
