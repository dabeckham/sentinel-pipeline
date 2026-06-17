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
    orchestrator_url: str = "http://orchestrator:8000"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "sentinel"
    minio_secret_key: str = "sentinel"
    minio_use_ssl: bool = False
    # minio_bucket_crops removed — crops now travel in-memory via RabbitMQ (issue #13)
    minio_bucket_snapshots: str = "snapshots"

    # ── Identity & versioning (reported in lifecycle events) ────────────────
    # protocol_version: the worker<->orchestrator contract (message schemas,
    #   queue names, API). Compatibility is gated on MAJOR; bump only on a
    #   breaking schema change. A compatible-MAJOR worker is accepted even on an
    #   older MINOR, so new code can canary on prod without a fleet lockstep.
    protocol_version: str = "1.0"
    # code_version: git short SHA baked into the image at build (WORKER_CODE_VERSION).
    #   Observability only — never gates. "dev" when built without the build-arg.
    worker_code_version: str = "dev"
    # agent_id: the node-agent (machine) that spawned this worker. "unmanaged"
    #   if started outside an agent (e.g. plain docker compose).
    agent_id: str = "unmanaged"

    # OC_MODEL_NAME avoids collision with YOLO_MODEL in .env (which may name a future model)
    oc_model_name: str = "yolo11s"
    oc_confidence_threshold: float = 0.5   # lowered from 0.85 — 0.85 missed too many detections causing track gaps
    oc_iou_threshold: float = 0.5
    oc_use_gpu: bool = False

    # ── De-fragmentation (issue #59) ─────────────────────────────────────────
    # ByteTrack's Kalman filter advances one step per model.track() call, so
    # feeding it only the sparse, non-contiguous motion frames makes predictions
    # land far from the object → re-id under a new track id → one vehicle splits
    # into many short "stationary" fragments. With this on, the tracker runs on
    # EVERY frame across the motion span (frame-accurate predictions, stable ids);
    # detections are still persisted only at the motion-frame cadence.
    oc_track_contiguous: bool = True
    # Safety cap: if the motion span exceeds this many frames, fall back to
    # sparse-frame tracking for that clip (0 = unbounded). Guards against a
    # pathologically long clip pinning the GPU.
    oc_track_max_span: int = 0

    # ── Post-hoc fragment merge (issue #59, the "right way" completion) ──────
    # Contiguous tracking keeps a MOVING object whole, but a STATIONARY object
    # still splits when a passing vehicle occludes it: ByteTrack drops its id
    # during the occlusion and re-ids it as a new short track afterwards. After
    # tracking, stitch such fragments back together — merge two same-class
    # tracks when one track's LAST box overlaps the other's FIRST box (IoU) across
    # a bounded frame gap, so one physical object keeps one track id. The IoU gate
    # makes this conservative: two distinct vehicles would have to occupy nearly
    # the same pixels across the gap, which within a single short clip means it is
    # the same object.
    oc_merge_fragments: bool = True
    # Max temporal gap (in source frames) to stitch a fragment across. ~60 ≈ 2s
    # at 30fps — long enough for a vehicle to pass in front of a parked car.
    oc_merge_max_gap: int = 60
    # Min IoU between the earlier track's last box and the later track's first box.
    oc_merge_min_iou: float = 0.5

    # YOLO inference image size (pixels, square).  640 = yolo11s native.
    yolo_imgsz: int = 640

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
            heartbeat=600,   # 10 min — give YOLO callbacks plenty of room
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
        extra = "ignore"   # tolerate extra keys in a shared .env (e.g. project PORT_* vars)


@lru_cache
def get_settings() -> Settings:
    return Settings()
