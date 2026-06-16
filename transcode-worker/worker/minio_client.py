from functools import lru_cache
from minio import Minio
from worker.config import get_settings


@lru_cache(maxsize=1)
def get_minio() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_use_ssl,
    )


def object_exists(bucket: str, name: str) -> bool:
    from minio.error import S3Error
    try:
        get_minio().stat_object(bucket, name)
        return True
    except S3Error:
        return False
