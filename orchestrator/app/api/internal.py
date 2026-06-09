"""Internal endpoints — no auth required, only reachable within the Docker network."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.job import Job, JobStatus

router = APIRouter(prefix="/internal", tags=["internal"])

# Statuses a worker should actually process
_PROCESSABLE = {JobStatus.queued, JobStatus.md_complete}


@router.get("/jobs/{job_id}/status")
def job_status(job_id: int, db: Session = Depends(get_db)):
    """
    Lightweight status check used by workers before starting expensive processing.
    Returns whether the job should be processed, and its current status.
    No auth — internal Docker network only.
    """
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        return {"processable": False, "status": "deleted"}
    return {
        "processable": job.status in _PROCESSABLE,
        "status": job.status.value,
    }


@router.get("/workers/{worker_id}/status")
def worker_status(worker_id: str):
    """
    Lightweight suspension check polled by workers on each heartbeat.
    Returns whether the worker is currently suspended.
    No auth — internal Docker network only.
    """
    from app.services import worker_registry
    return {"suspended": worker_registry.is_suspended(worker_id)}
