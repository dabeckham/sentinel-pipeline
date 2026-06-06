from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Ingest
    ingest_source_path: str = "/ingest"
    ingest_recurse: bool = True
    ingest_poll_interval: int = 10

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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def rabbitmq_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/{self.rabbitmq_vhost}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
