"""Transcode Worker — source clip → adaptive H.264 rendition (NVENC).

Consumes one message per requested rendition from the transcode_jobs queue:
    {
      "job_id":      int,
      "source_path": str,   # absolute path on the shared /ingest mount
      "object_name": str,   # renditions/{job_id}/{rung}p.mp4 in the snapshots bucket
      "height":      int,   # target rung height (downscale-only)
      "bitrate_k":   int
    }
It transcodes to a temp file, uploads to MinIO, and acks. Idempotent: if the
rendition object already exists it acks without re-encoding (duplicate requests
from impatient clicks are cheap). One worker, pinned to one GPU, handles the
on-demand playback transcodes — short and infrequent.
"""
import json
import os
import signal
import socket
import tempfile
import time

import pika
import setproctitle
import structlog

from worker.config import get_settings
from worker.minio_client import get_minio, object_exists
from worker.transcode import transcode

log = structlog.get_logger()

WORKER_ID = f"{socket.gethostname()}-transcode-{os.getpid()}"


def _connect(settings):
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            # Durable queue so requests survive a broker blip; one job at a time.
            ch.queue_declare(queue=settings.queue_transcode, durable=True)
            ch.basic_qos(prefetch_count=1)
            log.info("transcode_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("transcode_amqp_retry", attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def process(msg: dict, settings) -> None:
    job_id      = msg["job_id"]
    src         = msg["source_path"]
    object_name = msg["object_name"]
    height      = int(msg["height"])
    bitrate_k   = int(msg["bitrate_k"])

    bucket = settings.minio_bucket_snapshots

    if object_exists(bucket, object_name):
        log.info("transcode_skip_exists", job_id=job_id, object=object_name)
        return

    if not os.path.isfile(src):
        # Source purged or not on this node's mount — nothing we can do.
        log.warning("transcode_source_missing", job_id=job_id, src=src)
        return

    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "rendition.mp4")
        transcode(
            src, out, height, bitrate_k,
            preset=settings.nvenc_preset,
            hwaccel_decode=settings.use_hwaccel_decode,
            timeout_s=settings.transcode_timeout_s,
        )
        get_minio().fput_object(bucket, object_name, out, content_type="video/mp4")
        size = os.path.getsize(out)
    log.info("transcode_done", job_id=job_id, object=object_name,
             height=height, bytes=size, elapsed_s=round(time.time() - t0, 2))


def main():
    setproctitle.setproctitle(f"sentinel-transcode-worker [{WORKER_ID}]")
    settings = get_settings()
    log.info("transcode_worker_starting", worker_id=WORKER_ID,
             rabbitmq_host=settings.rabbitmq_host, queue=settings.queue_transcode,
             code_version=settings.worker_code_version, agent_id=settings.agent_id)

    conn, ch = _connect(settings)
    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("transcode_worker_sigterm", worker_id=WORKER_ID)
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
            process(msg, settings)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            log.exception("transcode_job_error")
            # Don't requeue: a bad/failed transcode would loop forever. The
            # client simply re-requests (and gets re-enqueued) on its next poll.
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                pass

    ch.basic_consume(queue=settings.queue_transcode, on_message_callback=on_message)
    log.info("transcode_worker_consuming", queue=settings.queue_transcode, worker_id=WORKER_ID)

    while not _shutdown:
        try:
            ch.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.AMQPError,
                pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError):
            if _shutdown:
                break
            log.warning("transcode_worker_reconnecting", worker_id=WORKER_ID)
            time.sleep(5)
            try:
                conn.close()
            except Exception:
                pass
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_transcode, on_message_callback=on_message)

    log.info("transcode_worker_stopped", worker_id=WORKER_ID)
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
