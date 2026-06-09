"""
Worker lifecycle event publisher.

Publishes online/offline/heartbeat messages to the oc_results queue so the
orchestrator can track which workers are alive and what they're doing.

Also polls the orchestrator on each heartbeat to check for suspension.
If suspended, sets self.suspended = True — the main loop checks this before
accepting new jobs and nacks+requeues them while suspended.

Uses a SEPARATE pika connection from the main consumer — pika BlockingConnection
is not thread-safe, so the heartbeat thread needs its own channel.
"""
import json
import threading
import time

import pika
import structlog

log = structlog.get_logger()

HEARTBEAT_INTERVAL = 15   # seconds between heartbeats


class WorkerEventPublisher:
    def __init__(self, worker_id: str, worker_type: str, device: str, settings):
        self._worker_id   = worker_id
        self._worker_type = worker_type
        self._device      = device.lower()
        self._settings    = settings
        self._conn        = None
        self._ch          = None
        self._lock        = threading.Lock()
        self._shutdown    = threading.Event()
        self._hb_thread   = None
        self._suspended   = False
        # Connect eagerly so the first publish (online event) doesn't race
        # against job delivery on the already-connected main channel.
        try:
            self._ensure_connected()
        except Exception:
            pass  # will retry in _publish

    @property
    def suspended(self) -> bool:
        return self._suspended

    # ── Connection ────────────────────────────────────────────────────────────

    def _ensure_connected(self):
        if self._conn is None or self._conn.is_closed:
            self._conn = pika.BlockingConnection(self._settings.rabbitmq_params())
            self._ch   = self._conn.channel()

    def _publish(self, payload: dict):
        with self._lock:
            for attempt in range(3):
                try:
                    self._ensure_connected()
                    self._ch.basic_publish(
                        exchange="",
                        routing_key=self._settings.queue_oc_results,
                        body=json.dumps(payload),
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            content_type="application/json",
                        ),
                    )
                    return
                except Exception as exc:
                    log.warning("worker_event_publish_retry",
                                attempt=attempt + 1, error=str(exc))
                    self._conn = None
                    time.sleep(2 ** attempt)

    # ── Public API ────────────────────────────────────────────────────────────

    def online(self):
        """Call once immediately after connecting to RabbitMQ."""
        self._publish({
            "worker_event": "online",
            "worker_id":    self._worker_id,
            "worker_type":  self._worker_type,
            "device":       self._device,
        })
        log.info("worker_event_online", worker_id=self._worker_id, device=self._device)
        self._start_heartbeat()

    def offline(self):
        """Call from the SIGTERM handler before exiting."""
        self._shutdown.set()
        self._publish({
            "worker_event": "offline",
            "worker_id":    self._worker_id,
        })
        log.info("worker_event_offline", worker_id=self._worker_id)

    def heartbeat(self):
        """Publish a heartbeat manually (e.g. mid-job to reset the timeout)."""
        self._publish({
            "worker_event": "heartbeat",
            "worker_id":    self._worker_id,
        })

    # ── Suspension polling ────────────────────────────────────────────────────

    def _poll_suspension(self):
        """
        Ask the orchestrator if this worker is suspended.
        Runs on each heartbeat cycle (~15s). Fails open (maintains current state)
        if the orchestrator is unreachable.
        """
        import urllib.request as _req
        try:
            url = f"{self._settings.orchestrator_url}/api/internal/workers/{self._worker_id}/status"
            resp = json.loads(_req.urlopen(url, timeout=3).read())
            self._suspended = resp.get("suspended", False)
        except Exception:
            pass  # orchestrator unreachable — keep current state

    # ── Background heartbeat ──────────────────────────────────────────────────

    def _start_heartbeat(self):
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"heartbeat-{self._worker_id}",
        )
        self._hb_thread.start()

    def _heartbeat_loop(self):
        while not self._shutdown.wait(HEARTBEAT_INTERVAL):
            try:
                self.heartbeat()
                self._poll_suspension()
            except Exception:
                pass
