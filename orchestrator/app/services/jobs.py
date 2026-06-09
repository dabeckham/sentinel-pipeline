"""Job endpoints."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_viewer
from app.db import get_db
from app.models.job import Job, JobStatus
from app.models.track import Track
from app.models.detection import Detection
from app.models.user import User
from app.schemas.job import JobResponse, JobListResponse
from app.schemas.track import TrackResponse, TrackListResponse
from app.schemas.detection import DetectionResponse, DetectionListResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Statuses that are still "in flight" — eligible for kill/pause
_ACTIVE = {JobStatus.pending, JobStatus.queued,
           JobStatus.md_processing, JobStatus.md_complete, JobStatus.oc_processing}
# Statuses that can be paused (not yet picked up by a worker)
_PAUSEABLE = {JobStatus.pending, JobStatus.queued}
# Terminal statuses — nothing can be done except remove
_TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.duplicate}


def _job_to_response(job: Job, track_count: int | None = None) -> JobResponse:
    return JobResponse(
        id=job.id,
        file_path=job.file_path,
        status=job.status,
        camera_name=job.camera_name,
        recorded_at=job.recorded_at,
        created_at=job.created_at,
        md_started_at=job.md_started_at,
        md_completed_at=job.md_completed_at,
        oc_started_at=job.oc_started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        track_count=track_count,
        md_worker_id=job.md_worker_id,
        oc_worker_id=job.oc_worker_id,
    )


# ── Bulk actions (must be declared before /{job_id} wildcard routes) ──────────

@router.post("/bulk/pause", response_model=dict)
def bulk_pause(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause all queued/pending jobs."""
    updated = (
        db.query(Job)
        .filter(Job.status.in_([JobStatus.pending, JobStatus.queued]))
        .update({"status": JobStatus.paused}, synchronize_session=False)
    )
    db.commit()
    return {"paused": updated}


@router.post("/bulk/kill", response_model=dict)
def bulk_kill(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel all non-terminal jobs (queued, pending, paused, in-flight)."""
    killable = list(_ACTIVE) + [JobStatus.paused]
    now = datetime.now(timezone.utc)
    updated = (
        db.query(Job)
        .filter(Job.status.in_(killable))
        .update(
            {"status": JobStatus.failed,
             "error_message": f"Bulk kill by {current_user.username}",
             "completed_at": now},
            synchronize_session=False,
        )
    )
    db.commit()
    return {"killed": updated}


@router.post("/bulk/resume", response_model=dict)
def bulk_resume(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume all paused jobs — set to queued and re-publish to ingest queue."""
    paused_jobs = db.query(Job).filter(Job.status == JobStatus.paused).all()
    if not paused_jobs:
        return {"resumed": 0}

    from app.config import get_settings
    from app.services import amqp
    settings = get_settings()

    for job in paused_jobs:
        job.status = JobStatus.queued
        amqp.publish(settings.queue_ingest, {
            "job_id": job.id,
            "video_path": job.file_path,
            "source_type": "resume",
            "options": {},
        })

    db.commit()
    return {"resumed": len(paused_jobs)}


@router.delete("/bulk/delete-failed", response_model=dict)
def bulk_delete_failed(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete all failed and duplicate jobs (cascade removes tracks/detections)."""
    deletable = [JobStatus.failed, JobStatus.duplicate, JobStatus.paused]
    jobs = db.query(Job).filter(Job.status.in_(deletable)).all()
    count = len(jobs)
    for job in jobs:
        db.delete(job)
    db.commit()
    return {"deleted": count}


# ── List / get ────────────────────────────────────────────────────────────────

@router.get("", response_model=JobListResponse)
def list_jobs(
    status: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    # Subquery: track count per job
    tc_sq = (
        select(Track.job_id, func.count(Track.id).label("cnt"))
        .group_by(Track.job_id)
        .subquery()
    )

    q = (
        db.query(Job, func.coalesce(tc_sq.c.cnt, 0).label("track_count"))
        .outerjoin(tc_sq, tc_sq.c.job_id == Job.id)
    )
    if status:
        # Support single or multiple status values (?status=queued&status=completed)
        q = q.filter(Job.status.in_(status))
    total = q.count()
    rows = q.order_by(Job.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_job_to_response(job, tc) for job, tc in rows]
    return JobListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_viewer)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tc = db.query(func.count(Track.id)).filter(Track.job_id == job_id).scalar()
    return _job_to_response(job, tc)


# ── Per-job actions ───────────────────────────────────────────────────────────

@router.post("/{job_id}/pause", response_model=JobResponse)
def pause_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hold a queued/pending job — it will not be processed until resumed.

    Only works on jobs that haven't been picked up by a worker yet
    (pending or queued).  In-flight jobs must be killed instead.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in _PAUSEABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Job is {job.status.value} — only queued/pending jobs can be paused",
        )
    job.status = JobStatus.paused
    db.commit()
    tc = db.query(func.count(Track.id)).filter(Track.job_id == job_id).scalar()
    return _job_to_response(job, tc)


@router.post("/{job_id}/resume", response_model=JobResponse)
def resume_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-queue a paused job for processing."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.paused:
        raise HTTPException(status_code=400, detail=f"Job is {job.status.value} — only paused jobs can be resumed")

    job.status = JobStatus.queued
    db.commit()

    # Re-publish to the ingest queue so a worker picks it up
    from app.config import get_settings
    from app.services import amqp
    settings = get_settings()
    amqp.publish(settings.queue_ingest, {
        "job_id": job.id,
        "video_path": job.file_path,
        "source_type": "resume",
        "options": {},
    })

    tc = db.query(func.count(Track.id)).filter(Track.job_id == job_id).scalar()
    return _job_to_response(job, tc)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a queued or in-progress job as failed (cancelled by user)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in _TERMINAL:
        raise HTTPException(status_code=400, detail=f"Job is already {job.status.value} — cannot cancel")
    job.status = JobStatus.failed
    job.error_message = f"Cancelled by {current_user.username}"
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    tc = db.query(func.count(Track.id)).filter(Track.job_id == job_id).scalar()
    return _job_to_response(job, tc)


@router.delete("/{job_id}", status_code=204)
def remove_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a job and all its detections/tracks.

    Only allowed on terminal or paused jobs.  Kill active jobs first.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in _ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Job is {job.status.value} — kill it before removing",
        )
    db.delete(job)
    db.commit()
    return None


# ── Job sub-resources ─────────────────────────────────────────────────────────

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
