"""Track endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_viewer
from app.db import get_db
from app.models.track import Track
from app.models.user import User
from app.schemas.track import TrackListResponse

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.get("", response_model=TrackListResponse)
def list_tracks(
    job_id: Optional[int] = Query(None),
    class_label: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
):
    q = db.query(Track)
    if job_id:
        q = q.filter(Track.job_id == job_id)
    if class_label:
        q = q.filter(Track.class_label == class_label)
    total = q.count()
    items = q.order_by(Track.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return TrackListResponse(items=items, total=total, page=page, page_size=page_size)
