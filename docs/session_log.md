# Session Log
*This file is the source of truth for resuming work after a context reset.*
*To resume: start a new session and say "Please read docs/session_log.md and continue where we left off."*

---

## How to Resume After a Context Reset

1. Start a new Cowork session
2. Say: **"Please read docs/session_log.md in the sentinel-pipeline project and continue where we left off."**
3. I'll read this file and pick up exactly where we stopped.

---

## Project: Sentinel Pipeline

**GitHub:** https://github.com/dabeckham/sentinel-pipeline  
**Local workspace:** `C:\Users\Don\Claude\Projects\Video analysis\`  
**User:** Don Beckham (dabeckham@yahoo.com, GitHub: dabeckham)

### What This System Does
Distributed, containerized video analysis pipeline. Cameras FTP motion-triggered video clips to a network path. The system ingests them, detects motion regions per frame, classifies and tracks objects across frames, stores metadata + snapshots, and exposes a browser UI for review. Workers (MD and OC) run as Docker containers and can be distributed across multiple machines/GPUs.

---

## All Locked Decisions

| Decision | Choice |
|---|---|
| Message broker | RabbitMQ (AMQP, durable queues, DLX) |
| MD algorithm | MOG2 background subtraction (OpenCV) |
| OC model | YOLO26 (Ultralytics, Jan 2026) |
| Object tracker | ByteTrack |
| Auth | Multi-user RBAC: admin / operator / viewer roles. JWT. LAN trust mode (admin-toggleable, CIDR-based) |
| Object storage | MinIO (S3-compatible, self-hosted) |
| Database | PostgreSQL 16 + SQLAlchemy 2 + Alembic |
| UI stack | React 18 + Vite + TailwindCSS + React Query |
| Primary input | FTP file-first (cameras write motion-triggered clips) |
| RTSP streams | Deferred to Phase 6 |
| Container runtime | Docker + Docker Compose |

---

## Technology Stack

| Layer | Technology |
|---|---|
| Orchestrator API | Python 3.12, FastAPI |
| MD Workers | Python 3.12, OpenCV (MOG2) |
| OC Workers | Python 3.12, Ultralytics YOLO26, ByteTrack |
| Message Broker | RabbitMQ 3.x (pika client) |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic |
| Object Storage | MinIO |
| UI | React 18, Vite, TailwindCSS, React Query |
| Auth | JWT (python-jose), bcrypt |
| GPU Workers | nvidia/cuda:12.x-runtime base image |

---

## Repository Structure (planned)

```
sentinel-pipeline/
├── orchestrator/       # FastAPI + file watcher + queue consumer/publisher
├── md-worker/          # Motion detection (OpenCV MOG2)
├── oc-worker/          # Object classification (YOLO26 + ByteTrack)
├── ui/                 # React 18 browser UI
├── infra/              # RabbitMQ, MinIO, Postgres config
├── docs/               # All documentation
├── tests/              # Integration + unit tests
├── docker-compose.yml
├── docker-compose.gpu.yml
├── .env.example
├── .gitignore
└── README.md
```

---

## Build Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Infrastructure skeleton: Docker Compose, DB schema, RabbitMQ queues, MinIO buckets, Orchestrator stub | 🔲 **NEXT** |
| 2 | Core pipeline: FTP watcher → MD worker → OC worker → DB writer | 🔲 Planned |
| 3 | Auth & REST API: JWT, roles, LAN trust, all endpoints, WebSocket | 🔲 Planned |
| 4 | Browser UI: all pages (Ingest, Status, Review, Config, User Mgmt) | 🔲 Planned |
| 5 | Hardening: DLQ retry, dedup, graceful shutdown, logging, tests | 🔲 Planned |
| 6 | RTSP live stream worker pool | 🔲 Future |

---

## Queue Design

| Queue | Producer | Consumer | Key Payload Fields |
|---|---|---|---|
| `ingest` | Orchestrator | MD Workers | `job_id, video_path, source_type, options` |
| `motion_results` | MD Workers | OC Workers | `job_id, frame_index, timestamp_ms, bounding_boxes[], crop_paths[]` |
| `oc_results` | OC Workers | Orchestrator | `job_id, track_id, frame_index, class_label, confidence, bbox, snapshot_path` |
| `dlx.*` | RabbitMQ (auto) | Dead-letter handler | Failed/expired messages |

---

## Database Schema (draft)

```sql
jobs (id, file_path, file_hash, source_path, status, created_at, completed_at)
workers (id, type, host, queue_name, status, last_seen_at, model_version)
motion_events (id, job_id, frame_index, timestamp_ms, bounding_boxes jsonb)
tracks (id, job_id, class_label, confidence_max, first_frame, last_frame, snapshot_path, created_at)
detections (id, track_id, job_id, frame_index, class_label, confidence, bbox jsonb, crop_path, created_at)
users (id, username, email, password_hash, role, created_at, last_login)
config (key, value, updated_by, updated_at)
```

---

## Files Created So Far

| File | Description |
|---|---|
| `README.md` | Project overview, architecture table, phase tracker, quick start |
| `.env.example` | All environment variables with comments |
| `.gitignore` | Python + Node + Docker + secrets |
| `docs/architecture_outline.md` | Full architecture spec (v0.2) |
| `docs/repo_structure.md` | Planned folder layout |
| `docs/github_setup_guide.md` | GitHub setup walkthrough for non-git users |
| `docs/session_log.md` | This file |

---

## Current Status

**Phase 1 infrastructure skeleton COMPLETE — ready to deploy.**

### Docker Host Details (private — do not commit)
- Hardware: i9-9900k, 2x RTX 3060 (12GB VRAM each)
- GPU 0: ~5GB used by Frigate (embeddings, detector, 17x ffmpeg streams)
- GPU 1: ~12GB free (Ollama unloads when idle — use this for OC workers)
- CUDA 13.0, Driver 580.159.03, TensorRT available (Frigate stable-tensorrt image)
- Existing containers: frigate, ollama, nginx-proxy
- Docker 29.5.2, Docker Compose v5.1.4 (use `docker compose`, not `docker-compose`)

### Phase 1 Files Created
| File | Description |
|---|---|
| `docker-compose.yml` | Full stack: rabbitmq, postgres, minio, minio-init, orchestrator, md-worker, oc-worker, ui |
| `docker-compose.gpu.yml` | GPU override — pins OC worker to GPU 1 (configurable via GPU_DEVICE_ID) |
| `infra/rabbitmq/definitions.json` | Pre-configured queues: ingest, motion_results, oc_results + DLX exchanges |
| `infra/rabbitmq/rabbitmq.conf` | Loads definitions, sets 1hr consumer timeout |
| `infra/minio/init.sh` | Creates frames-raw, crops, snapshots buckets on startup |
| `infra/postgres/init.sql` | Enables uuid-ossp and pg_trgm extensions |
| `orchestrator/Dockerfile` | Python 3.12-slim, runs alembic migrate then uvicorn |
| `orchestrator/requirements.txt` | FastAPI, SQLAlchemy, Alembic, pika, minio, JWT, bcrypt |
| `orchestrator/app/main.py` | FastAPI app with CORS, lifespan, health router |
| `orchestrator/app/config.py` | Pydantic settings — all config from env vars |
| `orchestrator/app/api/health.py` | GET /api/health endpoint |
| `orchestrator/app/models/` | SQLAlchemy models: Job, Worker, MotionEvent, Track, Detection, User, Config |
| `orchestrator/alembic/` | Alembic env.py + migration 0001_initial_schema |
| `md-worker/Dockerfile` | Python 3.12-slim + OpenCV |
| `md-worker/requirements.txt` | opencv, pika, minio, structlog |
| `md-worker/worker/main.py` | Phase 1 stub — logs readiness, loops |
| `oc-worker/Dockerfile` | Python 3.12-slim (CPU) |
| `oc-worker/Dockerfile.gpu` | nvidia/cuda:12.4.1 base (GPU/TensorRT) |
| `oc-worker/requirements.txt` | ultralytics, pika, minio (CPU) |
| `oc-worker/requirements.gpu.txt` | + torch cu124, tensorrt 10.4 |
| `oc-worker/worker/main.py` | Phase 1 stub — logs readiness, loops |
| `ui/Dockerfile` | Nginx serving stub page (Phase 4: full React build) |
| `ui/stub/index.html` | Placeholder page pointing to /api/health |

### Next Step: Deploy Phase 1
Run on Docker host (192.168.55.10):
```bash
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
cp .env.example .env
# Edit .env — set all passwords and INGEST_SOURCE_PATH
docker compose up -d
# Verify: curl http://localhost:8000/api/health
# RabbitMQ mgmt: http://localhost:15672
# MinIO console: http://localhost:9001
```

### Phase 2 Tasks (next coding session)
1. Orchestrator: FTP path watcher (watchdog) → publish to ingest queue
2. MD Worker: MOG2 motion detection → frame crops → publish to motion_results
3. OC Worker: YOLO26 inference + ByteTrack → publish to oc_results
4. Orchestrator: oc_results consumer → write to DB + copy snapshot to MinIO

---

## Session History

- **2026-06-05 Session 1:** Full architecture spec, all design decisions, GitHub repo setup, README, .env.example, .gitignore, all docs pushed to github.com/dabeckham/sentinel-pipeline.
- **2026-06-05 Session 1 cont.:** Phase 1 complete — full infrastructure skeleton built and ready to deploy.

---
