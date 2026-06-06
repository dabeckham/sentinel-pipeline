from pydantic import BaseModel


class ClassCount(BaseModel):
    class_label: str
    count: int
    avg_confidence: float


class StatsResponse(BaseModel):
    jobs_total: int
    jobs_completed: int
    jobs_queued: int
    jobs_processing: int
    jobs_failed: int
    tracks_total: int
    detections_total: int
    class_breakdown: list[ClassCount]
