# Sentinel Pipeline

A distributed, containerized video analysis system that ingests motion-triggered camera footage, detects and classifies objects frame-by-frame, tracks them across time, and stores everything for review through a browser-based UI.

**Current version: v0.6.0** — All 5 phases complete.

---

## What It Does

Cameras FTP motion-triggered video clips to a network location. Sentinel Pipeline picks them up automatically and runs them through a multi-stage analysis pipeline:

```
FTP folder → [Ingest Queue] → Motion Detection → [Motion Queue] → Object Classification → [Result Queue] → Database + Storage
```

Results — with snapshot thumbnails of every detected object — are browsable through a web UI. Workers run as Docker containers and can be distributed across multiple machines and GPUs.

---

## Architecture

| Component | Technology | Role |
|---|---|---|
| **Orchestrator** | Python 3.12 / FastAPI | File watcher, REST + WebSocket API, queue consumer, metrics SSE |
| **MD Workers** | Python 3.10 / OpenCV | Frigate-style weighted-average motion detection, frame extraction |
| **OC Workers** | Python 3.10 / YOLO11s + Norfair | Object classification + scale-normalized tracking |
| **Message Broker** | RabbitMQ | Durable job queues with dead-letter routing |
| **Database** | PostgreSQL 16 / SQLAlchemy 2 / Alembic | Job, track, and detection metadata |
| **Object Storage** | MinIO | Full-frame snapshots and best-shot thumbnails |
| **UI** | React 18 / Vite / TailwindCSS | Live job status, track review, system metrics |

### Pipeline Flow

```
Video file → Orchestrator (watcher)
  → MD Worker: Frigate-style motion detection (weighted avg background)
               Extracts motion frames, publishes full-frame JPEG + metadata
  → OC Worker: YOLO11s detect-only (conf ≥ 0.5)
               Norfair tracker (Frigate scale-normalized distance, hit_counter_max=30)
               Saves _best.jpg thumbnail per track (most-centered frame)
               On is_final: classifies track_type (moving vs stationary)
  → Orchestrator (result_consumer): writes Job/Track/Detection rows, broadcasts WebSocket events
```

---

## Features

### Pipeline
- **Queue-based architecture** — each stage independently scalable, workers deployable on any host
- **Frigate-style motion detection** — weighted average background model with temporal smoothing; background only absorbs moving objects after 10 consecutive motion frames
- **YOLO11s + Norfair tracking** — detect-only mode with scale-normalized Kalman tracking; prevents ghost detections from Kalman predictions
- **Track classification** — normalized centroid displacement at job close-out classifies each track as `moving` or `stationary` (threshold 0.3)
- **Multi-GPU support** — two OC workers: primary on GPU 1, secondary on GPU 0 (shared with Frigate)
- **Startup recovery** — on restart, stuck jobs are re-queued and missed ingest files are submitted automatically
- **Dead-letter routing** — failed messages routed to DLX queues; requeueable via API

### UI
- **Jobs page** — infinite scroll (50/load), resizable columns, status hover popover with per-stage timeline and durations, kill button (two-step confirm)
- **Tracks page** — card grid with Moving/Stationary/All segmented filter, multi-select camera + class dropdowns, time range filter (Today/Week/Month/Custom) with calendar and active-day dots, infinite scroll, bbox overlay on thumbnails
- **System metrics bar** — persistent bottom strip streaming CPU %, RAM, disk, per-GPU utilization / VRAM / temperature / power via SSE
- **Live status** — WebSocket job events with toast notifications; WS owned by Layout (no double connections)
- **Job stages** — hover any job status badge to see a timeline of MD processing → MD complete → OC processing → completed with wall-clock times and durations
- **Multi-user auth** — JWT, RBAC (admin / operator / viewer), optional LAN trust mode

---

## Project Structure

```
sentinel-pipeline/
├── orchestrator/           # FastAPI service — watcher, API, queue consumer, metrics SSE
│   ├── app/
│   │   ├── api/            # jobs, tracks, users, metrics, snapshots, dlx
│   │   ├── models/         # SQLAlchemy — Job, Track, Detection, User
│   │   ├── schemas/        # Pydantic response schemas
│   │   └── services/       # result_consumer, track classifier
│   └── alembic/versions/   # 0001–0005 migrations
├── md-worker/              # Motion detection worker (Frigate-style weighted average)
├── oc-worker/              # Object classification (YOLO11s + Norfair, GPU)
├── ui/                     # React 18 browser UI
│   └── src/
│       ├── pages/          # Jobs, Tracks, Users, Dashboard
│       └── components/     # MetricsBar, Layout
├── infra/                  # RabbitMQ, MinIO, PostgreSQL config
├── docs/                   # Architecture, deployment, session log, DR
├── docker-compose.yml      # Full stack (CPU OC worker)
├── docker-compose.gpu.yml  # GPU override — oc-worker (GPU 1) + oc-worker-2 (GPU 0)
└── .env.example            # Environment variable template
```

---

## Quick Start

> Prerequisites: Docker, Docker Compose v2+, Git, NVIDIA Container Toolkit (for GPU mode)

```bash
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
cp .env.example .env
# Edit .env — set JWT_SECRET_KEY, RABBITMQ_PASSWORD, POSTGRES_PASSWORD, MINIO_SECRET_KEY

# CPU-only mode
docker compose up -d

# GPU mode (recommended) — two OC workers on RTX 3060s
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker oc-worker-2
```

Then open `http://localhost:3000` — default login: `admin` / `changeme` (change immediately).

> ⚠️ **OC worker GPU rebuild must use both compose files:** `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build oc-worker`
> Running `docker-compose.gpu.yml` alone puts the worker on the wrong Docker network (RabbitMQ DNS fails).

---

## Build Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Infrastructure skeleton (Docker Compose, PostgreSQL, RabbitMQ, MinIO) | ✅ Complete |
| 2 | Core pipeline (watcher → MD → OC → DB) + GPU acceleration | ✅ Complete |
| 3 | Auth & REST API (JWT, RBAC, WebSocket, Alembic) | ✅ Complete v0.3.0 |
| 4 | Browser UI (React 18 + Vite + TailwindCSS) | ✅ Complete v0.4.0 |
| 5 | Hardening (DLQ, startup recovery, graceful shutdown, cleanup API) | ✅ Complete v0.5.0 |
| 6 | Post-launch improvements (track classification, metrics bar, dwell time Phase 1) | ✅ v0.6.0 |
| 7 | RTSP live stream worker pool | 🔲 Future |

---

## API Reference (v0.6.0)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | none | Get JWT token |
| GET | `/api/health` | none | Version + liveness |
| GET | `/api/stats` | viewer | Job/track/detection counts + class breakdown |
| GET | `/api/jobs` | viewer | Paginated job list; supports status filter; returns camera_name, timestamps, track_count |
| GET | `/api/jobs/{id}` | viewer | Single job with full stage timestamps |
| POST | `/api/jobs/{id}/cancel` | operator | Mark active job as failed (cancelled by user) |
| GET | `/api/tracks` | viewer | Paginated tracks; filter by camera, class, track_type, date range |
| GET | `/api/tracks/cameras` | viewer | Distinct camera names |
| GET | `/api/tracks/active-days` | viewer | Dates with track data (for calendar dot highlighting) |
| GET | `/api/tracks/{id}` | viewer | Track detail with full detections list |
| GET | `/api/snapshots/{path}` | viewer | Proxy MinIO snapshots with JWT auth |
| GET | `/api/metrics/stream` | viewer | SSE — CPU, RAM, disk, per-GPU stats every 2s |
| GET/PUT | `/api/config` | admin | Runtime config |
| GET/POST/PATCH/DELETE | `/api/users` | admin | User CRUD |
| GET | `/api/dlx/counts` | admin | Dead-letter queue depths |
| POST | `/api/dlx/requeue` | admin | Move DLX messages back to source queue |
| POST | `/api/snapshots/cleanup` | admin | Delete `_f{frame}.jpg` playback frames, keep `_best.jpg` |
| WS | `/ws/jobs` | viewer | Live job status events |

---

## Storage Layout (MinIO `snapshots` bucket)

| Path | Purpose |
|---|---|
| `{job_id}/track_{id:06d}_best.jpg` | Best-shot thumbnail — most vertically-centered frame. `tracks.snapshot_bbox` stores matching bbox for UI overlay. |
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

Migrations run automatically on orchestrator startup.

---

## Known Gotchas

1. **OC worker GPU build must use both compose files** — `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build oc-worker`
2. **bcrypt pinned to 3.2.2** — passlib 1.7.4 incompatible with bcrypt 4.x
3. **Dockerfile.gpu uses ubuntu22.04 + python3.10** — deadsnakes PPA unreachable from build host; don't switch base image without testing
4. **NFS + inotify = silent failure** — orchestrator uses `PollingObserver`
5. **CUDA_VISIBLE_DEVICES must be "0" inside container** — Docker remaps physical GPU → container GPU 0
6. **GPU 0 shared with Frigate** — oc-worker-2 runs on GPU 0; if Frigate starves for VRAM, `docker stop sentinel-oc-worker-2`
7. **nvidia-smi in orchestrator** — requires bind-mount of binary AND `libnvidia-ml.so.1` from host, plus `/dev/nvidia*` device files

---

## Documentation

- [Deployment Guide](docs/deployment.md) — infrastructure, first-time setup, scaling, DB migrations
- [Disaster Recovery](docs/disaster_recovery.md) — backup, restore, recovery procedures
- [Session Log](docs/session_log.md) — full build history, current state, how to resume after context reset
- [Architecture Outline](docs/architecture_outline.md)

---

## License

MIT
