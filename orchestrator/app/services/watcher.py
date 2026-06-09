import hashlib
import time
import threading
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

# Module-level observer handle so health_monitor can pause/resume it.
_observer: Observer | None = None
_observer_lock = threading.Lock()


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
        if self._is_ignored(event.src_path):
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
        if self._is_ignored(event.dest_path):
            return
        self._process(event.dest_path)

    def _is_ignored(self, path: str) -> bool:
        """Return True if any path component matches an ignore dir."""
        settings = get_settings()
        ignore = {d.strip().lower() for d in settings.ingest_ignore_dirs.split(",") if d.strip()}
        return any(part.lower() in ignore for part in Path(path).parts)

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

            settings = get_settings()
            job = Job(
                file_path=path,
                file_hash=file_hash,
                source_path=path,
                status=JobStatus.queued,
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            amqp.publish(settings.queue_ingest, {
                "job_id": job.id,
                "video_path": path,
                "source_type": "ftp",
                "options": {},
            })
            log.info("ingest_job_queued", job_id=job.id, path=path)

            # Broadcast to WebSocket clients
            try:
                from app.api.ws import broadcast
                from app.services.event_loop import get_loop
                loop = get_loop()
                if loop is not None:
                    import asyncio
                    event = {
                        "type": "job_update",
                        "job_id": job.id,
                        "status": job.status.value,
                        "file_path": path,
                    }
                    asyncio.run_coroutine_threadsafe(broadcast(event), loop)
            except Exception:
                pass

        except Exception:
            log.exception("ingest_process_error", path=path)
            db.rollback()
        finally:
            db.close()


def start_watcher() -> None:
    """
    Start the file watcher unless the health monitor already determined the
    pipeline is backed up (startup_health_check sets the paused state before
    this is called).  If paused, the watcher stays stopped; resume_watcher()
    will be called by the health monitor when the pipeline clears.
    """
    from app.services.health_monitor import _state as _hm_state
    if _hm_state.watcher_paused:
        log.warning("file_watcher_start_skipped_pipeline_backed_up")
        return
    resume_watcher()   # resume_watcher handles the observer creation + scan


def pause_watcher():
    """
    Stop the file watcher.  Called by the health monitor when the pipeline
    stalls.  Any files that arrive while paused will be picked up by
    resume_watcher() → scan_ingest_missed().
    """
    global _observer
    with _observer_lock:
        obs = _observer
        _observer = None

    if obs is not None:
        try:
            obs.stop()
            obs.join(timeout=5)
        except Exception:
            pass
        log.warning("file_watcher_paused")
    else:
        log.debug("file_watcher_pause_noop")


def resume_watcher():
    """
    Restart the file watcher and immediately scan for any files that
    arrived while it was paused.  Called by the health monitor on recovery.
    """
    global _observer
    with _observer_lock:
        already_running = _observer is not None

    if already_running:
        log.debug("file_watcher_resume_already_running")
        return

    settings = get_settings()
    obs = Observer()
    obs.schedule(
        IngestHandler(),
        settings.ingest_watch_path,
        recursive=settings.ingest_recurse,
    )
    obs.start()
    with _observer_lock:
        _observer = obs
    log.info("file_watcher_resumed", path=settings.ingest_watch_path)

    # Pick up any files that landed while we were paused
    try:
        from app.services.startup_recovery import scan_ingest_missed
        scan_ingest_missed()
    except Exception:
        log.exception("file_watcher_resume_scan_error")
