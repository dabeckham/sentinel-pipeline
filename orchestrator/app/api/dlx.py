"""
DLX (dead-letter exchange) management endpoints.

POST /api/dlx/requeue?queue=<dlx_queue_name>&limit=<n>
  Moves up to `limit` messages from a DLX queue back to its source queue.
  Admin only.

GET /api/dlx/counts
  Returns message counts for all known DLX queues.
"""
import json
import pika
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import require_admin
from app.config import get_settings
from app.models.user import User

log = structlog.get_logger()
router = APIRouter(prefix="/dlx", tags=["dlx"])

# Map DLX queue name → source queue name
_DLX_SOURCE_MAP = {
    "dlx.ingest":          "ingest",
    "dlx.motion_results":  "motion_results",
    "dlx.oc_results":      "oc_results",
}


def _open_channel() -> tuple[pika.BlockingConnection, pika.channel.Channel]:
    settings = get_settings()
    conn = pika.BlockingConnection(settings.rabbitmq_params())
    ch = conn.channel()
    return conn, ch


@router.get("/counts")
def dlx_counts(_: User = Depends(require_admin)):
    """Return message counts for all DLX queues."""
    conn, ch = _open_channel()
    try:
        counts = {}
        for dlx_queue in _DLX_SOURCE_MAP:
            try:
                q = ch.queue_declare(queue=dlx_queue, passive=True)
                counts[dlx_queue] = q.method.message_count
            except pika.exceptions.ChannelClosedByBroker:
                counts[dlx_queue] = 0
                # Channel closed by broker on passive declare of non-existent queue
                conn, ch = _open_channel()
        return counts
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.post("/requeue")
def dlx_requeue(
    queue: str = Query(..., description="DLX queue name, e.g. dlx.ingest"),
    limit: int = Query(100, ge=1, le=10000, description="Max messages to requeue"),
    _: User = Depends(require_admin),
):
    """
    Move up to `limit` messages from the specified DLX queue back to its
    source queue so they can be reprocessed.
    """
    if queue not in _DLX_SOURCE_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown DLX queue '{queue}'. Valid: {list(_DLX_SOURCE_MAP.keys())}",
        )

    source_queue = _DLX_SOURCE_MAP[queue]
    conn, ch = _open_channel()
    requeued = 0
    errors = 0

    try:
        for _ in range(limit):
            method, props, body = ch.basic_get(queue=queue, auto_ack=False)
            if method is None:
                break  # queue empty
            try:
                # Strip x-death headers so the message isn't re-dead-lettered immediately
                headers = (props.headers or {}).copy()
                headers.pop("x-death", None)
                headers.pop("x-first-death-exchange", None)
                headers.pop("x-first-death-queue", None)
                headers.pop("x-first-death-reason", None)

                new_props = pika.BasicProperties(
                    delivery_mode=2,
                    content_type=props.content_type or "application/json",
                    headers=headers,
                )
                ch.basic_publish(
                    exchange="",
                    routing_key=source_queue,
                    body=body,
                    properties=new_props,
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
                requeued += 1
            except Exception:
                log.exception("dlx_requeue_message_error")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                errors += 1

        log.info("dlx_requeue_done",
                 dlx_queue=queue, source_queue=source_queue,
                 requeued=requeued, errors=errors)
        return {"dlx_queue": queue, "source_queue": source_queue,
                "requeued": requeued, "errors": errors}
    finally:
        try:
            conn.close()
        except Exception:
            pass
