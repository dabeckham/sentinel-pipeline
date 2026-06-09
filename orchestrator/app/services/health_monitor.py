"""
Pipeline health monitor — daemon thread started from lifespan.

Polls every POLL_INTERVAL seconds and checks for stalled pipeline stages:

  UNHEALTHY conditions (any one triggers the watcher pause):
    • Queued jobs older than STUCK_THRESHOLD with 0 MD consumers on ingest queue
      (MD workers are down — jobs will never be picked up)
    • md_complete jobs older than STUCK_THRESHOLD
      (OC workers are down or not keeping up)
    • dlx.motion_results depth >= DLX_WARN_DEPTH
      (OC workers are crashing)

  On UNHEALTHY:
    • Pause the file watcher — no new files enter the pipeline while broken
    • Log structured error + broadcast pipeline_alert via WebSocket
    • Re-alert every ALERT_REPEAT_INTERVAL if still stuck

  On RECOVERY:
    • Resume the file watcher (unless manually_paused)
    • scan_ingest_missed() picks up files that arrived during the pause

  Persistent state:
    • manually_paused is written to the DB (pipeline_settings table) so it
      survives orchestrator restarts — the UI pause button sticks.
"""

import threading
import time
from datetime import datetime, timezone, timedelta

import pika
import structlog
from sqlalchemy import text

log = structlog.get_logger()

# ── Thresholds ────────────────────────────────────────────────────────────────
POLL_INTERVAL         = 30    # seconds between checks
STUCK_THRESHOLD       = 180   # seconds before a stuck job triggers a pause
DLX_WARN_DEPTH        = 15    # dead-letter messages before treating as unhealthy (corrupt files are normal)
ALERT_REPEAT_INTERVAL = 300   # seconds before re-broadcasting if still unhealthy
INGEST_QUEUE_MAX      = 50    # pause watcher when queued jobs exceed this count


# ── Persistent settings (survives restarts) ───────────────────────────────────
# Uses the pipeline_settings table created by migration 0008.

def _load_manually_paused(db_factory) -> bool:
    db = db_factory()
    try:
        row = db.execute(
            text("SELECT value FROM pipeline_settings WHERE key = 'manually_paused'")
        ).fetchone()
        return row[0].lower() == "true" if row else False
    except Exception:
        log.warning("pipeline_settings_load_error")
        return False
    finally:
        db.close()


def _save_manually_paused(db_factory, value: bool):
    db = db_factory()
    try:
        db.execute(text(
            "INSERT INTO pipeline_settings (key, value) VALUES ('manually_paused', :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ), {"v": "true" if value else "false"})
        db.commit()
    except Exception:
        log.exception("pipeline_settings_save_error")
        db.rollback()
    finally:
        db.close()


# ── RabbitMQ probes ───────────────────────────────────────────────────────────

def _probe_rabbitmq(settings) -> dict:
    """
    Return stats for the motion_results, ingest, and DLX queues.
    All counts are -1 on connection failure.
    """
    try:
        conn = pika.BlockingConnection(settings.rabbitmq_params())
        ch   = conn.channel()

        mq = ch.queue_declare(queue=settings.queue_motion_results, passive=True)
        oc_consumers = mq.method.consumer_count
        oc_depth     = mq.method.message_count

        ingest_consumers = 0
        ingest_depth     = 0
        try:
            iq = ch.queue_declare(queue=settings.queue_ingest, passive=True)
            ingest_consumers = iq.method.consumer_count
            ingest_depth     = iq.method.message_count
        except Exception:
            pass

        dlx_depth = 0
        try:
            dlx = ch.queue_declare(queue="dlx.motion_results", passive=True)
            dlx_depth = dlx.method.message_count
        except Exception:
            pass

        conn.close()
        return {
            "oc_consumers":     oc_consumers,
            "oc_depth":         oc_depth,
            "ingest_consumers": ingest_consumers,
            "ingest_depth":     ingest_depth,
            "dlx_depth":        dlx_depth,
            "error":            None,
        }

    except Exception as exc:
        return {
            "oc_consumers": -1, "oc_depth": -1,
            "ingest_consumers": -1, "ingest_depth": -1,
            "dlx_depth": -1, "error": str(exc),
        }


# ── DB probes ─────────────────────────────────────────────────────────────────

def _probe_stuck_jobs(db_factory) -> dict:
    """
    Return jobs stuck in each pipeline stage past STUCK_THRESHOLD,
    plus the total count of queued jobs (to catch queue flooding early).
    """
    from app.models.job import Job, JobStatus
    from sqlalchemy import func
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_THRESHOLD)
    db = db_factory()
    try:
        # Jobs stuck in queued (MD workers not picking them up)
        queued_stuck = (
            db.query(Job.id, Job.file_path, Job.created_at)
            .filter(Job.status == JobStatus.queued, Job.created_at < cutoff)
            .all()
        )
        # Total queued count (for flood detection — no threshold wait)
        queued_total = (
            db.query(func.count(Job.id))
            .filter(Job.status == JobStatus.queued)
            .scalar() or 0
        )
        # Jobs stuck in md_complete (OC workers not picking them up)
        oc_rows = (
            db.query(Job.id, Job.file_path, Job.md_completed_at)
            .filter(Job.status == JobStatus.md_complete, Job.md_completed_at < cutoff)
            .all()
        )
        return {
            "queued_stuck": [{"id": r.id, "file": r.file_path} for r in queued_stuck],
            "queued_total": queued_total,
            "md_complete":  [{"id": r.id, "file": r.file_path} for r in oc_rows],
        }
    except Exception:
        return {"queued_stuck": [], "queued_total": 0, "md_complete": []}
    finally:
        db.close()


# ── Diagnosis ─────────────────────────────────────────────────────────────────

def _diagnose(mq: dict, stuck: dict) -> str:
    parts = []

    if mq["error"]:
        parts.append(f"Cannot reach RabbitMQ: {mq['error']}")
        return " | ".join(parts)

    stuck_queued      = stuck["queued_stuck"]
    queued_total      = stuck["queued_total"]
    stuck_md_complete = stuck["md_complete"]

    # Queue flood — too many queued jobs (DB or RabbitMQ depth)
    ingest_depth = mq.get("ingest_depth", 0)
    if (queued_total >= INGEST_QUEUE_MAX or ingest_depth >= INGEST_QUEUE_MAX) and not stuck_queued:
        parts.append(
            f"Ingest queue flooded — {queued_total} DB-queued jobs, "
            f"{ingest_depth} in RabbitMQ ingest queue "
            f"(threshold: {INGEST_QUEUE_MAX}). Watcher paused to let workers catch up."
        )

    # MD worker stage — stuck jobs
    if stuck_queued:
        ids = [s["id"] for s in stuck_queued[:5]]
        extra = f" (+{len(stuck_queued)-5} more)" if len(stuck_queued) > 5 else ""
        if mq["ingest_consumers"] == 0:
            parts.append(
                f"MD workers offline — {len(stuck_queued)} job(s) stuck in queued "
                f"for >{STUCK_THRESHOLD // 60}m with 0 ingest consumers: "
                f"jobs {ids}{extra}. Check 'docker logs sentinel-md-worker'"
            )
        else:
            parts.append(
                f"{len(stuck_queued)} job(s) stuck queued >{STUCK_THRESHOLD // 60}m "
                f"({mq['ingest_consumers']} MD worker(s) connected but not keeping up): "
                f"jobs {ids}{extra}"
            )

    # OC worker stage
    if stuck_md_complete:
        ids = [s["id"] for s in stuck_md_complete[:5]]
        extra = f" (+{len(stuck_md_complete)-5} more)" if len(stuck_md_complete) > 5 else ""
        if mq["oc_consumers"] == 0:
            parts.append(
                f"OC workers offline — {len(stuck_md_complete)} job(s) stuck in md_complete "
                f">{STUCK_THRESHOLD // 60}m with 0 OC consumers: "
                f"jobs {ids}{extra}. Check 'docker logs sentinel-oc-worker'"
            )
        else:
            parts.append(
                f"{len(stuck_md_complete)} job(s) stuck in md_complete "
                f">{STUCK_THRESHOLD // 60}m — OC backed up "
                f"({mq['oc_consumers']} worker(s), depth: {mq['oc_depth']}): "
                f"jobs {ids}{extra}"
            )

    if mq["dlx_depth"] >= DLX_WARN_DEPTH:
        parts.append(
            f"{mq['dlx_depth']} dead-lettered in dlx.motion_results — "
            "OC workers crashing; POST /api/dlx/requeue?queue=dlx.motion_results to retry"
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


# ── Monitor state ─────────────────────────────────────────────────────────────

class _MonitorState:
    """Mutable state shared between startup_health_check and _monitor_loop."""
    watcher_paused   = False
    manually_paused  = False   # loaded from DB on startup; written to DB on change
    last_alert_at    = 0.0
    diagnosis        = ""


_state = _MonitorState()


# ── Core check ────────────────────────────────────────────────────────────────

def _run_check(settings, db_factory):
    """
    Run one health check cycle.  Updates _state and pauses/resumes the
    watcher as needed.  Safe to call from any thread.
    """
    from app.services.watcher import pause_watcher, resume_watcher

    mq    = _probe_rabbitmq(settings)
    stuck = _probe_stuck_jobs(db_factory)

    stuck_queued      = stuck["queued_stuck"]
    queued_total      = stuck["queued_total"]
    stuck_md_complete = stuck["md_complete"]

    # Queue flooded — DB queued count OR RabbitMQ ingest depth exceeds threshold
    queue_flooded = queued_total >= INGEST_QUEUE_MAX or mq["ingest_depth"] >= INGEST_QUEUE_MAX
    # MD workers down: jobs stuck in queued AND 0 ingest consumers
    md_backed_up  = len(stuck_queued) > 0 and mq["ingest_consumers"] == 0
    # OC workers down/slow: jobs stuck in md_complete
    oc_backed_up  = len(stuck_md_complete) > 0
    dlx_backed    = mq["dlx_depth"] >= DLX_WARN_DEPTH

    is_unhealthy = queue_flooded or md_backed_up or oc_backed_up or dlx_backed

    if is_unhealthy:
        diagnosis         = _diagnose(mq, stuck)
        _state.diagnosis  = diagnosis
        is_new_fault      = not _state.watcher_paused

        if is_new_fault:
            pause_watcher()
            _state.watcher_paused = True

        now = time.time()
        if is_new_fault or (now - _state.last_alert_at) >= ALERT_REPEAT_INTERVAL:
            _state.last_alert_at = now
            all_stuck_ids = [s["id"] for s in stuck_queued] + [s["id"] for s in stuck_md_complete]
            queued_total  = stuck["queued_total"]
            log.error(
                "pipeline_stalled",
                diagnosis=diagnosis,
                oc_consumers=mq["oc_consumers"],
                ingest_consumers=mq["ingest_consumers"],
                queue_depth=mq["oc_depth"],
                dlx_depth=mq["dlx_depth"],
                stuck_job_ids=all_stuck_ids,
                watcher_paused=True,
            )
            _broadcast("pipeline_alert", {
                "diagnosis":        diagnosis,
                "oc_consumers":     mq["oc_consumers"],
                "ingest_consumers": mq["ingest_consumers"],
                "queue_depth":      mq["oc_depth"],
                "dlx_depth":        mq["dlx_depth"],
                "stuck_jobs":       all_stuck_ids,
            })
    else:
        if _state.watcher_paused and not _state.manually_paused:
            resume_watcher()
            _state.watcher_paused = False
            _state.last_alert_at  = 0.0
            _state.diagnosis      = ""
            log.info("pipeline_recovered",
                     oc_consumers=mq["oc_consumers"],
                     ingest_consumers=mq["ingest_consumers"])
            _broadcast("pipeline_recovery", {
                "oc_consumers":     mq["oc_consumers"],
                "ingest_consumers": mq["ingest_consumers"],
            })


# ── Public API ────────────────────────────────────────────────────────────────

def get_pipeline_status() -> dict:
    return {
        "watcher_paused":          _state.watcher_paused,
        "watcher_manually_paused": _state.manually_paused,
        "diagnosis":               _state.diagnosis if _state.watcher_paused else None,
    }


def manual_pause_watcher():
    """UI-triggered pause. Persists to DB so it survives restarts."""
    from app.services.watcher import pause_watcher
    from app.db import SessionLocal
    _state.manually_paused = True
    _save_manually_paused(SessionLocal, True)
    if not _state.watcher_paused:
        pause_watcher()
        _state.watcher_paused = True
    log.info("file_watcher_manually_paused")


def manual_resume_watcher():
    """UI-triggered resume. Clears persisted pause flag."""
    from app.services.watcher import resume_watcher
    from app.db import SessionLocal
    _state.manually_paused = False
    _save_manually_paused(SessionLocal, False)
    if _state.watcher_paused:
        resume_watcher()
        _state.watcher_paused = False
    log.info("file_watcher_manually_resumed")


def startup_health_check():
    """
    Synchronous check run during lifespan startup, BEFORE the file watcher
    starts.  Also restores the manually_paused flag from the DB so a UI
    pause survives an orchestrator restart.
    """
    from app.config import get_settings
    from app.db import SessionLocal

    log.info("health_monitor_startup_check")
    try:
        # Restore persisted pause state first
        _state.manually_paused = _load_manually_paused(SessionLocal)
        if _state.manually_paused:
            _state.watcher_paused = True
            log.info("file_watcher_startup_paused_by_user")

        _run_check(get_settings(), SessionLocal)
    except Exception:
        log.exception("health_monitor_startup_check_error")


def _monitor_loop():
    from app.config import get_settings
    from app.db import SessionLocal

    settings = get_settings()
    log.info("health_monitor_running",
             poll_interval=POLL_INTERVAL,
             stuck_threshold_s=STUCK_THRESHOLD)

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            _run_check(settings, SessionLocal)
        except Exception:
            log.exception("health_monitor_loop_error")


def start_health_monitor():
    t = threading.Thread(target=_monitor_loop, daemon=True, name="health-monitor")
    t.start()
    log.info("health_monitor_started",
             poll_s=POLL_INTERVAL, stuck_threshold_s=STUCK_THRESHOLD)
