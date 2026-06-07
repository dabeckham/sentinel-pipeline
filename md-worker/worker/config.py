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

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel"
    minio_use_ssl: bool = False
    # minio_bucket_crops removed — crops now travel in-memory via RabbitMQ (issue #13)

    mog2_history: int = 500
    mog2_var_threshold: float = 25.0   # raised 16→25: less sensitive, fewer false positives
    mog2_detect_shadows: bool = False   # shadows=False: avoids grey-pixel bleed expanding boxes
    motion_min_contour_area: int = 800  # raised 500→800: filter small noise/leaves/birds
    motion_frame_skip: int = 2
    # Resize factor applied before MOG2 — bboxes are scaled back to original res for cropping
    # 0.25 = 640×360 from 2560×1440 (16× fewer pixels, ~8-10× faster MOG2)
    motion_scale: float = 0.25
    # Merge nearby bboxes into whole-object bboxes after contour finding.
    # Boxes within this many scaled-frame pixels of each other are merged.
    # At scale=0.25: 60px here = 240px in original resolution.
    # Large vehicles can fragment into hood/roof/trunk contours — 60 scaled px merges them.
    motion_merge_dist: int = 60

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
            heartbeat=60,
        )

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
