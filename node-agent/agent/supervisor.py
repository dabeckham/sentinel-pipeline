"""
Worker supervision via the Docker Engine API.

Starts/stops worker containers as siblings (the agent mounts the Docker socket).
Containers are labelled `sentinel.managed=true` so the agent can discover and
adopt its pool across restarts and never touch unmanaged containers.

Launch spec mirrors docker-compose.yml + docker-compose.override.yml:
  - OC: sentinel-oc-worker-gpu image, GPU device request, yolo-models + ingest(ro)
  - MD: sentinel-pipeline-md-worker image, ingest(rw)
Secrets are NOT read by the agent — the host .env is bind-mounted to /app/.env
and the worker's own pydantic Settings load it, exactly as `env_file:` does.
"""
from __future__ import annotations

import docker
import structlog
from docker.types import DeviceRequest

from agent.config import Settings
from agent.identity import get_agent_id

log = structlog.get_logger()

LABEL_MANAGED = "sentinel.managed"
LABEL_TYPE = "sentinel.worker_type"


class Supervisor:
    def __init__(self, s: Settings):
        self._s = s
        self._client = docker.from_env()
        self._gpu_rr = 0  # round-robin index into oc_gpu_id_list
        self._agent_id = get_agent_id()   # stable, persisted per machine

    # ── Discovery ───────────────────────────────────────────────────────────
    # A worker counts as "present" while running, just-created, or restarting —
    # anything not terminal. This avoids a double-spawn race when a tick lands
    # before a freshly started container reaches "running".
    _ALIVE = {"running", "created", "restarting"}

    def counts(self) -> dict[str, int]:
        out = {"oc": 0, "md": 0, "transcode": 0}
        for c in self._managed():
            if c.status in self._ALIVE:
                t = c.labels.get(LABEL_TYPE)
                if t in out:
                    out[t] += 1
        return out

    def reap(self) -> int:
        """Remove exited managed containers (crashed or parked) so they don't
        accumulate. Returns how many were removed."""
        n = 0
        for c in self._managed():
            if c.status in ("exited", "dead"):
                try:
                    c.remove(force=True)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        if n:
            log.info("supervisor_reaped", removed=n)
        return n

    def _managed(self) -> list:
        return self._client.containers.list(
            all=True, filters={"label": f"{LABEL_MANAGED}=true"}
        )

    # ── Scale up ────────────────────────────────────────────────────────────
    def start(self, worker_type: str) -> str | None:
        if self._s.dry_run:
            log.info("supervisor_dry_run_start", worker_type=worker_type)
            return None
        try:
            if worker_type == "oc":
                return self._start_oc()
            elif worker_type == "md":
                return self._start_md()
        except Exception:
            log.exception("supervisor_start_failed", worker_type=worker_type)
        return None

    def _next_name(self, worker_type: str) -> str:
        existing = {c.name for c in self._managed()}
        i = 1
        while f"sentinel-{worker_type}-managed-{i}" in existing:
            i += 1
        return f"sentinel-{worker_type}-managed-{i}"

    def _common(self, worker_type: str) -> dict:
        return dict(
            detach=True,
            name=self._next_name(worker_type),
            network=self._s.worker_network,
            labels={LABEL_MANAGED: "true", LABEL_TYPE: worker_type, "sentinel.node": self._s.node_name},
            restart_policy={"Name": "no"},  # the agent owns lifecycle, not Docker
            volumes={self._s.env_file_host_path: {"bind": "/app/.env", "mode": "ro"}},
        )

    def _start_oc(self) -> str:
        gpu_ids = self._s.oc_gpu_id_list or ["0"]
        gpu = gpu_ids[self._gpu_rr % len(gpu_ids)]
        self._gpu_rr += 1
        spec = self._common("oc")
        spec["environment"] = {
            "RABBITMQ_HOST": "rabbitmq", "MINIO_ENDPOINT": "minio:9000",
            "WORKER_TYPE": "oc", "OC_USE_GPU": "true",
            "CUDA_VISIBLE_DEVICES": "0", "OC_MODEL_NAME": "yolo11s",
            "AGENT_ID": self._agent_id,
        }
        spec["volumes"].update({
            self._s.yolo_volume: {"bind": "/app/models", "mode": "rw"},
            self._s.ingest_source: {"bind": "/ingest", "mode": "ro"},
        })
        spec["device_requests"] = [DeviceRequest(device_ids=[gpu], capabilities=[["gpu"]])]
        c = self._client.containers.run(self._s.oc_image, **spec)
        log.info("supervisor_started", worker_type="oc", name=c.name, gpu=gpu)
        return c.name

    def _start_md(self) -> str:
        spec = self._common("md")
        spec["environment"] = {
            "RABBITMQ_HOST": "rabbitmq", "MINIO_ENDPOINT": "minio:9000",
            "WORKER_TYPE": "md", "MD_DEBUG_VIDEO": "false",
            "AGENT_ID": self._agent_id,
        }
        spec["volumes"][self._s.ingest_source] = {"bind": "/ingest", "mode": "rw"}
        c = self._client.containers.run(self._s.md_image, **spec)
        log.info("supervisor_started", worker_type="md", name=c.name)
        return c.name

    def _start_transcode(self) -> str:
        spec = self._common("transcode")
        spec["environment"] = {
            "RABBITMQ_HOST": "rabbitmq", "MINIO_ENDPOINT": "minio:9000",
            "WORKER_TYPE": "transcode",
            "CUDA_VISIBLE_DEVICES": "0",   # the single device exposed below
            "AGENT_ID": self._agent_id,
        }
        # Read-only source clips; renditions go to MinIO, not the mount.
        spec["volumes"][self._s.ingest_source] = {"bind": "/ingest", "mode": "ro"}
        spec["device_requests"] = [
            DeviceRequest(device_ids=[self._s.transcode_gpu_id], capabilities=[["gpu"]])
        ]
        c = self._client.containers.run(self._s.transcode_image, **spec)
        log.info("supervisor_started", worker_type="transcode",
                 name=c.name, gpu=self._s.transcode_gpu_id)
        return c.name

    def ensure_transcode(self) -> None:
        """Keep exactly one transcode worker alive (always-on, no demand scaling).
        Cheap idle cost; renditions must be ready quickly when a user hits play."""
        if not self._s.transcode_enabled:
            return
        if self.counts().get("transcode", 0) >= 1:
            return
        if self._s.dry_run:
            log.info("supervisor_dry_run_start", worker_type="transcode")
            return
        try:
            self._start_transcode()
        except Exception:
            log.exception("supervisor_start_failed", worker_type="transcode")

    # ── Scale down ──────────────────────────────────────────────────────────
    def park(self, worker_type: str) -> str | None:
        """Stop one worker of the given type. SIGTERM lets it drain its current
        job; the unacked RabbitMQ message redelivers, so no work is lost."""
        victim = next(
            (c for c in self._managed()
             if c.status == "running" and c.labels.get(LABEL_TYPE) == worker_type),
            None,
        )
        if victim is None:
            return None
        if self._s.dry_run:
            log.info("supervisor_dry_run_park", worker_type=worker_type, name=victim.name)
            return victim.name
        try:
            victim.stop(timeout=30)
            victim.remove(force=True)
            log.info("supervisor_parked", worker_type=worker_type, name=victim.name)
            return victim.name
        except Exception:
            log.exception("supervisor_park_failed", name=victim.name)
            return None
