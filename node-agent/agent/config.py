"""
Node-agent configuration.

The agent is the per-machine supervisor for Sentinel workers. It probes local
resources, decides how many MD/OC workers this machine can run, and brings them
on/off the clock based on live load. Phase 1 = single host (the orchestrator
host); later phases add enrollment, remote transport, and a networked data plane.

All values are overridable via environment so the same image can run on any
machine. Secrets (RabbitMQ creds) come from the standard .env via `env_file:`,
exactly like the workers — the agent never hard-codes them.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Identity ────────────────────────────────────────────────────────────
    node_name: str = "local"               # human label for this machine
    agent_state_dir: str = "/state"        # persists the self-generated agent_id

    # ── Governor cadence + watermarks ───────────────────────────────────────
    # Load is normalized per physical core: load1 / physical_cores.
    governor_interval_s: int = 15          # control-loop period
    load_high: float = 0.90                # >= this → park a worker
    load_low: float = 0.65                 # <  this → eligible to add a worker
    # Swap THRASHING signal — sustained swap-IN rate (MB/s), NOT occupancy.
    # A full-but-idle swap (e.g. 82% after an earlier spike) is harmless; only
    # active paging-in indicates memory pressure. (Caught in observe-mode.)
    swap_in_high_mb_s: float = 5.0         # >= this → emergency park
    action_cooldown_s: int = 45            # min seconds between scale actions
    # Reserve physical cores for the OS + co-tenants (Frigate, Ollama, …).
    # On the i9-9900K host this keeps Sentinel from starving Frigate (the
    # session-16 thrash: 8 workers on 8 cores → load 48).
    reserve_cores: int = 3

    # ── Per-worker cost model (calibrated session 16; refine empirically) ────
    oc_cost_cores: float = 1.5
    oc_cost_ram_mb: int = 1400
    oc_cost_vram_mb: int = 60              # ~13MB engine + decode headroom
    md_cost_cores: float = 1.5
    md_cost_ram_mb: int = 1600

    # ── Pool bounds ─────────────────────────────────────────────────────────
    oc_min: int = 1                        # keep a puller alive so work flows
    oc_max: int = 8                        # hard ceiling regardless of budget
    md_min: int = 1
    md_max: int = 8

    # ── Worker launch spec (mirrors docker-compose) ─────────────────────────
    worker_network: str = "sentinel-pipeline_sentinel-net"
    yolo_volume: str = "sentinel-pipeline_yolo-models"
    ingest_source: str = "/mnt/ds-one/sentinel-ingest"   # HOST path for bind mount
    env_file_host_path: str = "/home/dabeckham/sentinel-pipeline/.env"  # bind-mounted to /app/.env
    oc_image: str = "sentinel-oc-worker-gpu:latest"
    md_image: str = "sentinel-pipeline-md-worker:latest"
    oc_gpu_ids: str = "1"                  # comma-separated physical GPU ids for OC workers

    # ── Transcode worker (on-demand playback renditions) ────────────────────
    # A single always-on NVENC worker handles browser-friendly H.264 renditions.
    # Short, infrequent jobs — no demand scaling; the agent just keeps one alive.
    transcode_enabled: bool = True
    transcode_image: str = "sentinel-transcode-worker:latest"
    transcode_gpu_id: str = "1"            # GPU0 is Frigate; encode on GPU1

    # ── Broker (for demand signal: queue depths) ────────────────────────────
    rabbitmq_user: str = "sentinel"
    rabbitmq_password: str = "sentinel"
    rabbitmq_mgmt_url: str = "http://rabbitmq:15672"
    queue_ingest: str = "ingest"           # MD demand
    queue_motion_results: str = "motion_results"  # OC demand

    # ── Safety ──────────────────────────────────────────────────────────────
    # Observe-only: log every decision but never start/stop a container.
    # Deploy with this TRUE, validate the decisions against real load, then flip.
    dry_run: bool = True

    class Config:
        env_prefix = "AGENT_"
        env_file = ".env"
        case_sensitive = False

    @property
    def oc_gpu_id_list(self) -> list[str]:
        return [g.strip() for g in self.oc_gpu_ids.split(",") if g.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
