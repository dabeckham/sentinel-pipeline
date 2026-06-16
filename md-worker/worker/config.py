from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "sentinel"
    rabbitmq_password: str = "sentinel"
    rabbitmq_vhost: str = "/"
    queue_ingest: str = "ingest"
    queue_motion_results: str = "motion_results"
    queue_oc_results: str = "oc_results"
    orchestrator_url: str = "http://orchestrator:8000"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel"
    minio_use_ssl: bool = False
    # minio_bucket_crops removed — crops now travel in-memory via RabbitMQ (issue #13)

    # ── Identity & versioning (reported in lifecycle events) ────────────────
    # See oc-worker/worker/config.py for the full rationale. protocol_version
    # gates compatibility on MAJOR; code_version (git SHA) is observability only;
    # agent_id is the node-agent that spawned this worker.
    protocol_version: str = "1.0"
    worker_code_version: str = "dev"
    agent_id: str = "unmanaged"

    # Frigate-style motion detection parameters
    motion_frame_height: int = 100      # detection frame height (maintains aspect ratio)
    motion_threshold: int = 25          # delta threshold (1-255); lower = more sensitive
    motion_delta_alpha: float = 0.2     # temporal smoothing of frame deltas (Frigate default)
    motion_frame_alpha: float = 0.01    # background learning rate (Frigate default)
    motion_improve_contrast: bool = True
    motion_min_contour_area: int = 10   # min contour area in motion-frame pixels
    motion_frame_skip: int = 2
    motion_merge_dist: int = 10         # merge nearby boxes (in motion-frame pixels)

    # Debug video (issue #14) — set MD_DEBUG_VIDEO=true to enable
    md_debug_video: bool = False
    md_debug_output_dir: str = "/ingest/debug"

    def rabbitmq_params(self):
        import pika
        return pika.ConnectionParameters(
            host=self.rabbitmq_host,
            port=self.rabbitmq_port,
            virtual_host=self.rabbitmq_vhost,
            credentials=pika.PlainCredentials(self.rabbitmq_user, self.rabbitmq_password),
            heartbeat=600,
        )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"   # tolerate extra keys in a shared .env (e.g. project PORT_* vars)


@lru_cache
def get_settings() -> Settings:
    return Settings()
