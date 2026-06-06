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
    minio_bucket_crops: str = "crops"
    minio_bucket_snapshots: str = "snapshots"

    yolo_model: str = "yolo11s"
    oc_confidence_threshold: float = 0.45
    oc_iou_threshold: float = 0.5
    oc_use_gpu: bool = False

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
        m = self.yolo_model
        if not m.endswith(".pt"):
            m += ".pt"
        return m

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
