"""Snapshot proxy and cleanup utilities."""
import re
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.deps import require_admin, require_viewer
from app.config import get_settings
from app.minio_client import get_minio
from app.models.user import User

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


@router.get("/{path:path}")
def get_snapshot(
    path: str,
    _: User = Depends(require_viewer),
):
    """Proxy a MinIO snapshot object to the browser as an image."""
    s = get_settings()
    mc = get_minio()
    try:
        response = mc.get_object(s.minio_bucket_snapshots, path)
        data = response.read()
        response.close()
        response.release_conn()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Snapshot not found: {path}") from exc

    # Detect content type from extension
    content_type = "image/jpeg"
    if path.lower().endswith(".png"):
        content_type = "image/png"
    elif path.lower().endswith(".webp"):
        content_type = "image/webp"

    return StreamingResponse(
        iter([data]),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


class CleanupResponse(BaseModel):
    deleted: int
    freed_bytes: int
    errors: int


@router.post("/cleanup", response_model=CleanupResponse)
def cleanup_playback_frames(
    job_id: int | None = None,
    _: User = Depends(require_admin),
):
    """
    Delete per-detection playback frames (_f{frame}.jpg) from MinIO,
    keeping only the best-shot thumbnails (_best.jpg) per track.

    Pass job_id to limit cleanup to a specific job, or omit to clean all.
    This is safe to run at any time — completed jobs' best shots are never touched.
    """
    s = get_settings()
    mc = get_minio()
    bucket = s.minio_bucket_snapshots

    # Regex matches playback frames: {job_id}/track_{track_id:06d}_f{frame:06d}.jpg
    frame_pattern = re.compile(r"^\d+/track_\d{6}_f\d{6}\.jpg$")

    prefix = f"{job_id}/" if job_id is not None else ""
    deleted = 0
    freed_bytes = 0
    errors = 0

    try:
        objects = list(mc.list_objects(bucket, prefix=prefix, recursive=True))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"MinIO list failed: {exc}") from exc

    to_delete = [
        obj for obj in objects
        if frame_pattern.match(obj.object_name)
    ]

    for obj in to_delete:
        try:
            freed_bytes += obj.size or 0
            mc.remove_object(bucket, obj.object_name)
            deleted += 1
        except Exception:
            errors += 1

    return CleanupResponse(deleted=deleted, freed_bytes=freed_bytes, errors=errors)
