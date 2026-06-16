from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── RabbitMQ ────────────────────────────────────────────────────────────
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "sentinel"
    rabbitmq_password: str = "sentinel"
    rabbitmq_vhost: str = "/"
    # Queue of transcode requests published by the orchestrator's playback API.
    queue_transcode: str = "transcode_jobs"

    # ── MinIO ───────────────────────────────────────────────────────────────
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel"
    minio_use_ssl: bool = False
    # Renditions live in the snapshots bucket under a renditions/ prefix so we
    # don't have to create/manage a second bucket.
    minio_bucket_snapshots: str = "snapshots"

    # ── Encode ──────────────────────────────────────────────────────────────
    # h264_nvenc preset (p1 fastest … p7 best quality). p4 is a good balance.
    nvenc_preset: str = "p4"
    # Software HEVC decode + NVENC H.264 encode is the robust default (NVDEC
    # build flags vary across ffmpeg packages). Set true to also decode on GPU
    # via -hwaccel cuda when the image's ffmpeg supports it.
    use_hwaccel_decode: bool = False
    # Per-clip transcode wall-clock ceiling (seconds) — a stuck ffmpeg is killed.
    transcode_timeout_s: int = 120

    # ── Identity & versioning (reported in lifecycle events) ────────────────
    protocol_version: str = "1.0"
    worker_code_version: str = "dev"   # WORKER_CODE_VERSION, baked at build
    agent_id: str = "unmanaged"        # node-agent that spawned this worker

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
        extra = "ignore"   # tolerate extra keys in a shared .env


@lru_cache
def get_settings() -> Settings:
    return Settings()
