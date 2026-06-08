"""MD Worker — Motion Detection"""
import json
import os
import re
import signal
import socket
import time
import pika
import structlog
import setproctitle
import cv2
from datetime import datetime, timezone
from pathlib import Path

# Unique identity for this worker instance — survives for the lifetime of the process
WORKER_ID = f"{socket.gethostname()}-md-{os.getpid()}"

from worker.config import get_settings
from worker.motion import detect_motion

log = structlog.get_logger()


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            ch.basic_qos(prefetch_count=1)
            log.info("md_worker_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("md_worker_amqp_retry", attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def _parse_filename(video_path: str) -> tuple[str | None, datetime | None]:
    """
    Parse camera name and recording start time from filename.
    Expected pattern: CAMNAME_01_20230505200023.mp4
      - Everything before the _NN_ stream number = camera name (underscores → spaces)
      - 14-digit suffix = YYYYmmddHHMMSS = timestamp of first frame
    Returns (camera_name, recorded_at) — both may be None if pattern doesn't match.
    """
    stem = Path(video_path).stem
    m = re.match(r'^(.+)_\d{2}_(\d{14})$', stem)
    if not m:
        log.warning("filename_parse_failed", stem=stem)
        return None, None
    camera_name = m.group(1).replace('_', ' ')
    try:
        recorded_at = datetime.strptime(m.group(2), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return camera_name, None
    return camera_name, recorded_at


def _get_fps(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)
    try:
        return cap.get(cv2.CAP_PROP_FPS) or 30.0
    finally:
        cap.release()


def process_job(msg: dict, ch, method):
    settings = get_settings()
    job_id = msg["job_id"]
    video_path = msg["video_path"]
    log.info("md_job_start", job_id=job_id, video_path=video_path)

    try:
        # Notify orchestrator that MD processing has started
        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({"job_id": job_id, "md_status": "md_processing", "worker_id": WORKER_ID}),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )

        osd_camera_name, osd_recorded_at_dt = _parse_filename(video_path)
        osd_recorded_at = osd_recorded_at_dt.isoformat() if osd_recorded_at_dt else None
        video_fps = _get_fps(video_path)
        log.info("md_filename_parsed", camera=osd_camera_name, recorded_at=osd_recorded_at, fps=video_fps)

        motion_frames = detect_motion(video_path)
        log.info("md_motion_detected", job_id=job_id, motion_frames=len(motion_frames))

        # Publish a single job-descriptor message — OC worker opens the video
        # locally and seeks to the listed frames.  No frame data in the broker.
        # One message per job → one OC worker owns all frames → correct tracking.
        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_motion_results,
            body=json.dumps({
                "job_id":          job_id,
                "video_path":      video_path,
                "motion_frames":   [mf.frame_index for mf in motion_frames],
                "osd_camera_name": osd_camera_name,
                "osd_recorded_at": osd_recorded_at,
                "video_fps":       video_fps,
            }),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
        )

        # Notify orchestrator that MD has finished queuing all frames
        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({"job_id": job_id, "md_status": "md_complete"}),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )

        log.info("md_job_complete", job_id=job_id, frames_published=len(motion_frames))
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception:
        log.exception("md_job_error", job_id=job_id)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def main():
    setproctitle.setproctitle("sentinel-md-worker")
    settings = get_settings()
    log.info("md_worker_starting",
             rabbitmq_host=settings.rabbitmq_host,
             rabbitmq_user=settings.rabbitmq_user,
             queue=settings.queue_ingest)

    conn, ch = _connect(settings)

    # Graceful SIGTERM — finish current job then exit cleanly
    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("md_worker_sigterm_received")
        _shutdown = True
        try:
            ch.stop_consuming()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    def on_message(ch, method, _props, body):
        try:
            msg = json.loads(body)
            process_job(msg, ch, method)
        except Exception:
            log.exception("md_message_parse_error")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_consume(queue=settings.queue_ingest, on_message_callback=on_message)
    log.info("md_worker_consuming", queue=settings.queue_ingest)

    while not _shutdown:
        try:
            ch.start_consuming()
        except pika.exceptions.AMQPConnectionError:
            if _shutdown:
                break
            log.warning("md_worker_reconnecting")
            time.sleep(5)
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_ingest, on_message_callback=on_message)

    log.info("md_worker_stopped")
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
