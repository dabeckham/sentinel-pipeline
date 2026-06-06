"""
Startup recovery routines — run once during orchestrator lifespan startup.

  1. recover_stuck_jobs()  — reset in-flight jobs to queued and re-publish
  2. scan_ingest_missed()  — detect files already on disk that have no job record
"""
import hashlib
import structlog
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models.job import Job, JobStatus
from app.services import amqp

log = structlog.get_logger()

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".ts", ".m4v", ".mpg", ".mpeg"}

# Statuses that mean the job was in-flight when the process died
_STUCK_STATUSES = (
    JobStatus.queued,
    JobStatus.md_processing,
    JobStatus.oc_processing,
)


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def recover_stuck_jobs() -> int:
    """
    Find jobs left in an intermediate state (queued / md_processing /
    oc_processing) from before the last restart and re-publish them to
    the ingest queue so they are processed from scratch.

    Returns the number of jobs recovered.
    """
    db = SessionLocal()
    recovered = 0
    try:
        stuck = (
            db.query(Job)
            .filter(Job.status.in_(_STUCK_STATUSES))
            .all()
        )
        if not stuck:
            log.info("startup_recovery_no_stuck_jobs")
            return 0

        for job in stuck:
            log.info(
                "startup_recovery_requeueing",
                job_id=job.id,
                old_status=job.status.value,
                file_path=job.file_path,
            )
            job.status = JobStatus.queued
            db.add(job)
            db.flush()

            amqp.publish(
                get_settings().queue_ingest,
                {
                    "job_id": job.id,
                    "video_path": job.file_path,
                    "source_type": "recovery",
                    "options": {},
                },
            )
            recovered += 1

        db.commit()
        log.info("startup_recovery_complete", recovered=recovered)
    except Exception:
        log.exception("startup_recovery_error")
        db.rollback()
    finally:
        db.close()

    return recovered


def scan_ingest_missed() -> int:
    """
    Walk the ingest directory and submit any video files that have no
    existing job record (matched by SHA-256 hash).  Safe to call even
    when the watcher is already running — the watcher's own hash check
    prevents double-ingestion.

    Returns the number of new jobs created.
    """
    settings = get_settings()
    ingest_path = Path(settings.ingest_watch_path)
    if not ingest_path.exists():
        log.warning("startup_scan_path_missing", path=str(ingest_path))
        return 0

    ignore_dirs = {d.strip().lower() for d in settings.ingest_ignore_dirs.split(",") if d.strip()}
    glob = "**/*" if settings.ingest_recurse else "*"
    candidates = [
        p for p in ingest_path.glob(glob)
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and not any(part.lower() in ignore_dirs for part in p.parts)
    ]

    if not candidates:
        log.info("startup_scan_no_files", path=str(ingest_path))
        return 0

    log.info("startup_scan_found_files", count=len(candidates), path=str(ingest_path))
    created = 0
    db = SessionLocal()
    try:
        for p in candidates:
            try:
                file_hash = _hash_file(str(p))
            except OSError as exc:
                log.warning("startup_scan_hash_error", path=str(p), error=str(exc))
                continue

            existing = db.query(Job).filter_by(file_hash=file_hash).first()
            if existing:
                log.debug("startup_scan_already_tracked",
                           path=str(p), job_id=existing.id, status=existing.status.value)
                continue

            log.info("startup_scan_new_file", path=str(p))
            job = Job(
                file_path=str(p),
                file_hash=file_hash,
                source_path=str(p),
                status=JobStatus.queued,
            )
            db.add(job)
            db.flush()

            amqp.publish(
                settings.queue_ingest,
                {
                    "job_id": job.id,
                    "video_path": str(p),
                    "source_type": "startup_scan",
                    "options": {},
                },
            )
            created += 1

        db.commit()
        log.info("startup_scan_complete", new_jobs=created)
    except Exception:
        log.exception("startup_scan_error")
        db.rollback()
    finally:
        db.close()

    return created
