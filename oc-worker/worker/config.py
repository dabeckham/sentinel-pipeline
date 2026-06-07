from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "sentinel"
    rabbitmq_password: str = "sentinel"
    rabbitmq_vhost: str = "/"
    queue_motion_results: str = "motion_results"
    queue_oc_results: str = "oc_results"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel"
    minio_use_ssl: bool = False
    # minio_bucket_crops removed — crops now travel in-memory via RabbitMQ (issue #13)
    minio_bucket_snapshots: str = "snapshots"

    # OC_MODEL_NAME avoids collision with YOLO_MODEL in .env (which may name a future model)
    oc_model_name: str = "yolo11s"
    oc_confidence_threshold: float = 0.5   # lowered from 0.85 — 0.85 missed too many detections causing track gaps
    oc_iou_threshold: float = 0.5
    oc_use_gpu: bool = False

    # Norfair tracker parameters (Frigate defaults)
    tracker_distance_threshold: float = 2.5   # max normalised distance to match a detection
    tracker_initialization_delay: int = 2     # consecutive hits before a new track is confirmed (eliminates single-frame ghosts)
    tracker_hit_counter_max: int = 30         # frames a track survives without a detection (sparse motion frames need more)

    # YOLO class allowlist — only these labels are forwarded to ByteTrack / stored.
    # Comma-separated. Empty string = allow all (not recommended).
    # COCO vehicles + person + common animals.
    oc_allowed_classes: str = (
        "person,"
        "bicycle,car,motorcycle,airplane,bus,train,truck,boat,"
        "bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe"
    )

    def rabbitmq_params(self):
        import pika
        return pika.ConnectionParameters(
            host=self.rabbitmq_host,
            port=self.rabbitmq_port,
            virtual_host=self.rabbitmq_vhost,
            credentials=pika.PlainCredentials(self.rabbitmq_user, self.rabbitmq_password),
            heartbeat=60,
        )

    @property
    def yolo_model_path(self) -> str:
        import os
        m = self.oc_model_name
        if not m.endswith(".pt"):
            m += ".pt"
        # Prefer persistent volume location so model isn't re-downloaded on every restart
        model_dir = "/app/models"
        if os.path.isdir(model_dir):
            return os.path.join(model_dir, m)
        return m

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
