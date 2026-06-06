"""
Consumes oc_results queue and writes Track + Detection rows to Postgres.
Runs in a daemon thread started from the FastAPI lifespan.
"""
import json
import time
import pika
import structlog
from datetime import datetime, timezone

from app.config import get_settings
from app.db import SessionLocal
from app.models.job import Job, JobStatus
from app.models.track import Track
from app.models.detection import Detection

log = structlog.get_logger()


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
    track_id = msg.get("track_id")
    frame_index = msg.get("frame_index")
    class_label = msg.get("class_label")
    confidence = msg.get("confidence", 0.0)
    bbox = msg.get("bbox")
    snapshot_path = msg.get("snapshot_path")
    is_final = msg.get("is_final", False)

    db = SessionLocal()
    try:
        # Update job status on first oc_result
        job = db.query(Job).filter_by(id=job_id).first()
        if job is None:
            log.error("oc_result_unknown_job", job_id=job_id)
            return

        if job.status == JobStatus.queued:
            job.status = JobStatus.oc_processing

        if track_id is not None:
            track = _get_or_create_track(db, job_id, track_id)

            # Update track aggregate fields
            if confidence > (track.confidence_max or 0.0):
                track.confidence_max = confidence
            if class_label:
                track.class_label = class_label
            if frame_index is not None:
                if track.first_frame is None or frame_index < track.first_frame:
                    track.first_frame = frame_index
                if track.last_frame is None or frame_index > track.last_frame:
                    track.last_frame = frame_index
            if snapshot_path and track.snapshot_path is None:
                track.snapshot_path = snapshot_path

            db.add(Detection(
                track_id=track.id,
                job_id=job_id,
                frame_index=frame_index,
                class_label=class_label,
                confidence=confidence,
                bbox=bbox,
                crop_path=msg.get("crop_path"),
            ))

        if is_final:
            job.status = JobStatus.completed
            job.completed_at = datetime.now(timezone.utc)
            log.info("job_completed", job_id=job_id)

        db.commit()

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
