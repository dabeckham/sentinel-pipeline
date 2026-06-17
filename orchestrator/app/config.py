from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Ingest — container always mounts the ingest path at /ingest.
    # INGEST_SOURCE_PATH in .env is only for docker-compose volume syntax; not read here.
    ingest_watch_path: str = "/ingest"
    ingest_recurse: bool = True
    ingest_poll_interval: int = 10
    # Comma-separated subdirectory names to ignore when recursing (e.g. debug output)
    ingest_ignore_dirs: str = "debug"
    # Master switch for automatic ingestion (file watcher + startup backfill scan).
    # Set INGEST_WATCH_ENABLED=false to bring the orchestrator up WITHOUT pulling
    # in on-disk files — useful when a large /ingest backlog should not be
    # reprocessed automatically. recover_stuck_jobs still runs.
    ingest_watch_enabled: bool = True

    # Auth
    jwt_secret_key: str
    jwt_access_token_expire_minutes: int = 60
    cors_origins: str = "http://localhost:3000"

    # RabbitMQ
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "sentinel"
    rabbitmq_password: str
    rabbitmq_vhost: str = "/"
    queue_ingest: str = "ingest"
    queue_motion_results: str = "motion_results"
    queue_oc_results: str = "oc_results"
    # On-demand playback transcodes (source HEVC → adaptive H.264 rendition).
    queue_transcode: str = "transcode_jobs"

    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "sentinel"
    postgres_user: str = "sentinel"
    postgres_password: str

    # MinIO
    minio_endpoint: str = "minio:9000"
    minio_access_key: str
    minio_secret_key: str
    minio_use_ssl: bool = False
    minio_bucket_frames: str = "frames-raw"
    minio_bucket_crops: str = "crops"
    minio_bucket_snapshots: str = "snapshots"

    # LAN trust (runtime toggle stored in DB; these are defaults)
    lan_trust_cidrs: str = ""

    # Track classification — stationary vs moving
    # Normalized centroid displacement (first→last detection, divided by avg bbox width)
    # below this threshold → track_type = "stationary"
    tracker_min_displacement: float = 0.3

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def rabbitmq_params(self) -> "pika.ConnectionParameters":
        import pika
        return pika.ConnectionParameters(
            host=self.rabbitmq_host,
            port=self.rabbitmq_port,
            virtual_host=self.rabbitmq_vhost,
            credentials=pika.PlainCredentials(self.rabbitmq_user, self.rabbitmq_password),
            heartbeat=60,
        )

    @property
    def rabbitmq_url(self) -> str:
        from urllib.parse import quote
        user = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password, safe="")
        vhost = quote(self.rabbitmq_vhost, safe="")
        return f"amqp://{user}:{password}@{self.rabbitmq_host}:{self.rabbitmq_port}/{vhost}"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
