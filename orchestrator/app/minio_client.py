"""Minimal MinIO client for the orchestrator (snapshot proxy)."""
from functools import lru_cache
from minio import Minio
from app.config import get_settings


@lru_cache(maxsize=1)
def get_minio() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_use_ssl,
    )
