"""Authentication endpoints."""
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.jwt import create_access_token
from app.auth.password import verify_password
from app.db import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

log = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.username == payload.username,
        User.is_active == True,
    ).first()

    if not user or not verify_password(payload.password, user.password_hash):
        log.warning("login_failed", username=payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(subject=user.username, role=user.role.value)
    log.info("login_success", username=user.username, role=user.role.value)

    return TokenResponse(access_token=token, role=user.role.value, username=user.username)
