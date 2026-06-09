"""
Metrics broadcaster — daemon thread that pushes queue depths and worker
states to all connected WebSocket clients every PUSH_INTERVAL seconds.

Message type: 'queue_metrics'
{
  "type": "queue_metrics",
  "queues": {
    "ingest":         {"depth": 17, "consumers": 4},
    "motion_results": {"depth": 3,  "consumers": 4},
    "oc_results":     {"depth": 0,  "consumers": 1},
    "dlx_ingest":     {"depth": 0,  "consumers": 0}
  },
  "workers": [
    {"worker_id": "...", "type": "md", "status": "processing", "job_id": 1811},
    ...
  ]
}
"""
import asyncio
import threading
import time

import pika
import structlog

log = structlog.get_logger()

PUSH_INTERVAL = 3  # seconds between broadcasts


class _QueueProber:
    """Persistent pika connection for lightweight passive queue declares."""

    def __init__(self):
        self._conn = None
        self._ch   = None
        self._lock = threading.Lock()

    def probe(self, settings) -> dict:
        with self._lock:
            try:
                if self._conn is None or self._conn.is_closed:
                    self._conn = pika.BlockingConnection(settings.rabbitmq_params())
                    self._ch   = self._conn.channel()

                def _q(name):
                    try:
                        r = self._ch.queue_declare(queue=name, passive=True)
                        return {"depth": r.method.message_count,
                                "consumers": r.method.consumer_count}
                    except Exception:
                        self._conn = None  # force reconnect next call
                        return {"depth": -1, "consumers": 0}

                return {
                    "ingest":              _q("ingest"),
                    "motion_results":      _q("motion_results"),
                    "oc_results":          _q("oc_results"),
                    "dlx_ingest":          _q("dlx.ingest"),
                    "dlx_motion_results":  _q("dlx.motion_results"),
                }
            except Exception as exc:
                log.warning("metrics_probe_error", error=str(exc))
                self._conn = None
                return {}


_prober = _QueueProber()


def _push_once(settings):
    from app.services import worker_registry
    from app.api.ws import broadcast
    from app.services.event_loop import get_loop

    queues  = _prober.probe(settings)
    workers = worker_registry.get_all()

    loop = get_loop()
    if loop is None:
        return

    asyncio.run_coroutine_threadsafe(
        broadcast({
            "type":    "queue_metrics",
            "queues":  queues,
            "workers": workers,
        }),
        loop,
    )


def _broadcaster_loop():
    from app.config import get_settings
    settings = get_settings()
    log.info("metrics_broadcaster_started", interval_s=PUSH_INTERVAL)

    while True:
        time.sleep(PUSH_INTERVAL)
        try:
            _push_once(settings)
        except Exception:
            log.exception("metrics_broadcaster_error")


def start_metrics_broadcaster():
    t = threading.Thread(target=_broadcaster_loop, daemon=True, name="metrics-broadcaster")
    t.start()
