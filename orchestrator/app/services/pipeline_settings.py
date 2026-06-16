"""
Persistent key-value pipeline settings (pipeline_settings table, migration 0008).

Survives orchestrator restarts AND any `docker compose up` — unlike env vars,
which revert to their default whenever Compose reconciles a service without the
override set (that footgun is exactly what silently re-enabled ingestion).
"""
import structlog
from sqlalchemy import text

from app.db import SessionLocal

log = structlog.get_logger()


def get_bool(key: str, default: bool) -> bool:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT value FROM pipeline_settings WHERE key = :k"), {"k": key}
        ).fetchone()
        if row is None:
            return default
        return str(row[0]).lower() == "true"
    except Exception:
        log.warning("pipeline_setting_load_error", key=key)
        return default
    finally:
        db.close()


def set_bool(key: str, value: bool) -> None:
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO pipeline_settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ), {"k": key, "v": "true" if value else "false"})
        db.commit()
    except Exception:
        log.exception("pipeline_setting_save_error", key=key)
        db.rollback()
    finally:
        db.close()
