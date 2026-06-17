"""
System metrics endpoint — Server-Sent Events stream.

Streams CPU, RAM, and GPU stats every 2 seconds.
GPU data is collected via nvidia-smi subprocess (no extra library needed).
"""
import asyncio
import json
import subprocess
import time
from typing import AsyncGenerator

import psutil
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.auth.deps import require_viewer
from app.models.user import User

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _gpu_stats() -> list[dict]:
    """Query nvidia-smi for per-GPU utilization, memory, and temperature."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,utilization.memory,"
                "memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=3,
        )
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 8:
                continue
            idx, name, gpu_pct, mem_pct, mem_used, mem_total, temp, pwr, pwr_lim = parts[:9]
            gpus.append({
                "index":    int(idx),
                "name":     name,
                "gpu_pct":  _safe_float(gpu_pct),
                "mem_pct":  _safe_float(mem_pct),
                "mem_used_mb":  _safe_float(mem_used),
                "mem_total_mb": _safe_float(mem_total),
                "temp_c":   _safe_float(temp),
                "power_w":  _safe_float(pwr),
                "power_limit_w": _safe_float(pwr_lim),
            })
        return gpus
    except Exception:
        return []


def _safe_float(v: str):
    try:
        return round(float(v), 1)
    except (ValueError, TypeError):
        return None


def _collect() -> dict:
    cpu_pct  = psutil.cpu_percent(interval=None)
    vm       = psutil.virtual_memory()
    swap     = psutil.swap_memory()
    disk     = psutil.disk_usage("/")
    net_io   = psutil.net_io_counters()

    return {
        "cpu_pct":      round(cpu_pct, 1),
        "cpu_count":    psutil.cpu_count(logical=True),
        "ram_pct":      round(vm.percent, 1),
        "ram_used_mb":  round(vm.used / 1024 / 1024, 0),
        "ram_total_mb": round(vm.total / 1024 / 1024, 0),
        "swap_pct":     round(swap.percent, 1),
        "disk_pct":     round(disk.percent, 1),
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
        "disk_total_gb":round(disk.total / 1024 / 1024 / 1024, 1),
        "net_sent_mb":  round(net_io.bytes_sent / 1024 / 1024, 1),
        "net_recv_mb":  round(net_io.bytes_recv / 1024 / 1024, 1),
        "gpus":         _gpu_stats(),
    }


async def _metric_stream(interval: float = 2.0) -> AsyncGenerator[str, None]:
    # Prime psutil CPU measurement (first call always returns 0.0)
    psutil.cpu_percent(interval=None)
    await asyncio.sleep(0.5)

    while True:
        data = await asyncio.to_thread(_collect)
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(interval)


@router.get("/stream")
async def metrics_stream(
    _: User = Depends(require_viewer),
):
    """SSE stream of system metrics. Connect with EventSource('/api/metrics/stream')."""
    return StreamingResponse(
        _metric_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.get("/snapshot")
async def metrics_snapshot(
    _: User = Depends(require_viewer),
):
    """Single metrics snapshot (non-streaming)."""
    psutil.cpu_percent(interval=None)
    await asyncio.sleep(0.5)
    return await asyncio.to_thread(_collect)


# ── Storage usage (admin) ─────────────────────────────────────────────────────
# Postgres DB size + MinIO bucket sizes, so admins can watch data growth (the
# snapshot/image buckets grow fastest, especially with per-detection frames).
# Listing object sizes is O(objects), so the result is cached with a TTL.
_storage_cache: dict = {"ts": 0.0, "data": None}
_STORAGE_TTL = 300.0


def _postgres_bytes() -> int | None:
    from app.db import SessionLocal
    try:
        db = SessionLocal()
        try:
            return int(db.execute(text("SELECT pg_database_size(current_database())")).scalar())
        finally:
            db.close()
    except Exception:
        return None


def _minio_usage() -> list[dict]:
    from app.minio_client import get_minio
    out: list[dict] = []
    try:
        mc = get_minio()
        for b in mc.list_buckets():
            n = total = 0
            try:
                for obj in mc.list_objects(b.name, recursive=True):
                    n += 1
                    total += (obj.size or 0)
            except Exception:
                pass
            out.append({"bucket": b.name, "objects": n, "bytes": total})
    except Exception:
        pass
    return out


def _storage_collect() -> dict:
    pg = _postgres_bytes()
    buckets = _minio_usage()
    return {
        "postgres_bytes": pg,
        "buckets": buckets,
        "minio_total_bytes": sum(b["bytes"] for b in buckets),
    }


@router.get("/storage")
async def metrics_storage(_: User = Depends(require_viewer)):
    """Postgres + MinIO storage breakdown for the admin sidebar. Cached (TTL)
    because summing bucket object sizes scans the whole bucket."""
    now = time.time()
    if _storage_cache["data"] is None or now - _storage_cache["ts"] > _STORAGE_TTL:
        _storage_cache["data"] = await asyncio.to_thread(_storage_collect)
        _storage_cache["ts"] = now
    return {**_storage_cache["data"], "cached_age_s": round(time.time() - _storage_cache["ts"])}
