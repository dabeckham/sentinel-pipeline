"""MD Worker — Motion Detection (MOG2)"""
import json
import time
import pika
import structlog

from worker.config import get_settings
from worker.motion import detect_motion
from worker.minio_client import upload_crop

log = structlog.get_logger()


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            params = pika.URLParameters(settings.rabbitmq_url)
            params.heartbeat = 60
            conn = pika.BlockingConnection(params)
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
            crop_paths = []

            for box_idx, (bbox, crop) in enumerate(zip(mf.bounding_boxes, mf.crops)):
                object_name = f"{job_id}/frame_{mf.frame_index:06d}_box_{box_idx:03d}.jpg"
                try:
                    upload_crop(settings.minio_bucket_crops, object_name, crop)
                    crop_paths.append(object_name)
                except Exception:
                    log.exception("md_crop_upload_error", job_id=job_id, object_name=object_name)
                    crop_paths.append(None)

            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_motion_results,
                body=json.dumps({
                    "job_id": job_id,
                    "frame_index": mf.frame_index,
                    "timestamp_ms": mf.timestamp_ms,
                    "bounding_boxes": mf.bounding_boxes,
                    "crop_paths": crop_paths,
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
                    "crop_paths": [],
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
    settings = get_settings()
    log.info("md_worker_starting",
             rabbitmq_host=settings.rabbitmq_host,
             queue=settings.queue_ingest)

    conn, ch = _connect(settings)

    def on_message(ch, method, _props, body):
        try:
            msg = json.loads(body)
            process_job(msg, ch, method)
        except Exception:
            log.exception("md_message_parse_error")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    ch.basic_consume(queue=settings.queue_ingest, on_message_callback=on_message)
    log.info("md_worker_consuming", queue=settings.queue_ingest)

    while True:
        try:
            ch.start_consuming()
        except pika.exceptions.AMQPConnectionError:
            log.warning("md_worker_reconnecting")
            time.sleep(5)
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_ingest, on_message_callback=on_message)


if __name__ == "__main__":
    main()
