"""
In-memory worker state registry.

Workers announce themselves via oc_results messages:
  worker_event: "online"    — worker started, ready to work
  worker_event: "offline"   — graceful shutdown (SIGTERM handled)
  worker_event: "heartbeat" — periodic keepalive (every 15s)

Status lifecycle:
  online  → idle (when worker comes online)
  idle    → processing (when job starts)
  processing → idle (when job completes)
  idle/processing → lost (no heartbeat in LOST_AFTER_S seconds)
  idle/processing/lost → offline (graceful shutdown received)

Workers are NEVER removed from the list — offline/lost workers remain
visible so you can see the history of what was connected.
"""
import re
import threading
import time

LOST_AFTER_S  = 45   # no heartbeat/activity → mark lost
CHECK_INTERVAL = 10  # seconds between liveness scans

_lock    = threading.Lock()
_workers: dict[str, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_type(worker_id: str) -> str:
    m = re.search(r'-(md|oc)-', worker_id)
    return m.group(1) if m else "unknown"


def _now() -> float:
    return time.time()


# ── Public API ─────────────────────────────────────────────────────────────────

def on_online(worker_id: str, worker_type: str | None = None):
    """Worker came online and is ready."""
    with _lock:
        existing = _workers.get(worker_id, {})
        _workers[worker_id] = {
            "worker_id":      worker_id,
            "type":           worker_type or _parse_type(worker_id),
            "status":         "idle",
            "job_id":         None,
            "registered_at":  existing.get("registered_at", _now()),  # keep first-seen time
            "idle_since":     _now(),
            "last_heartbeat": _now(),
            "index":          existing.get("index", len(_workers)),
        }


def on_offline(worker_id: str):
    """Worker gracefully shut down."""
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["status"]     = "offline"
            _workers[worker_id]["job_id"]     = None
            _workers[worker_id]["idle_since"] = None


def on_heartbeat(worker_id: str):
    """Worker sent a keepalive."""
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["last_heartbeat"] = _now()
            # If we had marked it lost, bring it back to idle
            if _workers[worker_id]["status"] == "lost":
                _workers[worker_id]["status"]     = "idle"
                _workers[worker_id]["idle_since"] = _now()


def update(worker_id: str, status: str, job_id=None):
    """
    Called by result_consumer on processing/idle transitions.
    status: 'processing' | 'idle'
    """
    with _lock:
        w = _workers.get(worker_id)
        if w is None:
            # Worker came online before the registry was listening — register it now
            _workers[worker_id] = {
                "worker_id":      worker_id,
                "type":           _parse_type(worker_id),
                "registered_at":  _now(),
                "index":          len(_workers),
            }
            w = _workers[worker_id]

        w["status"]         = status
        w["job_id"]         = job_id
        w["last_heartbeat"] = _now()
        if status == "idle":
            w["idle_since"] = _now()
        else:
            w["idle_since"] = None


def get_all() -> list[dict]:
    """Return all known workers, sorted by type then index."""
    with _lock:
        return sorted(_workers.values(), key=lambda w: (w.get("type", ""), w.get("index", 0)))


# ── Liveness monitor ──────────────────────────────────────────────────────────

def _liveness_loop():
    while True:
        time.sleep(CHECK_INTERVAL)
        now = _now()
        with _lock:
            for w in _workers.values():
                if w.get("status") in ("offline",):
                    continue  # already terminal
                last_hb = w.get("last_heartbeat", 0)
                if last_hb and (now - last_hb) > LOST_AFTER_S:
                    w["status"] = "offline"  # no heartbeat = gone, remove from UI
                    w["job_id"] = None


def start_liveness_monitor():
    t = threading.Thread(target=_liveness_loop, daemon=True, name="worker-liveness")
    t.start()
