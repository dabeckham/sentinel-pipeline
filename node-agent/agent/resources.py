"""
Local resource probing.

Reads host-level CPU/RAM/swap/load (psutil reads the shared /proc, so these are
host-wide even when the agent runs in a container) and GPU state via nvidia-smi
(bind-mounted into the agent like the orchestrator does for its metrics).

Nothing here mutates state — it only observes.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

import psutil
import structlog

log = structlog.get_logger()


@dataclass
class Gpu:
    index: int
    name: str
    mem_total_mb: int
    mem_free_mb: int
    util_pct: float


@dataclass
class Resources:
    physical_cores: int
    logical_cores: int
    load1: float
    load5: float
    load15: float
    ram_total_mb: int
    ram_available_mb: int
    swap_used_pct: float
    gpus: list[Gpu] = field(default_factory=list)

    @property
    def load_per_core(self) -> float:
        """1-min load average normalized to physical cores (the headroom metric)."""
        return self.load1 / self.physical_cores if self.physical_cores else self.load1


def _probe_gpus() -> list[Gpu]:
    """Best-effort GPU probe via nvidia-smi. Returns [] if unavailable."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        log.warning("gpu_probe_failed", error=str(exc))
        return []

    gpus: list[Gpu] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            gpus.append(Gpu(
                index=int(parts[0]),
                name=parts[1],
                mem_total_mb=int(float(parts[2])),
                mem_free_mb=int(float(parts[3])),
                util_pct=float(parts[4]),
            ))
        except ValueError:
            continue
    return gpus


def probe() -> Resources:
    load1, load5, load15 = psutil.getloadavg()
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    return Resources(
        physical_cores=psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
        logical_cores=psutil.cpu_count() or 1,
        load1=load1, load5=load5, load15=load15,
        ram_total_mb=vm.total // (1024 * 1024),
        ram_available_mb=vm.available // (1024 * 1024),
        swap_used_pct=sm.percent,
        gpus=_probe_gpus(),
    )
