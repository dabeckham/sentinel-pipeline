"""OC Worker — Object Classification + Tracking (YOLO + Norfair)"""
import base64
import json
import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pika
import setproctitle
import structlog

from worker.config import get_settings
from worker.detector import track_full_frame, reset_tracker, get_model
from worker.minio_client import upload_snapshot

log = structlog.get_logger()

# Unique identity for this worker instance
WORKER_ID = f"{socket.gethostname()}-oc-{os.getpid()}"

# Best-shot tracking: (job_id, track_id) → best score seen so far (lower = better)
# Score = abs(bbox_center_y / frame_height - 0.5): 0.0 = perfectly centered vertically
_best_shot_score: dict[tuple[int, int], float] = {}

# Background thread pool for async MinIO uploads (best-shot only)
_upload_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="minio-upload")


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            # prefetch_count=4: pre-fetch next messages while current is processing
            # Keeps the GPU fed without waiting on ack round-trips
            ch.basic_qos(prefetch_count=4)
            log.info("oc_worker_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("oc_worker_amqp_retry", attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def _decode_frame(b64: str) -> np.ndarray | None:
    try:
        data = base64.b64decode(b64)
        arr  = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        log.exception("oc_frame_decode_error")
        return None


def _upload_best_shot(bucket: str, name: str, frame: np.ndarray):
    """Fire-and-forget MinIO upload — runs in background thread pool."""
    try:
        upload_snapshot(bucket, name, frame)
    except Exception:
        log.exception("oc_best_shot_upload_error", name=name)


def _publish_final(ch, settings, job_id, osd_camera_name, osd_recorded_at):
    ch.basic_publish(
        exchange="",
        routing_key=settings.queue_oc_results,
        body=json.dumps({
            "job_id": job_id,
            "is_final": True,
            "worker_id": WORKER_ID,
            "osd_camera_name": osd_camera_name,
            "osd_recorded_at": osd_recorded_at,
        }),
        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
    )


def process_frame(msg: dict, ch, method):
    settings     = get_settings()
    job_id       = msg["job_id"]
    frame_index  = msg["frame_index"]
    timestamp_ms = msg["timestamp_ms"]
    frame_b64    = msg.get("frame_b64", "")
    is_final     = msg.get("is_final", False)
    osd_camera_name = msg.get("osd_camera_name")
    osd_recorded_at = msg.get("osd_recorded_at")
    video_fps       = msg.get("video_fps", 30.0)

    try:
        if not frame_b64:
            if is_final:
                _publish_final(ch, settings, job_id, osd_camera_name, osd_recorded_at)
                reset_tracker()
                _cleanup_best_shots(job_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        full_frame = _decode_frame(frame_b64)
        if full_frame is None:
            if is_final:
                _publish_final(ch, settings, job_id, osd_camera_name, osd_recorded_at)
                reset_tracker()
                _cleanup_best_shots(job_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        frame_h, frame_w = full_frame.shape[:2]

        # ── YOLO + Norfair ────────────────────────────────────────────────────
        detections = track_full_frame(full_frame, video_fps, frame_index)

        for det in detections:
            track_id  = det["track_id"]
            bbox      = det["bbox"]
            key       = (job_id, track_id)
            best_name = f"{job_id}/track_{track_id:06d}_best.jpg"

            # Best-shot: update _best.jpg when this frame is more vertically centered.
            # Upload happens in background — does NOT block the main processing loop.
            bbox_cy = bbox["y"] + bbox["h"] / 2
            score   = abs(bbox_cy / frame_h - 0.5)
            is_best = score < _best_shot_score.get(key, 1.0)
            if is_best:
                _best_shot_score[key] = score
                frame_copy = full_frame.copy()   # copy before frame may be GC'd
                _upload_pool.submit(
                    _upload_best_shot,
                    settings.minio_bucket_snapshots,
                    best_name,
                    frame_copy,
                )

            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_oc_results,
                body=json.dumps({
                    "job_id":        job_id,
                    "track_id":      track_id,
                    "frame_index":   frame_index,
                    "timestamp_ms":  timestamp_ms,
                    "class_label":   det["class_label"],
                    "confidence":    det["confidence"],
                    "bbox":          bbox,
                    "snapshot_path": best_name,
                    "snapshot_bbox": bbox if is_best else None,
                    "crop_path":     None,   # _f{frame}.jpg removed for throughput
                    "is_final":      False,
                    "worker_id":     WORKER_ID,
                    "osd_camera_name": osd_camera_name,
                    "osd_recorded_at": osd_recorded_at,
                }),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )

        if is_final:
            _publish_final(ch, settings, job_id, osd_camera_name, osd_recorded_at)
            reset_tracker()
            _cleanup_best_shots(job_id)

        log.info("oc_frame_processed",
                 job_id=job_id, frame_index=frame_index,
                 detections=len(detections), is_final=is_final,
                 worker=WORKER_ID)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception:
        log.exception("oc_frame_error", job_id=job_id, frame_index=frame_index)
        try:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass  # channel may already be closed — reconnect loop will handle it


def _cleanup_best_shots(job_id: int):
    for k in [k for k in _best_shot_score if k[0] == job_id]:
        del _best_shot_score[k]


def main():
    setproctitle.setproctitle(f"sentinel-oc-worker [{WORKER_ID}]")
    settings = get_settings()
    log.info("oc_worker_starting",
             worker_id=WORKER_ID,
             rabbitmq_host=settings.rabbitmq_host,
             queue=settings.queue_motion_results,
             gpu=settings.oc_use_gpu,
             model=settings.yolo_model_path)

    get_model()

    conn, ch = _connect(settings)

    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("oc_worker_sigterm_received", worker_id=WORKER_ID)
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
            process_frame(msg, ch, method)
        except Exception:
            log.exception("oc_message_parse_error")
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                pass  # channel may already be closed

    ch.basic_consume(queue=settings.queue_motion_results, on_message_callback=on_message)
    log.info("oc_worker_consuming", queue=settings.queue_motion_results, worker_id=WORKER_ID)

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
            ch.basic_consume(queue=settings.queue_motion_results, on_message_callback=on_message)

    log.info("oc_worker_stopped", worker_id=WORKER_ID)
    _upload_pool.shutdown(wait=True)   # drain any pending best-shot uploads
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
