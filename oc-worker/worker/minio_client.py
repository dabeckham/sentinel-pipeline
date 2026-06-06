import io
import cv2
import numpy as np
from minio import Minio
from worker.config import get_settings

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        s = get_settings()
        _client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_use_ssl,
        )
    return _client


def upload_snapshot(bucket: str, object_name: str, image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("Failed to encode snapshot as JPEG")
    data = buf.tobytes()
    client = get_client()
    client.put_object(
        bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type="image/jpeg",
    )
    return object_name
