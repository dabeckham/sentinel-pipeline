"""
Consumes oc_results queue and writes Track + Detection rows to Postgres.
Runs in a daemon thread started from the FastAPI lifespan.
"""
import json
import time
import pika
import structlog
from datetime import datetime, timezone, timedelta

from app.config import get_settings
from app.db import SessionLocal
from app.models.job import Job, JobStatus
from app.models.track import Track
from app.models.detection import Detection

log = structlog.get_logger()


def _classify_tracks(db, job_id: int) -> None:
    """
    Classify every track in a completed job as 'moving' or 'stationary'.

    Uses normalized centroid displacement: the straight-line distance between
    the first and last detection's bbox centre, divided by the average bbox width.

    Threshold comes from settings.tracker_min_displacement (default 0.3).
    Below threshold → stationary (parked car, package on porch, etc.)
    At or above → moving (person walking, vehicle driving, etc.)

    Single-detection tracks (no first/last pair) are classified stationary.
    """
    import math
    from app.models.detection import Detection
    from sqlalchemy import func

    settings = get_settings()
    threshold = settings.tracker_min_displacement

    tracks = db.query(Track).filter_by(job_id=job_id).all()
    if not tracks:
        return

    track_ids = [t.id for t in tracks]

    # First detection per track (lowest frame_index with non-null bbox)
    first_rows = (
        db.query(Detection.track_id,
                 Detection.bbox)
        .filter(Detection.track_id.in_(track_ids),
                Detection.bbox.isnot(None))
        .order_by(Detection.track_id, Detection.frame_index.asc())
        .distinct(Detection.track_id)
        .all()
    )

    # Last detection per track
    last_rows = (
        db.query(Detection.track_id,
                 Detection.bbox)
        .filter(Detection.track_id.in_(track_ids),
                Detection.bbox.isnot(None))
        .order_by(Detection.track_id, Detection.frame_index.desc())
        .distinct(Detection.track_id)
        .all()
    )

    first_map = {r.track_id: r.bbox for r in first_rows}
    last_map  = {r.track_id: r.bbox for r in last_rows}

    track_map = {t.id: t for t in tracks}

    classified_moving = 0
    classified_stationary = 0

    for tid, track in track_map.items():
        fb = first_map.get(tid)
        lb = last_map.get(tid)

        if not fb or not lb:
            track.track_type = "stationary"
            classified_stationary += 1
            continue

        # Centroid of first bbox
        fcx = fb["x"] + fb["w"] / 2
        fcy = fb["y"] + fb["h"] / 2

        # Centroid of last bbox
        lcx = lb["x"] + lb["w"] / 2
        lcy = lb["y"] + lb["h"] / 2

        # Pixel displacement
        displacement = math.sqrt((lcx - fcx) ** 2 + (lcy - fcy) ** 2)

        # Normalize by average bbox width
        avg_width = (fb["w"] + lb["w"]) / 2
        norm_disp = displacement / avg_width if avg_width > 0 else 0.0

        if norm_disp >= threshold:
            track.track_type = "moving"
            classified_moving += 1
        else:
            track.track_type = "stationary"
            classified_stationary += 1

    log.info("tracks_classified", job_id=job_id,
             moving=classified_moving, stationary=classified_stationary,
             threshold=threshold)


def _get_or_create_track(db, job_id: int, track_id: int) -> Track:
    track = db.query(Track).filter_by(job_id=job_id, track_id=track_id).first()
    if track is None:
        track = Track(job_id=job_id, track_id=track_id)
        db.add(track)
        db.flush()
    return track


def _handle_message(body: bytes):
    try:
        msg = json.loads(body)
    except Exception:
        log.error("oc_result_bad_json")
        return

    job_id = msg.get("job_id")

    # OC worker pickup ack — update status immediately when worker takes the job
    if msg.get("oc_status") == "oc_processing":
        db = SessionLocal()
        try:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job or job.status in (JobStatus.failed, JobStatus.paused):
                return
            if job.status == JobStatus.md_complete:
                job.status = JobStatus.oc_processing
                job.oc_started_at = datetime.now(timezone.utc)
                if msg.get("worker_id"):
                    job.oc_worker_id = msg["worker_id"]
                db.commit()
                try:
                    from app.api.ws import broadcast
                    from app.services.event_loop import get_loop
                    loop = get_loop()
                    if loop is not None:
                        import asyncio
                        asyncio.run_coroutine_threadsafe(
                            broadcast({"type": "job_update", "job_id": job_id, "status": "oc_processing"}),
                            loop,
                        )
                except Exception:
                    pass
        except Exception:
            log.exception("oc_status_update_error", job_id=job_id)
            db.rollback()
        finally:
            db.close()
        return

    # MD worker status ping — update job status and stamp timestamps
    if msg.get("md_status"):
        db = SessionLocal()
        try:
            job = db.query(Job).filter_by(id=job_id).first()
            if not job:
                return
            # Drop updates for killed/paused jobs
            if job.status in (JobStatus.failed, JobStatus.paused):
                return
            new_status = None
            if msg["md_status"] == "md_processing" and job.status == JobStatus.queued:
                job.status = JobStatus.md_processing
                job.md_started_at = datetime.now(timezone.utc)
                if msg.get("worker_id"):
                    job.md_worker_id = msg["worker_id"]
                new_status = "md_processing"
            elif msg["md_status"] == "md_complete" and job.status == JobStatus.md_processing:
                job.status = JobStatus.md_complete
                job.md_completed_at = datetime.now(timezone.utc)
                new_status = "md_complete"
            if new_status:
                db.commit()
                try:
                    from app.api.ws import broadcast
                    from app.services.event_loop import get_loop
                    loop = get_loop()
                    if loop is not None:
                        import asyncio
                        asyncio.run_coroutine_threadsafe(
                            broadcast({"type": "job_update", "job_id": job_id, "status": new_status}),
                            loop,
                        )
                except Exception:
                    pass
        except Exception:
            log.exception("md_status_update_error", job_id=job_id)
            db.rollback()
        finally:
            db.close()
        return

    is_final        = msg.get("is_final", False)
    osd_camera_name = msg.get("osd_camera_name")
    osd_recorded_at_str = msg.get("osd_recorded_at")

    # Parse OSD recorded_at ISO string once
    osd_recorded_at: datetime | None = None
    if osd_recorded_at_str:
        try:
            osd_recorded_at = datetime.fromisoformat(osd_recorded_at_str).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # OC workers now send one message per job with all detections bundled.
    # The message always has is_final=True.
    detections = msg.get("detections", [])

    db = SessionLocal()
    try:
        job = db.query(Job).filter_by(id=job_id).first()
        if job is None:
            log.error("oc_result_unknown_job", job_id=job_id)
            return

        if job.status in (JobStatus.failed, JobStatus.paused):
            log.info("oc_result_dropped_inactive", job_id=job_id, status=job.status.value)
            return

        # Mark oc_processing + stamp start time
        job.status     = JobStatus.oc_processing
        job.oc_started_at = datetime.now(timezone.utc)
        if msg.get("worker_id"):
            job.oc_worker_id = msg["worker_id"]

        if osd_camera_name and job.camera_name is None:
            job.camera_name = osd_camera_name
        if osd_recorded_at and job.recorded_at is None:
            job.recorded_at = osd_recorded_at

        # ── Bulk-build tracks + detections in one transaction ─────────────────
        track_map: dict[int, Track] = {}

        for det in detections:
            track_id    = det["track_id"]
            frame_index = det["frame_index"]
            timestamp_ms = det.get("timestamp_ms", 0)
            confidence  = det.get("confidence", 0.0)
            class_label = det.get("class_label")
            bbox        = det.get("bbox")
            snapshot_path = det.get("snapshot_path")
            snapshot_bbox = det.get("snapshot_bbox")

            if track_id not in track_map:
                track_map[track_id] = _get_or_create_track(db, job_id, track_id)
            track = track_map[track_id]

            if confidence > (track.confidence_max or 0.0):
                track.confidence_max = confidence
            if class_label:
                track.class_label = class_label
            if frame_index is not None:
                if track.first_frame is None or frame_index < track.first_frame:
                    track.first_frame = frame_index
                    if osd_recorded_at:
                        track.started_at = osd_recorded_at + timedelta(milliseconds=timestamp_ms)
                if track.last_frame is None or frame_index > track.last_frame:
                    track.last_frame = frame_index
                    if osd_recorded_at:
                        track.ended_at = osd_recorded_at + timedelta(milliseconds=timestamp_ms)
            if snapshot_path:
                track.snapshot_path = snapshot_path
            if snapshot_bbox:
                track.snapshot_bbox = snapshot_bbox

            db.add(Detection(
                track_id=track.id,
                job_id=job_id,
                frame_index=frame_index,
                class_label=class_label,
                confidence=confidence,
                bbox=bbox,
                crop_path=None,
            ))

        if is_final:
            job.status = JobStatus.completed
            job.completed_at = datetime.now(timezone.utc)
            _classify_tracks(db, job_id)
            log.info("job_completed", job_id=job_id, detections=len(detections))

        db.commit()

        try:
            from app.api.ws import broadcast
            from app.services.event_loop import get_loop
            loop = get_loop()
            if loop is not None:
                import asyncio
                event = {"type": "job_update", "job_id": job_id, "status": job.status.value}
                if is_final:
                    event["completed_at"] = job.completed_at.isoformat()
                asyncio.run_coroutine_threadsafe(broadcast(event), loop)
        except Exception:
            pass

    except Exception:
        log.exception("oc_result_write_error", job_id=job_id)
        db.rollback()
    finally:
        db.close()


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    conn = pika.BlockingConnection(settings.rabbitmq_params())
    ch = conn.channel()
    ch.basic_qos(prefetch_count=10)
    return conn, ch


def start_result_consumer():
    settings = get_settings()
    conn = None
    while True:
        try:
            conn, ch = _connect(settings)
            log.info("oc_result_consumer_ready", queue=settings.queue_oc_results)

            def on_message(ch, method, _props, body):
                try:
                    _handle_message(body)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception:
                    log.exception("oc_result_nack", delivery_tag=method.delivery_tag)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            ch.basic_consume(queue=settings.queue_oc_results, on_message_callback=on_message)
            ch.start_consuming()

        except pika.exceptions.AMQPConnectionError as exc:
            log.warning("oc_consumer_reconnecting", error=str(exc))
            time.sleep(5)
        except Exception:
            log.exception("oc_consumer_error")
            time.sleep(5)
        finally:
            if conn and not conn.is_closed:
                try:
                    conn.close()
                except Exception:
                    pass
