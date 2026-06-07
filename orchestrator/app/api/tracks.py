"""Track endpoints — list, detail, camera list, active-days calendar."""
from calendar import monthrange
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.db import get_db
from app.models.detection import Detection
from app.models.job import Job
from app.models.track import Track
from app.models.user import User
from app.schemas.track import TrackDetailResponse, TrackListResponse, TrackResponse

router = APIRouter(prefix="/tracks", tags=["tracks"])


def _track_to_response(track: Track, camera_name: str | None, detection_count: int,
                        snapshot_bbox: dict | None = None) -> TrackResponse:
    return TrackResponse(
        id=track.id,
        job_id=track.job_id,
        track_id=track.track_id,
        class_label=track.class_label,
        confidence_max=track.confidence_max,
        first_frame=track.first_frame,
        last_frame=track.last_frame,
        snapshot_path=track.snapshot_path,
        started_at=track.started_at,
        ended_at=track.ended_at,
        created_at=track.created_at,
        camera_name=camera_name,
        detection_count=detection_count,
        snapshot_bbox=snapshot_bbox,
    )


@router.get("", response_model=TrackListResponse)
def list_tracks(
    job_id: Optional[int] = Query(None),
    class_label: list[str] = Query(default=[]),
    camera: list[str] = Query(default=[]),
    from_dt: Optional[datetime] = Query(None, description="Filter tracks started at or after this time (ISO)"),
    to_dt: Optional[datetime] = Query(None, description="Filter tracks started at or before this time (ISO)"),
    sort: str = Query("newest", description="Sort order: newest | oldest | confidence | class"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    # Subquery: detection count per track
    det_count_sq = (
        select(Detection.track_id, func.count(Detection.id).label("cnt"))
        .group_by(Detection.track_id)
        .subquery()
    )

    q = (
        db.query(Track, Job.camera_name,
                 func.coalesce(det_count_sq.c.cnt, 0).label("detection_count"))
        .join(Job, Job.id == Track.job_id)
        .outerjoin(det_count_sq, det_count_sq.c.track_id == Track.id)
    )

    if job_id:
        q = q.filter(Track.job_id == job_id)
    if class_label:
        q = q.filter(Track.class_label.in_(class_label))
    if camera:
        q = q.filter(Job.camera_name.in_(camera))
    if from_dt:
        q = q.filter(Track.started_at >= from_dt)
    if to_dt:
        q = q.filter(Track.started_at <= to_dt)

    total = q.count()

    order = {
        "newest": Track.id.desc(),
        "oldest": Track.id.asc(),
        "confidence": Track.confidence_max.desc().nulls_last(),
        "class": Track.class_label.asc().nulls_last(),
    }.get(sort, Track.id.desc())

    rows = q.order_by(order).offset((page - 1) * page_size).limit(page_size).all()

    items = [_track_to_response(t, cam, cnt, t.snapshot_bbox) for t, cam, cnt in rows]
    return TrackListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/active-days", response_model=list[str])
def active_days(
    year: int = Query(..., description="Calendar year"),
    month: int = Query(..., ge=1, le=12, description="Calendar month (1–12)"),
    camera: list[str] = Query(default=[]),
    class_label: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    """Return distinct dates (YYYY-MM-DD) that have tracks in the given month,
    optionally filtered by camera and class — used to highlight calendar days."""
    last_day = monthrange(year, month)[1]
    from_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    to_dt = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    q = (
        db.query(func.date(Track.started_at).label("day"))
        .join(Job, Job.id == Track.job_id)
        .filter(Track.started_at.isnot(None))
        .filter(Track.started_at >= from_dt)
        .filter(Track.started_at <= to_dt)
    )
    if camera:
        q = q.filter(Job.camera_name.in_(camera))
    if class_label:
        q = q.filter(Track.class_label.in_(class_label))

    rows = q.distinct().all()
    return sorted(str(r[0]) for r in rows)


@router.get("/cameras", response_model=list[str])
def list_cameras(
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    """Return distinct camera names that have associated tracks."""
    rows = (
        db.query(Job.camera_name)
        .join(Track, Track.job_id == Job.id)
        .filter(Job.camera_name.isnot(None))
        .distinct()
        .order_by(Job.camera_name)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/{track_id}", response_model=TrackDetailResponse)
def get_track(
    track_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    """Full track detail including all detections."""
    row = (
        db.query(Track, Job.camera_name)
        .join(Job, Job.id == Track.job_id)
        .filter(Track.id == track_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Track not found")

    track, camera_name = row
    detections = (
        db.query(Detection)
        .filter(Detection.track_id == track.id)
        .order_by(Detection.frame_index)
        .all()
    )
    detection_count = len(detections)

    base = _track_to_response(track, camera_name, detection_count)
    return TrackDetailResponse(
        **base.model_dump(),
        detections=[
            {
                "id": d.id,
                "frame_index": d.frame_index,
                "class_label": d.class_label,
                "confidence": d.confidence,
                "bbox": d.bbox,
                "crop_path": d.crop_path,
            }
            for d in detections
        ],
    )
