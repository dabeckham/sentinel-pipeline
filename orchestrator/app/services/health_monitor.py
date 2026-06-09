"""
Pipeline health monitor — daemon thread started from lifespan.

Polls every POLL_INTERVAL seconds and checks for stalled OC processing:

  UNHEALTHY conditions (any one triggers the circuit breaker):
    • md_complete jobs older than STUCK_THRESHOLD with 0 OC consumers on
      the motion_results queue
    • dlx.motion_results depth >= DLX_WARN_DEPTH (OC workers are crashing)

  On UNHEALTHY:
    • Open the ingest circuit breaker — watcher saves files to DB as
      'pending' but does NOT publish to the ingest queue.  MD worker is
      spared from doing work that will just pile up with no OC to consume.
    • Run diagnosis (consumer count, DLX depth, stuck job IDs, ingest mount)
    • Log structured error + broadcast pipeline_alert via WebSocket
    • Re-alert every ALERT_REPEAT_INTERVAL if still stuck

  On RECOVERY (was unhealthy, now healthy):
    • Close the circuit breaker
    • Log recovery + broadcast pipeline_recovery via WebSocket
    • Promote all 'pending' jobs → 'queued' and publish to ingest queue
"""

import threading
import time
from datetime import datetime, timezone, timedelta

import pika
import structlog

log = structlog.get_logger()

# ── Thresholds ────────────────────────────────────────────────────────────────
POLL_INTERVAL         = 30    # seconds between checks
STUCK_THRESHOLD       = 180   # seconds before an md_complete job is considered stuck
DLX_WARN_DEPTH        = 3     # dead-letter messages before treating as unhealthy
ALERT_REPEAT_INTERVAL = 300   # seconds before re-broadcasting if still unhealthy

# ── Circuit breaker ───────────────────────────────────────────────────────────
_ingest_blocked = False
_block_reason   = ""
_block_lock     = threading.Lock()


def is_ingest_blocked() -> tuple[bool, str]:
    """Return (blocked, reason).  Called by the watcher before publishing."""
    with _block_lock:
        return _ingest_blocked, _block_reason


def _set_blocked(reason: str) -> bool:
    """Set circuit breaker open.  Returns True if this is a new blockage."""
    global _ingest_blocked, _block_reason
    with _block_lock:
        new = not _ingest_blocked
        _ingest_blocked = True
        _block_reason   = reason
        return new


def _set_healthy() -> bool:
    """Clear circuit breaker.  Returns True if this is a new recovery."""
    global _ingest_blocked, _block_reason
    with _block_lock:
        was_blocked     = _ingest_blocked
        _ingest_blocked = False
        _block_reason   = ""
        return was_blocked


# ── RabbitMQ probe ────────────────────────────────────────────────────────────

def _probe_rabbitmq(settings) -> dict:
    """
    Return stats about the motion_results queue and its DLX.
    Keys: consumers (int), depth (int), dlx_depth (int).
    All -1 on connection failure.
    """
    try:
        conn = pika.BlockingConnection(settings.rabbitmq_params())
        ch   = conn.channel()

        mq = ch.queue_declare(queue=settings.queue_motion_results, passive=True)
        consumers = mq.method.consumer_count
        depth     = mq.method.message_count

        dlx_depth = 0
        try:
            dlx = ch.queue_declare(queue="dlx.motion_results", passive=True)
            dlx_depth = dlx.method.message_count
        except Exception:
            pass  # queue may not exist yet

        conn.close()
        return {"consumers": consumers, "depth": depth, "dlx_depth": dlx_depth, "error": None}

    except Exception as exc:
        return {"consumers": -1, "depth": -1, "dlx_depth": -1, "error": str(exc)}


# ── DB probe ──────────────────────────────────────────────────────────────────

def _probe_stuck_jobs(db_factory) -> list[dict]:
    """Return md_complete jobs whose md_completed_at is older than STUCK_THRESHOLD."""
    from app.models.job import Job, JobStatus
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_THRESHOLD)
    db = db_factory()
    try:
        rows = (
            db.query(Job.id, Job.file_path, Job.md_completed_at)
            .filter(
                Job.status == JobStatus.md_complete,
                Job.md_completed_at < cutoff,
            )
            .all()
        )
        return [{"id": r.id, "file": r.file_path, "since": r.md_completed_at} for r in rows]
    except Exception:
        return []
    finally:
        db.close()


# ── Pending job promotion ─────────────────────────────────────────────────────

def _promote_pending_jobs(db_factory, settings) -> int:
    """
    Promote all 'pending' jobs to 'queued' and publish them to the ingest
    queue.  Called when the circuit breaker is cleared.
    """
    from app.models.job import Job, JobStatus
    from app.services import amqp

    db = db_factory()
    promoted = 0
    try:
        pending = db.query(Job).filter(Job.status == JobStatus.pending).all()
        for job in pending:
            job.status = JobStatus.queued
            amqp.publish(settings.queue_ingest, {
                "job_id":      job.id,
                "video_path":  job.file_path,
                "source_type": "circuit_breaker_release",
                "options":     {},
            })
            promoted += 1
        db.commit()
    except Exception:
        log.exception("health_monitor_promote_pending_error")
        db.rollback()
    finally:
        db.close()

    if promoted:
        log.info("health_monitor_pending_promoted", count=promoted)
    return promoted


# ── Diagnosis ─────────────────────────────────────────────────────────────────

def _diagnose(mq: dict, stuck: list[dict]) -> str:
    """Build a plain-English diagnosis from probe results."""
    parts = []

    if mq["error"]:
        parts.append(f"Cannot reach RabbitMQ: {mq['error']}")
    elif mq["consumers"] == 0:
        parts.append(
            "No OC workers connected to the motion_results queue — "
            "check 'docker logs sentinel-oc-worker' and 'docker ps'"
        )
    elif mq["consumers"] > 0 and mq["depth"] > 10:
        parts.append(
            f"motion_results queue depth is {mq['depth']} with only "
            f"{mq['consumers']} OC consumer(s) — workers may be overloaded or stuck"
        )

    if mq["dlx_depth"] > 0:
        parts.append(
            f"{mq['dlx_depth']} job(s) dead-lettered in dlx.motion_results — "
            "OC workers are crashing mid-job; check worker logs for errors, "
            "then use POST /api/dlx/requeue?queue=dlx.motion_results to retry"
        )

    if stuck:
        ids = [s["id"] for s in stuck[:5]]
        extra = f" (+{len(stuck)-5} more)" if len(stuck) > 5 else ""
        parts.append(
            f"{len(stuck)} job(s) stuck in md_complete for "
            f">{STUCK_THRESHOLD // 60}m: jobs {ids}{extra}"
        )

    return " | ".join(parts) if parts else "Unknown pipeline issue"


# ── WebSocket broadcast ───────────────────────────────────────────────────────

def _broadcast(event_type: str, payload: dict):
    try:
        from app.api.ws import broadcast
        from app.services.event_loop import get_loop
        import asyncio
        loop = get_loop()
        if loop:
            asyncio.run_coroutine_threadsafe(broadcast({"type": event_type, **payload}), loop)
    except Exception:
        pass


# ── Monitor loop ──────────────────────────────────────────────────────────────

def _monitor_loop():
    from app.config import get_settings
    from app.db import SessionLocal

    settings       = get_settings()
    last_alert_at  = 0.0

    log.info("health_monitor_running",
             poll_interval=POLL_INTERVAL,
             stuck_threshold_s=STUCK_THRESHOLD)

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            mq    = _probe_rabbitmq(settings)
            stuck = _probe_stuck_jobs(SessionLocal)

            # Determine health: unhealthy if OC queue has no consumers AND
            # jobs are stuck, OR if DLX is filling up with failed jobs.
            oc_absent  = mq["consumers"] == 0 and len(stuck) > 0
            dlx_backed = mq["dlx_depth"] >= DLX_WARN_DEPTH

            if oc_absent or dlx_backed:
                diagnosis   = _diagnose(mq, stuck)
                is_new_fault = _set_blocked(diagnosis)

                now = time.time()
                if is_new_fault or (now - last_alert_at) > ALERT_REPEAT_INTERVAL:
                    last_alert_at = now
                    log.error(
                        "pipeline_stalled",
                        diagnosis=diagnosis,
                        oc_consumers=mq["consumers"],
                        queue_depth=mq["depth"],
                        dlx_depth=mq["dlx_depth"],
                        stuck_job_ids=[s["id"] for s in stuck],
                        ingest_blocked=True,
                    )
                    _broadcast("pipeline_alert", {
                        "diagnosis":    diagnosis,
                        "oc_consumers": mq["consumers"],
                        "queue_depth":  mq["depth"],
                        "dlx_depth":    mq["dlx_depth"],
                        "stuck_jobs":   [s["id"] for s in stuck],
                    })

            else:
                recovered = _set_healthy()
                if recovered:
                    log.info("pipeline_recovered",
                             oc_consumers=mq["consumers"],
                             queue_depth=mq["depth"])
                    promoted = _promote_pending_jobs(SessionLocal, settings)
                    _broadcast("pipeline_recovery", {
                        "oc_consumers":    mq["consumers"],
                        "pending_released": promoted,
                    })

        except Exception:
            log.exception("health_monitor_loop_error")


def start_health_monitor():
    t = threading.Thread(target=_monitor_loop, daemon=True, name="health-monitor")
    t.start()
    log.info("health_monitor_started",
             poll_s=POLL_INTERVAL, stuck_threshold_s=STUCK_THRESHOLD)
