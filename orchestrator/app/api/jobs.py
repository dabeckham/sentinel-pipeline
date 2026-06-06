"""Job endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_viewer
from app.db import get_db
from app.models.job import Job
from app.models.track import Track
from app.models.detection import Detection
from app.models.user import User
from app.schemas.job import JobResponse, JobListResponse
from app.schemas.track import TrackResponse, TrackListResponse
from app.schemas.detection import DetectionResponse, DetectionListResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    q = db.query(Job)
    if status:
        q = q.filter(Job.status == status)
    total = q.count()
    items = q.order_by(Job.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return JobListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/tracks", response_model=TrackListResponse)
def get_job_tracks(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    q = db.query(Track).filter(Track.job_id == job_id)
    total = q.count()
    items = q.order_by(Track.id).offset((page - 1) * page_size).limit(page_size).all()
    return TrackListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}/detections", response_model=DetectionListResponse)
def get_job_detections(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    q = db.query(Detection).filter(Detection.job_id == job_id)
    total = q.count()
    items = q.order_by(Detection.frame_index).offset((page - 1) * page_size).limit(page_size).all()
    return DetectionListResponse(items=items, total=total, page=page, page_size=page_size)
