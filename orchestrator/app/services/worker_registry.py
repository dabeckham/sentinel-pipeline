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
  idle/processing → offline (no heartbeat in LOST_AFTER_S seconds)
  idle/processing/offline → offline (graceful shutdown received)

Workers are NEVER removed from the list — offline workers remain
in the dict so stats and indices persist across restarts.
"""
import re
import threading
import time

LOST_AFTER_S   = 45   # no heartbeat/activity → mark offline (disappear from UI)
CHECK_INTERVAL = 10   # seconds between liveness scans

_lock    = threading.Lock()
_workers: dict[str, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_type(worker_id: str) -> str:
    m = re.search(r'-(md|oc)-', worker_id)
    return m.group(1) if m else "unknown"


def _now() -> float:
    return time.time()


def _next_index(wtype: str, wdevice: str, exclude_id: str) -> int:
    """Assign the lowest unused per-type+device index."""
    used = {
        w.get("index", -1)
        for wid, w in _workers.items()
        if wid != exclude_id
        and w.get("type") == wtype
        and w.get("device") == wdevice
        and w.get("status") != "offline"
    }
    idx = 0
    while idx in used:
        idx += 1
    return idx


# ── Public API ─────────────────────────────────────────────────────────────────

def on_online(worker_id: str, worker_type: str | None = None, device: str | None = None,
              agent_id: str | None = None, protocol_version: str | None = None,
              code_version: str | None = None):
    """Worker came online and is ready."""
    with _lock:
        existing = _workers.get(worker_id, {})
        wtype   = worker_type or _parse_type(worker_id)
        wdevice = (device or "cpu").lower()

        # Preserve existing index if already assigned for this type+device
        prev_index = existing.get("index")
        index = prev_index if prev_index is not None else _next_index(wtype, wdevice, worker_id)

        _workers[worker_id] = {
            "worker_id":       worker_id,
            "type":            wtype,
            "device":          wdevice,
            "status":          "idle",
            "suspended":       existing.get("suspended", False),
            "job_id":          None,
            "registered_at":   existing.get("registered_at", _now()),
            "idle_since":      _now(),
            "last_heartbeat":  _now(),
            "index":           index,
            # Identity & versioning (broker/agent/worker hierarchy + compatibility)
            "agent_id":         agent_id or existing.get("agent_id", "unmanaged"),
            "protocol_version": protocol_version or existing.get("protocol_version", "?"),
            "code_version":     code_version or existing.get("code_version", "?"),
            # Cumulative stats — preserved across container restarts
            "jobs_processed":  existing.get("jobs_processed", 0),
            "total_compute_s": existing.get("total_compute_s", 0.0),
            "total_frames":    existing.get("total_frames", 0),
            "fps_high":        existing.get("fps_high", 0.0),
            "fps_low":         existing.get("fps_low", 0.0),
            "fps_sum":         existing.get("fps_sum", 0.0),
            "fps_count":       existing.get("fps_count", 0),
        }


def on_offline(worker_id: str):
    """Worker gracefully shut down."""
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["status"]     = "offline"
            _workers[worker_id]["job_id"]     = None
            _workers[worker_id]["idle_since"] = None


def on_heartbeat(worker_id: str, worker_type: str | None = None, device: str | None = None,
                 agent_id: str | None = None, protocol_version: str | None = None,
                 code_version: str | None = None):
    """Worker sent a keepalive.
    If the worker is unknown (orchestrator restarted), re-register it from
    the heartbeat payload so the panel recovers without restarting workers.
    """
    with _lock:
        if worker_id not in _workers:
            # Orchestrator lost state — bootstrap from heartbeat data
            _workers[worker_id] = {
                "worker_id":       worker_id,
                "type":            worker_type or _parse_type(worker_id),
                "device":          (device or "?").lower(),
                "status":          "idle",
                "suspended":       False,
                "job_id":          None,
                "registered_at":   _now(),
                "idle_since":      _now(),
                "last_heartbeat":  _now(),
                "index":           _next_index(
                                       worker_type or _parse_type(worker_id),
                                       (device or "?").lower(),
                                       worker_id,
                                   ),
                "agent_id":         agent_id or "unmanaged",
                "protocol_version": protocol_version or "?",
                "code_version":     code_version or "?",
                "jobs_processed":  0,
                "total_compute_s": 0.0,
                "total_frames":    0,
                "fps_high":        0.0,
                "fps_low":         0.0,
                "fps_sum":         0.0,
                "fps_count":       0,
            }
            return
        w = _workers[worker_id]
        w["last_heartbeat"] = _now()
        # Refresh identity/version each heartbeat (worker may have been recycled
        # onto a new image with a new code_version).
        if agent_id:          w["agent_id"]         = agent_id
        if protocol_version:  w["protocol_version"] = protocol_version
        if code_version:      w["code_version"]     = code_version
        # If liveness monitor had marked it offline, restore to idle
        if w["status"] == "offline":
            w["status"]     = "idle"
            w["idle_since"] = _now()


def update(worker_id: str, status: str, job_id=None):
    """
    Called by result_consumer on processing/idle transitions.
    status: 'processing' | 'idle'
    """
    with _lock:
        w = _workers.get(worker_id)
        if w is None:
            # Worker came online before the registry was listening — register it now.
            # Device is unknown until the online event arrives; use "?" so the UI
            # shows a placeholder label rather than incorrectly labelling it "cpu".
            _workers[worker_id] = {
                "worker_id":       worker_id,
                "type":            _parse_type(worker_id),
                "device":          "?",
                "suspended":       False,
                "registered_at":   _now(),
                "index":           0,
                "jobs_processed":  0,
                "total_compute_s": 0.0,
                "total_frames":    0,
                "fps_high":        0.0,
                "fps_low":         0.0,
                "fps_sum":         0.0,
                "fps_count":       0,
            }
            w = _workers[worker_id]

        w["status"]         = status
        w["job_id"]         = job_id
        w["last_heartbeat"] = _now()
        if status == "idle":
            w["idle_since"] = _now()
        else:
            w["idle_since"] = None


def record_job_stats(worker_id: str, elapsed_s: float, fps: float, frames: int):
    """
    Called by result_consumer after a job completes.
    Updates cumulative performance stats for display in the worker callout.
    """
    with _lock:
        w = _workers.get(worker_id)
        if w is None:
            return
        w["jobs_processed"]  = w.get("jobs_processed", 0) + 1
        w["total_compute_s"] = round(w.get("total_compute_s", 0.0) + elapsed_s, 2)
        w["total_frames"]    = w.get("total_frames", 0) + frames
        if fps > 0:
            w["fps_sum"]   = w.get("fps_sum", 0.0) + fps
            w["fps_count"] = w.get("fps_count", 0) + 1
            if fps > w.get("fps_high", 0.0):
                w["fps_high"] = round(fps, 1)
            low = w.get("fps_low", 0.0)
            if low == 0.0 or fps < low:
                w["fps_low"] = round(fps, 1)


def suspend(worker_id: str):
    """Mark a worker as suspended — it will nack+requeue new jobs."""
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["suspended"] = True


def resume(worker_id: str):
    """Clear the suspended flag — worker resumes accepting jobs."""
    with _lock:
        if worker_id in _workers:
            _workers[worker_id]["suspended"] = False


def is_suspended(worker_id: str) -> bool:
    with _lock:
        return _workers.get(worker_id, {}).get("suspended", False)


def get_all() -> list[dict]:
    """Return all known workers sorted by type, device, index.
    Offline workers are included (UI filters them out).
    Internal fps_sum/fps_count are replaced with fps_avg.
    """
    with _lock:
        result = []
        for w in sorted(
            _workers.values(),
            key=lambda x: (x.get("type", ""), x.get("device", ""), x.get("index", 0)),
        ):
            entry = dict(w)
            fps_count = entry.pop("fps_count", 0)
            fps_sum   = entry.pop("fps_sum", 0.0)
            entry["fps_avg"] = round(fps_sum / fps_count, 1) if fps_count > 0 else 0.0
            result.append(entry)
        return result


# ── Liveness monitor ──────────────────────────────────────────────────────────

def _liveness_loop():
    while True:
        time.sleep(CHECK_INTERVAL)
        now = _now()
        with _lock:
            for w in _workers.values():
                if w.get("status") == "offline":
                    continue  # already terminal
                last_hb = w.get("last_heartbeat", 0)
                if last_hb and (now - last_hb) > LOST_AFTER_S:
                    w["status"] = "offline"
                    w["job_id"] = None
                    w["idle_since"] = None


def start_liveness_monitor():
    t = threading.Thread(target=_liveness_loop, daemon=True, name="worker-liveness")
    t.start()
