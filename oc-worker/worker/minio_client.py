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


def download_crop(bucket: str, object_name: str) -> np.ndarray:
    """Download a crop from MinIO and decode it as a BGR numpy array."""
    client = get_client()
    response = client.get_object(bucket, object_name)
    data = response.read()
    response.close()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Failed to decode image: {object_name}")
    return img


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
