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
    oc_confidence_threshold: float = 0.45
    oc_iou_threshold: float = 0.5
    oc_use_gpu: bool = False

    # ByteTrack tuning — defaults suit frame_skip=2 at ~30fps cameras
    # Lower match threshold handles larger inter-frame jumps from skipped frames
    bytetrack_match_threshold: float = 0.3
    # Buffer in frames — how long to hold a lost track before killing it
    # Set high enough to survive motion_frame_skip gaps (frame_skip+1) * fps
    bytetrack_lost_buffer: int = 90
    # Minimum consecutive frames before a track is confirmed
    bytetrack_min_hits: int = 1

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
        m = self.oc_model_name
        if not m.endswith(".pt"):
            m += ".pt"
        return m

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
