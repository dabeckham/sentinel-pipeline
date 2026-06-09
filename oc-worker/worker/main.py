"""OC Worker — Object Classification + Tracking (TRT FP16 + ByteTrack)

Architecture (issue #39):
  Receives one job-descriptor message per job from the motion_results queue.
  Message schema:
    {
      "job_id":          int,
      "video_path":      str,       # absolute path on the shared ingest mount
      "motion_frames":   [int, ...],# frame indices from MD worker
      "osd_camera_name": str | null,
      "osd_recorded_at": str | null,# ISO-8601
      "video_fps":       float
    }
  The worker opens the video locally, runs TRT+ByteTrack, and publishes
  per-detection results to oc_results (same schema as before).
  One worker owns one job — ByteTrack state is never split across workers.
"""
import json
import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import pika
import setproctitle
import structlog

from worker.config import get_settings
from worker.detector import process_job_video, get_model
from worker.minio_client import upload_snapshot

log = structlog.get_logger()

WORKER_ID = f"{socket.gethostname()}-oc-{os.getpid()}"

# Best-shot tracking: (job_id, track_id) → best score seen so far
# Score = abs(bbox_center_y / frame_height - 0.5): 0.0 = perfectly centered
_best_shot_score: dict[tuple[int, int], float] = {}

# Background thread pool for async MinIO uploads (best-shot only)
_upload_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="minio-upload")


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            # prefetch_count=1: with the new per-job architecture each message
            # represents a full video (several seconds of work).  Only pull one
            # at a time so RabbitMQ can distribute remaining jobs to other workers.
            ch.basic_qos(prefetch_count=1)
            log.info("oc_worker_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("oc_worker_amqp_retry",
                        attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def _upload_best_shot(bucket: str, name: str, frame):
    """Fire-and-forget MinIO upload — runs in background thread pool."""
    try:
        upload_snapshot(bucket, name, frame)
    except Exception:
        log.exception("oc_best_shot_upload_error", name=name)


def _cleanup_best_shots(job_id: int):
    for k in [k for k in _best_shot_score if k[0] == job_id]:
        del _best_shot_score[k]


def process_job(msg: dict, ch, method):
    """
    Process a complete job — open video, run TRT+ByteTrack on motion frames,
    publish per-detection results, publish final marker, update best shots.
    """
    settings        = get_settings()
    job_id          = msg["job_id"]
    video_path      = msg["video_path"]
    motion_frames   = msg.get("motion_frames", [])
    osd_camera_name = msg.get("osd_camera_name")
    osd_recorded_at = msg.get("osd_recorded_at")
    video_fps       = msg.get("video_fps", 30.0)

    log.info("oc_job_start",
             job_id=job_id, video=video_path,
             motion_frames=len(motion_frames), worker=WORKER_ID)

    try:
        # Immediately notify orchestrator that this worker has taken the job
        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({"job_id": job_id, "status": "oc_processing", "worker_id": WORKER_ID}),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )

        # ── Run TRT + ByteTrack ───────────────────────────────────────────────
        detections = process_job_video(video_path, motion_frames)

        # ── Best-shot selection ───────────────────────────────────────────────
        cap = cv2.VideoCapture(video_path)
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
        cap.release()

        best_candidates: dict[int, dict] = {}   # track_id → best detection so far
        for det in detections:
            track_id = det["track_id"]
            bbox_cy  = det["bbox"]["y"] + det["bbox"]["h"] / 2
            score    = abs(bbox_cy / frame_h - 0.5)
            key      = (job_id, track_id)
            if score < _best_shot_score.get(key, 1.0):
                _best_shot_score[key] = score
                best_candidates[track_id] = det

        # Upload best-shot frames (sequential read, only needed frame indices)
        if best_candidates:
            needed_frames = {det["frame_index"]: track_id
                             for track_id, det in best_candidates.items()}
            max_fi = max(needed_frames)
            cap = cv2.VideoCapture(video_path)
            current = 0
            while current <= max_fi:
                ok, frame = cap.read()
                if not ok:
                    break
                if current in needed_frames:
                    tid  = needed_frames[current]
                    name = f"{job_id}/track_{tid:06d}_best.jpg"
                    _upload_pool.submit(
                        _upload_best_shot,
                        settings.minio_bucket_snapshots,
                        name,
                        frame.copy(),
                    )
                current += 1
            cap.release()

        # ── Publish ONE message with all detections bundled ───────────────────
        # Annotate each detection with its best-shot path.
        best_shot_map = {
            tid: f"{job_id}/track_{tid:06d}_best.jpg"
            for tid in best_candidates
        }
        for det in detections:
            det["snapshot_path"] = best_shot_map.get(det["track_id"])
            det["snapshot_bbox"] = det["bbox"] if det["track_id"] in best_candidates else None

        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({
                "job_id":          job_id,
                "is_final":        True,
                "detections":      detections,
                "worker_id":       WORKER_ID,
                "osd_camera_name": osd_camera_name,
                "osd_recorded_at": osd_recorded_at,
            }),
            properties=pika.BasicProperties(
                delivery_mode=2, content_type="application/json"),
        )

        _cleanup_best_shots(job_id)

        log.info("oc_job_complete",
                 job_id=job_id, detections=len(detections), worker=WORKER_ID)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as exc:
        log.exception("oc_job_error", job_id=job_id)
        # Notify orchestrator so the job is marked failed in the DB
        try:
            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_oc_results,
                body=json.dumps({
                    "job_id":    job_id,
                    "status":    "failed",
                    "worker_id": WORKER_ID,
                    "error":     str(exc),
                }),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
        except Exception:
            pass
        try:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass


def main():
    setproctitle.setproctitle(f"sentinel-oc-worker [{WORKER_ID}]")
    settings = get_settings()
    log.info("oc_worker_starting",
             worker_id=WORKER_ID,
             rabbitmq_host=settings.rabbitmq_host,
             queue=settings.queue_motion_results,
             gpu=settings.oc_use_gpu,
             model=settings.yolo_model_path)

    # Pre-load model (TRT export happens here on first run if needed)
    get_model()

    conn, ch = _connect(settings)

    # Worker lifecycle events (separate pika connection, own heartbeat thread)
    from worker.worker_events import WorkerEventPublisher
    events = WorkerEventPublisher(WORKER_ID, "oc", settings)
    events.online()

    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("oc_worker_sigterm_received", worker_id=WORKER_ID)
        _shutdown = True
        events.offline()
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
            log.exception("oc_message_parse_error")
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                pass

    ch.basic_consume(queue=settings.queue_motion_results,
                     on_message_callback=on_message)
    log.info("oc_worker_consuming",
             queue=settings.queue_motion_results, worker_id=WORKER_ID)

    while not _shutdown:
        try:
            ch.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.AMQPError,
                pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError):
            if _shutdown:
                break
            log.warning("oc_worker_reconnecting", worker_id=WORKER_ID)
            time.sleep(5)
            try:
                conn.close()
            except Exception:
                pass
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_motion_results,
                             on_message_callback=on_message)

    log.info("oc_worker_stopped", worker_id=WORKER_ID)
    _upload_pool.shutdown(wait=True)
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
