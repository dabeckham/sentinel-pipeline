"""MD Worker — Motion Detection (MOG2)"""
import json
import signal
import time
import pika
import structlog
import setproctitle

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


def process_job(msg: dict, ch, method):
    settings = get_settings()
    job_id = msg["job_id"]
    video_path = msg["video_path"]
    log.info("md_job_start", job_id=job_id, video_path=video_path)

    try:
        motion_frames = detect_motion(video_path)
        log.info("md_motion_detected", job_id=job_id, motion_frames=len(motion_frames))

        for i, mf in enumerate(motion_frames):
            is_final = (i == len(motion_frames) - 1)

            # Crops travel in the message body (base64 JPEG) — no MinIO round-trip (issue #13)
            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_motion_results,
                body=json.dumps({
                    "job_id": job_id,
                    "frame_index": mf.frame_index,
                    "timestamp_ms": mf.timestamp_ms,
                    "bounding_boxes": mf.bounding_boxes,
                    "crops_b64": mf.crops_b64,
                    "is_final": is_final,
                }),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                ),
            )

        if not motion_frames:
            # No motion detected — still send a final message to close out the job
            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_motion_results,
                body=json.dumps({
                    "job_id": job_id,
                    "frame_index": 0,
                    "timestamp_ms": 0,
                    "bounding_boxes": [],
                    "crops_b64": [],
                    "is_final": True,
                }),
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
