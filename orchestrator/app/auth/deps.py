"""FastAPI dependencies for authentication and authorization."""
import ipaddress
from typing import Optional

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth.jwt import decode_token
from app.db import get_db
from app.models.user import User, UserRole

log = structlog.get_logger()
bearer = HTTPBearer(auto_error=False)


def _get_lan_trust_config(db: Session) -> tuple[bool, list[str]]:
    """Read LAN trust settings from DB config table."""
    from app.models.config import Config
    enabled_row = db.query(Config).filter(Config.key == "lan_trust_enabled").first()
    cidrs_row = db.query(Config).filter(Config.key == "lan_trust_cidrs").first()
    enabled = (enabled_row.value == "true") if enabled_row else False
    cidrs = [c.strip() for c in cidrs_row.value.split(",")] if (cidrs_row and cidrs_row.value) else []
    return enabled, cidrs


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _ip_in_cidrs(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(c, strict=False) for c in cidrs)
    except ValueError:
        return False


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the current user. Two paths:
    1. LAN trust — if enabled and request IP is in trusted CIDRs, return a synthetic admin user.
    2. JWT bearer token — decode and load user from DB.
    """
    # --- LAN trust check ---
    lan_enabled, lan_cidrs = _get_lan_trust_config(db)
    if lan_enabled and lan_cidrs:
        ip = _client_ip(request)
        if _ip_in_cidrs(ip, lan_cidrs):
            log.debug("lan_trust_granted", ip=ip)
            # Return a synthetic admin — not stored in DB
            synthetic = User()
            synthetic.id = 0
            synthetic.username = "lan-trust"
            synthetic.role = UserRole.admin
            synthetic.is_active = True
            return synthetic

    # --- JWT: Authorization header, or ?token= query param ---
    # Native <video>/<a download> elements can't set an Authorization header, so
    # media endpoints (playback/video) pass the same JWT as a ?token= query param.
    raw_token = credentials.credentials if credentials else request.query_params.get("token")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(raw_token)
        username: str = payload.get("sub")
        if not username:
            raise JWTError("no sub")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return user


def require_roles(*roles: UserRole):
    """Dependency factory — enforces one of the given roles."""
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user
    return _dep


require_admin = require_roles(UserRole.admin)
require_operator = require_roles(UserRole.admin, UserRole.operator)
require_viewer = require_roles(UserRole.admin, UserRole.operator, UserRole.viewer)
