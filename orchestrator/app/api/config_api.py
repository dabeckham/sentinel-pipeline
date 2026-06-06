"""Runtime config endpoints (admin only)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.db import get_db
from app.models.config import Config
from app.models.user import User

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
def get_config(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.query(Config).all()
    return {r.key: r.value for r in rows}


@router.put("")
def set_config(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    for key, value in payload.items():
        row = db.query(Config).filter(Config.key == key).first()
        if row:
            row.value = str(value)
            row.updated_at = now
            row.updated_by = current_user.id if current_user.id != 0 else None
        else:
            row = Config(key=key, value=str(value), updated_at=now,
                        updated_by=current_user.id if current_user.id != 0 else None)
            db.add(row)
    db.commit()
    return {"updated": list(payload.keys())}
