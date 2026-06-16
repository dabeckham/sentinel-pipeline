"""
Adaptive playback transcode.

The source clips are 11MP HEVC, which browsers cannot decode (HEVC playback is
hardware-gated and capped at ~4K). This serves a browser-friendly H.264
rendition sized to what the client profiled itself able to decode and carry:

  GET /api/jobs/{id}/playback?h=720
    • resolves the requested height to the nearest rendition rung
    • if that rung is already cached in MinIO  → 200, streams the H.264 mp4
    • otherwise enqueues a transcode and returns 202 {status:"transcoding"}
      so the UI can poll until it's ready.

Renditions are quantized to a small ladder so the cache stays small and hits
are common (one object per (job, rung)). The download button keeps serving the
original via /jobs/{id}/video.
"""
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.db import get_db
from app.models.job import Job
from app.models.user import User

router = APIRouter(prefix="/jobs", tags=["playback"])

# Rendition ladder (output height) → target H.264 bitrate (kbps). The client
# picks the rung = min(maxDecodableHeight, bandwidthAllowedHeight); the encoder
# only ever downscales, so a rung above the source just yields the source size.
LADDER: dict[int, int] = {
    360: 800,
    480: 1500,
    720: 3000,
    1080: 6000,
    1440: 12000,
}
_RUNGS = sorted(LADDER)

# Best-effort de-dup of in-flight transcode requests so rapid re-polls / double
# clicks don't enqueue the same rung many times. Idempotent on the worker side
# regardless; this just trims noise. Not durable across restarts (fine).
_inflight: set[tuple[int, int]] = set()
_inflight_lock = threading.Lock()
_queue_declared = False


def _resolve_rung(h: int) -> int:
    """Largest ladder rung <= requested height (never below the smallest rung)."""
    eligible = [r for r in _RUNGS if r <= h]
    return eligible[-1] if eligible else _RUNGS[0]


def _object_name(job_id: int, rung: int) -> str:
    return f"renditions/{job_id}/{rung}p.mp4"


def _ensure_queue_declared() -> None:
    global _queue_declared
    if _queue_declared:
        return
    from app.config import get_settings
    from app.services import amqp
    amqp.declare_durable(get_settings().queue_transcode)
    _queue_declared = True


@router.get("/{job_id}/playback")
def get_playback(
    job_id: int,
    h: int = Query(720, ge=144, le=4320, description="target rendition height"),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.source_deleted:
        raise HTTPException(status_code=410, detail="Source video has been purged")
    if not job.file_path:
        raise HTTPException(status_code=404, detail="Source video not on disk")

    rung = _resolve_rung(h)
    object_name = _object_name(job_id, rung)

    from app.config import get_settings
    from app.minio_client import get_minio
    s = get_settings()
    mc = get_minio()

    # Cache hit → stream the rendition.
    try:
        resp = mc.get_object(s.minio_bucket_snapshots, object_name)
        data = resp.read()
        resp.close()
        resp.release_conn()
        with _inflight_lock:
            _inflight.discard((job_id, rung))   # done — allow re-enqueue if ever purged
        return StreamingResponse(
            iter([data]),
            media_type="video/mp4",
            headers={
                "Cache-Control": "public, max-age=86400",
                "Content-Length": str(len(data)),
                "X-Rendition-Rung": str(rung),
            },
        )
    except Exception:
        pass  # not cached yet — fall through to enqueue

    # Cache miss → enqueue a transcode (deduped) and tell the client to poll.
    key = (job_id, rung)
    with _inflight_lock:
        already = key in _inflight
        _inflight.add(key)
    if not already:
        try:
            _ensure_queue_declared()
            from app.services import amqp
            amqp.publish(s.queue_transcode, {
                "job_id": job_id,
                "source_path": job.file_path,
                "object_name": object_name,
                "height": rung,
                "bitrate_k": LADDER[rung],
            })
        except Exception:
            with _inflight_lock:
                _inflight.discard(key)
            raise HTTPException(status_code=503, detail="Could not enqueue transcode")

    return JSONResponse(status_code=202, content={"status": "transcoding", "rung": rung})
