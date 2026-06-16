# Sentinel Pipeline

A distributed, containerized video analysis system that ingests motion-triggered camera footage, detects and classifies objects frame-by-frame, tracks them across time, and stores everything for review through a browser-based UI.

**Current version: v0.6.0** — All 5 phases complete.

---

## What It Does

Cameras FTP motion-triggered video clips to a network location. Sentinel Pipeline picks them up automatically and runs them through a two-stage analysis pipeline:

```
FTP folder → [Ingest Queue] → MD Worker → [Motion Queue] → OC Worker → [Result Queue] → Database + Storage
```

The MD Worker identifies which frames contain motion and sends a single job descriptor. The OC Worker opens the video file directly, runs TRT FP16 inference and ByteTrack tracking end-to-end for the entire job. Results — with snapshot thumbnails of every detected object — are browsable through a web UI.

---

## Architecture

| Component | Technology | Role |
|---|---|---|
| **Orchestrator** | Python 3.12 / FastAPI | File watcher, REST + WebSocket API, queue consumer, metrics SSE |
| **MD Workers** | Python 3.10 / OpenCV | Frigate-style weighted-average motion detection, motion frame extraction |
| **OC Workers** | Python 3.10 / TRT FP16 + ByteTrack | Object classification + cross-frame tracking (~42fps, ~13MB VRAM/worker) |
| **Message Broker** | RabbitMQ | Durable job queues with dead-letter routing |
| **Database** | PostgreSQL 16 / SQLAlchemy 2 / Alembic | Job, track, and detection metadata |
| **Object Storage** | MinIO | Full-frame snapshots and best-shot thumbnails |
| **UI** | React 18 / Vite / TailwindCSS | Live job status, track review, worker panel, system metrics |

### Pipeline Flow

```
Video file → Orchestrator (watcher)
  → MD Worker: Frigate-style motion detection (weighted average background)
               Identifies motion frame indices
               Sends ONE job descriptor to motion_results queue:
               { job_id, video_path, motion_frames:[...], fps, camera_name, recorded_at }
  → OC Worker: Opens video directly from NAS mount
               TRT FP16 inference (yolo11s.engine, conf ≥ 0.5)
               ByteTrack cross-frame tracking (supervision 0.22.0)
               Saves _best.jpg thumbnail per track (most vertically-centered frame)
               Sends all detections bundled in one final message
  → Orchestrator (result_consumer): writes Job/Track/Detection rows, broadcasts WebSocket events
```

**One worker owns one job start to finish** — no ByteTrack state split across workers, no frames in the queue.

---

## Features

### Pipeline
- **Job-descriptor architecture** — MD sends one lightweight message per job; OC reads video directly from NAS
- **TRT FP16 inference** — `yolo11s.engine` auto-exported on first run (~4 min, cached to shared volume); ~42fps, ~13MB VRAM/worker
- **ByteTrack tracking** — supervision 0.22.0; stable track IDs across frames for entire job
- **4 GPU OC workers** — GPU 1: workers 1, 3, 4 (~771MB VRAM total); GPU 0: worker 2 (shared with Frigate)
- **Frigate-style motion detection** — weighted average background model with temporal smoothing
- **Track classification** — normalized centroid displacement classifies each track as `moving` or `stationary`
- **Startup recovery** — on restart, stuck jobs are re-queued and missed ingest files are submitted automatically
- **Dead-letter routing** — failed messages routed to DLX queues; requeueable via API

### Workers
- **Live worker panel** — labels (`OC-GPU-1`, `MD-CPU-1`), status dots (idle/processing/suspended), stats callout with cumulative FPS/jobs/frames
- **Suspend/resume** — right-click any worker; takes effect after current job; nack+requeues cleanly
- **Self-healing registry** — after orchestrator restart, workers re-register automatically within one heartbeat cycle (~15s) with no manual intervention

### UI
- **Jobs page** — infinite scroll, resizable columns, status hover popover with per-stage timeline, pause/resume/kill, bulk actions
- **Tracks page** — card grid with Moving/Stationary filter, multi-select camera + class dropdowns, date range filter with calendar, infinite scroll, bbox overlay on thumbnails
- **System metrics bar** — persistent bottom strip streaming CPU, RAM, disk, per-GPU utilization / VRAM / temperature / power via SSE
- **Live status** — WebSocket job events with toast notifications
- **Multi-user auth** — JWT, RBAC (admin / operator / viewer), optional LAN trust mode

---

## Project Structure

```
sentinel-pipeline/
├── orchestrator/               # FastAPI service — watcher, API, queue consumer, metrics SSE
│   ├── app/
│   │   ├── api/                # jobs, tracks, users, workers, metrics, snapshots, dlx
│   │   ├── models/             # SQLAlchemy — Job, Track, Detection, User
│   │   ├── schemas/            # Pydantic response schemas
│   │   └── services/           # result_consumer, worker_registry, track classifier
│   └── alembic/versions/       # 0001–0008 migrations
├── md-worker/                  # Motion detection worker (Frigate-style weighted average)
│   └── worker/
│       ├── main.py             # Consume ingest, detect motion, publish job descriptor
│       ├── detector.py         # Weighted-average background model
│       └── worker_events.py    # Lifecycle events (online/heartbeat/offline)
├── oc-worker/                  # Object classification (TRT FP16 + ByteTrack)
│   ├── Dockerfile.gpu          # CUDA base, TRT, supervision
│   └── worker/
│       ├── main.py             # Consume job descriptor, run full pipeline
│       ├── detector.py         # TRT FP16 inference + ByteTrack
│       └── worker_events.py    # Lifecycle events + suspension polling
├── ui/                         # React 18 browser UI
│   └── src/
│       ├── pages/              # Jobs, Tracks, Users, Dashboard
│       └── components/         # MetricsBar, PipelineStatus (worker panel), Layout
├── infra/                      # RabbitMQ, MinIO, PostgreSQL config
├── docs/                       # Architecture, deployment, session log, DR
├── docker-compose.yml          # Full stack (CPU fallback OC worker)
├── docker-compose.override.yml # GPU workers (auto-merged — no -f flags needed)
└── .env.example                # Environment variable template
```

---

## Quick Start

> Prerequisites: Docker, Docker Compose v2+, Git, NVIDIA Container Toolkit (for GPU mode)

```bash
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
cp .env.example .env
# Edit .env — set JWT_SECRET_KEY, RABBITMQ_PASSWORD, POSTGRES_PASSWORD, MINIO_SECRET_KEY

# Create model cache volume (one time)
docker volume create sentinel-pipeline_yolo-models

# Start full stack — GPU OC workers included automatically
docker compose up -d
```

Then open `http://localhost:3000` — default login: `admin` / `changeme` (change immediately).

### GPU Rebuild

```bash
# Build once — all 4 OC workers share the same image
docker compose build oc-worker --build-arg CACHEBUST=$(date +%s)
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4
```

> ⚠️ **One image, four containers.** Workers 2/3/4 use `image: sentinel-oc-worker-gpu:latest` — no `build:` block. Never add `build:` to workers 2/3/4.

---

## Build Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Infrastructure skeleton (Docker Compose, PostgreSQL, RabbitMQ, MinIO) | ✅ Complete |
| 2 | Core pipeline (watcher → MD → OC → DB) + GPU acceleration | ✅ Complete |
| 3 | Auth & REST API (JWT, RBAC, WebSocket, Alembic) | ✅ Complete v0.3.0 |
| 4 | Browser UI (React 18 + Vite + TailwindCSS) | ✅ Complete v0.4.0 |
| 5 | Hardening (DLQ, startup recovery, graceful shutdown, cleanup API) | ✅ Complete v0.5.0 |
| 6 | Post-launch improvements (track classification, metrics, TRT pipeline, worker panel) | ✅ v0.6.0 |
| 7 | RTSP live stream worker pool | 🔲 Future |

---

## API Reference (v0.6.0)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | none | Get JWT token |
| GET | `/api/health` | none | Version + liveness |
| GET | `/api/stats` | viewer | Job/track/detection counts + class breakdown |
| GET | `/api/jobs` | viewer | Paginated job list with status filter and infinite scroll |
| GET | `/api/jobs/{id}` | viewer | Single job with full stage timestamps |
| POST | `/api/jobs/{id}/pause` | operator | Pause a queued job |
| POST | `/api/jobs/{id}/resume` | operator | Resume a paused job |
| POST | `/api/jobs/{id}/cancel` | operator | Mark active job as failed |
| DELETE | `/api/jobs/{id}` | admin | Remove a job record |
| POST | `/api/jobs/bulk/pause` | operator | Pause multiple jobs |
| POST | `/api/jobs/bulk/kill` | operator | Kill multiple active jobs |
| GET | `/api/tracks` | viewer | Paginated tracks; filter by camera, class, track_type, date range |
| GET | `/api/tracks/cameras` | viewer | Distinct camera names |
| GET | `/api/tracks/active-days` | viewer | Dates with track data (calendar dot highlighting) |
| GET | `/api/tracks/{id}` | viewer | Track detail with full detections list |
| GET | `/api/snapshots/{path}` | viewer | Proxy MinIO snapshots with JWT auth |
| POST | `/api/snapshots/cleanup` | admin | Delete `_f{frame}.jpg` playback frames, keep `_best.jpg` |
| GET | `/api/metrics/stream` | viewer | SSE — CPU, RAM, disk, per-GPU stats every 2s |
| GET | `/api/workers` | viewer | All known workers with status and stats |
| POST | `/api/workers/{id}/suspend` | operator | Suspend a worker |
| POST | `/api/workers/{id}/resume` | operator | Resume a suspended worker |
| GET/PUT | `/api/config` | admin | Runtime config |
| GET/POST/PATCH/DELETE | `/api/users` | admin | User CRUD |
| GET | `/api/dlx/counts` | admin | Dead-letter queue depths |
| POST | `/api/dlx/requeue` | admin | Move DLX messages back to source queue |
| WS | `/ws/jobs` | viewer | Live job status events |

---

## Storage Layout (MinIO `snapshots` bucket)

| Path | Purpose |
|---|---|
| `{job_id}/track_{id:06d}_best.jpg` | Best-shot thumbnail — most vertically-centered detection frame. `tracks.snapshot_bbox` stores matching bbox for UI overlay. |
| `{job_id}/track_{id:06d}_f{frame:06d}.jpg` | Per-detection full frame for in-browser playback. Clean up with `/api/snapshots/cleanup`. |

---

## Database Migrations

| Revision | Description |
|---|---|
| 0001 | Initial schema (jobs, tracks, detections, users) |
| 0002 | camera_name, recorded_at, started_at, ended_at on tracks and jobs |
| 0003 | snapshot_bbox JSON column on tracks |
| 0004 | track_type String(16) column + index on tracks |
| 0005 | md_complete JobStatus enum value; md_started_at, md_completed_at, oc_started_at on jobs |
| 0006 | md_worker_id, oc_worker_id columns on jobs |
| 0007 | `paused` value added to jobstatus enum |
| 0008 | pipeline_settings key-value table |
| 0009 | `jobs.file_hash` UNIQUE — race-safe ingest dedup |

Migrations run automatically on orchestrator startup.

---

## Known Gotchas

1. **`docker compose up -d` starts everything** — `docker-compose.override.yml` is auto-merged; no `-f` flags needed
2. **One image for all 4 OC workers** — build only `oc-worker`; never add `build:` to workers 2/3/4
3. **TRT engine warmup required** — `YOLO(engine_path, task="detect")` leaves `.names` empty until first `predict()` call; worker handles this automatically
4. **bcrypt pinned to 3.2.2** — passlib 1.7.4 incompatible with bcrypt 4.x
5. **Dockerfile.gpu uses ubuntu22.04 + python3.10** — deadsnakes PPA unreachable from build host; don't switch base image without testing
6. **NFS + inotify = silent failure** — orchestrator uses `PollingObserver`
7. **CUDA_VISIBLE_DEVICES must be "0" inside container** — Docker remaps physical GPU → container GPU 0
8. **GPU 0 shared with Frigate** — oc-worker-2 runs on GPU 0; if Frigate starves for VRAM, `docker stop sentinel-oc-worker-2`
9. **RabbitMQ mnesia wipe** — `change_password` silently does nothing if user doesn't exist; always use `add_user` after a credential rotation on a fresh container
10. **`nvidia-smi` in orchestrator** — requires bind-mount of binary AND `libnvidia-ml.so.1` from host, plus `/dev/nvidia*` device files

---

## Documentation

- [Deployment Guide](docs/deployment.md) — infrastructure, first-time setup, scaling, DB migrations
- [Disaster Recovery](docs/disaster_recovery.md) — backup, restore, recovery procedures
- [Session Log](docs/session_log.md) — full build history, current state, how to resume after context reset
- [Architecture Outline](docs/architecture_outline.md)

---

## License

MIT
