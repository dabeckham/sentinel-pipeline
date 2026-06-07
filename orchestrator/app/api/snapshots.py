"""Snapshot proxy — streams MinIO images to the browser."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth.deps import require_viewer
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
