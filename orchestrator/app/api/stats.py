"""Stats endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.db import get_db
from app.models.job import Job, JobStatus
from app.models.track import Track
from app.models.detection import Detection
from app.models.user import User
from app.schemas.stats import StatsResponse, ClassCount

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    jobs_total = db.query(func.count(Job.id)).scalar()
    jobs_completed = db.query(func.count(Job.id)).filter(Job.status == JobStatus.completed).scalar()
    jobs_queued = db.query(func.count(Job.id)).filter(Job.status == JobStatus.queued).scalar()
    jobs_processing = db.query(func.count(Job.id)).filter(
        Job.status.in_([JobStatus.md_processing, JobStatus.oc_processing])
    ).scalar()
    jobs_failed = db.query(func.count(Job.id)).filter(Job.status == JobStatus.failed).scalar()
    tracks_total = db.query(func.count(Track.id)).scalar()
    detections_total = db.query(func.count(Detection.id)).scalar()

    class_rows = (
        db.query(
            Track.class_label,
            func.count(Track.id).label("count"),
            func.avg(Track.confidence_max).label("avg_confidence"),
        )
        .filter(Track.class_label.isnot(None))
        .group_by(Track.class_label)
        .order_by(func.count(Track.id).desc())
        .all()
    )

    return StatsResponse(
        jobs_total=jobs_total,
        jobs_completed=jobs_completed,
        jobs_queued=jobs_queued,
        jobs_processing=jobs_processing,
        jobs_failed=jobs_failed,
        tracks_total=tracks_total,
        detections_total=detections_total,
        class_breakdown=[
            ClassCount(
                class_label=r.class_label,
                count=r.count,
                avg_confidence=round(r.avg_confidence or 0, 3),
            )
            for r in class_rows
        ],
    )
