import hashlib
import time
import structlog
from pathlib import Path
from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

from app.config import get_settings
from app.db import SessionLocal
from app.models.job import Job, JobStatus
from app.services import amqp

log = structlog.get_logger()

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".ts", ".m4v", ".mpg", ".mpeg"}


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class IngestHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).suffix.lower() not in VIDEO_EXTENSIONS:
            return
        # Brief pause — camera may still be writing the file
        time.sleep(3)
        self._process(event.src_path)

    def on_moved(self, event):
        # Some FTP servers write to a temp name then rename on completion
        if event.is_directory:
            return
        if Path(event.dest_path).suffix.lower() not in VIDEO_EXTENSIONS:
            return
        self._process(event.dest_path)

    def _process(self, path: str):
        log.info("ingest_file_detected", path=path)
        try:
            file_hash = _hash_file(path)
        except OSError as exc:
            log.error("ingest_hash_error", path=path, error=str(exc))
            return

        db = SessionLocal()
        try:
            existing = db.query(Job).filter_by(file_hash=file_hash).first()
            if existing:
                log.info("ingest_duplicate_skipped", path=path, existing_job_id=existing.id)
                return

            job = Job(
                file_path=path,
                file_hash=file_hash,
                source_path=path,
                status=JobStatus.queued,
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            settings = get_settings()
            amqp.publish(settings.queue_ingest, {
                "job_id": job.id,
                "video_path": path,
                "source_type": "ftp",
                "options": {},
            })
            log.info("ingest_job_queued", job_id=job.id, path=path)

        except Exception:
            log.exception("ingest_process_error", path=path)
            db.rollback()
        finally:
            db.close()


def start_watcher() -> Observer:
    settings = get_settings()
    observer = Observer()
    observer.schedule(
        IngestHandler(),
        settings.ingest_watch_path,
        recursive=settings.ingest_recurse,
    )
    observer.start()
    log.info("file_watcher_started", path=settings.ingest_watch_path)
    return observer
