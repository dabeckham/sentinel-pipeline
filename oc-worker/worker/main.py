"""OC Worker — Object Classification (YOLO + ByteTrack)"""
import base64
import json
import signal
import time
import pika
import structlog
import setproctitle
import cv2
import numpy as np

from worker.config import get_settings
from worker.detector import classify_crop, track_frame, release_tracker, get_model
from worker.minio_client import upload_snapshot

log = structlog.get_logger()

# Track which tracks we've already uploaded a snapshot for
_snapshot_uploaded: dict[tuple[int, int], bool] = {}


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            ch.basic_qos(prefetch_count=1)
            log.info("oc_worker_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("oc_worker_amqp_retry", attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def _decode_crop(b64: str) -> np.ndarray | None:
    """Decode a base64 JPEG string to a BGR numpy array."""
    try:
        data = base64.b64decode(b64)
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        log.exception("oc_crop_decode_error")
        return None


def process_frame(msg: dict, ch, method):
    settings = get_settings()
    job_id = msg["job_id"]
    frame_index = msg["frame_index"]
    timestamp_ms = msg["timestamp_ms"]
    bboxes = msg.get("bounding_boxes", [])
    crops_b64 = msg.get("crops_b64", [])
    is_final = msg.get("is_final", False)

    # OSD metadata passed through from md-worker
    osd_camera_name = msg.get("osd_camera_name")
    osd_recorded_at = msg.get("osd_recorded_at")   # ISO datetime string or None
    video_fps = msg.get("video_fps", 30.0)

    try:
        if not bboxes or not crops_b64:
            # No motion in this frame — just propagate is_final
            if is_final:
                ch.basic_publish(
                    exchange="",
                    routing_key=settings.queue_oc_results,
                    body=json.dumps({
                        "job_id": job_id,
                        "is_final": True,
                        "osd_camera_name": osd_camera_name,
                        "osd_recorded_at": osd_recorded_at,
                    }),
                    properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                )
                release_tracker(job_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Decode crops from message body — no MinIO download needed (issue #13)
        crops = []
        valid_bboxes = []
        valid_b64 = []
        for bbox, b64 in zip(bboxes, crops_b64):
            img = _decode_crop(b64)
            if img is not None:
                crops.append(img)
                valid_bboxes.append(bbox)
                valid_b64.append(b64)

        if not crops:
            if is_final:
                ch.basic_publish(
                    exchange="",
                    routing_key=settings.queue_oc_results,
                    body=json.dumps({
                        "job_id": job_id,
                        "is_final": True,
                        "osd_camera_name": osd_camera_name,
                        "osd_recorded_at": osd_recorded_at,
                    }),
                    properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                )
                release_tracker(job_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        detections = track_frame(job_id, valid_bboxes, crops)

        for det in detections:
            track_id = det["track_id"]
            crop_idx = det["crop_idx"]
            track_snapshot_path = None
            det_crop_path = None

            # Per-detection crop — saved for every frame so UI can step through them
            det_name = f"{job_id}/track_{track_id:06d}_f{frame_index:06d}.jpg"
            try:
                upload_snapshot(settings.minio_bucket_snapshots, det_name, crops[crop_idx])
                det_crop_path = det_name
            except Exception:
                log.exception("oc_det_snapshot_error", job_id=job_id, track_id=track_id, frame=frame_index)

            # Track thumbnail — first detection only, used as the card thumbnail
            key = (job_id, track_id)
            if key not in _snapshot_uploaded:
                _snapshot_uploaded[key] = True
                track_snap_name = f"{job_id}/track_{track_id:06d}.jpg"
                try:
                    upload_snapshot(settings.minio_bucket_snapshots, track_snap_name, crops[crop_idx])
                    track_snapshot_path = track_snap_name
                except Exception:
                    log.exception("oc_snapshot_upload_error", job_id=job_id, track_id=track_id)

            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_oc_results,
                body=json.dumps({
                    "job_id": job_id,
                    "track_id": track_id,
                    "frame_index": frame_index,
                    "timestamp_ms": timestamp_ms,
                    "class_label": det["class_label"],
                    "confidence": det["confidence"],
                    "bbox": det["bbox"],
                    "snapshot_path": track_snapshot_path,
                    "crop_path": det_crop_path,
                    "is_final": False,
                    "osd_camera_name": osd_camera_name,
                    "osd_recorded_at": osd_recorded_at,
                }),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )

        if is_final:
            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_oc_results,
                body=json.dumps({
                    "job_id": job_id,
                    "is_final": True,
                    "osd_camera_name": osd_camera_name,
                    "osd_recorded_at": osd_recorded_at,
                }),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
            release_tracker(job_id)
            # Clean up snapshot tracking for this job
            keys_to_del = [k for k in _snapshot_uploaded if k[0] == job_id]
            for k in keys_to_del:
                del _snapshot_uploaded[k]

        log.info("oc_frame_processed",
                 job_id=job_id, frame_index=frame_index,
                 detections=len(detections), is_final=is_final)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception:
        log.exception("oc_frame_error", job_id=job_id, frame_index=frame_index)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def main():
    setproctitle.setproctitle("sentinel-oc-worker")
    settings = get_settings()
    log.info("oc_worker_starting",
             rabbitmq_host=settings.rabbitmq_host,
             queue=settings.queue_motion_results,
             gpu=settings.oc_use_gpu,
             model=settings.yolo_model_path)

    # Pre-load model before consuming so first message isn't slow
    get_model()

    conn, ch = _connect(settings)

    # Graceful SIGTERM — finish current message then exit cleanly
    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("oc_worker_sigterm_received")
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
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_consume(queue=settings.queue_motion_results, on_message_callback=on_message)
    log.info("oc_worker_consuming", queue=settings.queue_motion_results)

    while not _shutdown:
        try:
            ch.start_consuming()
        except pika.exceptions.AMQPConnectionError:
            if _shutdown:
                break
            log.warning("oc_worker_reconnecting")
            time.sleep(5)
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_motion_results, on_message_callback=on_message)

    log.info("oc_worker_stopped")
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
