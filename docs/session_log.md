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

## Current Status — End of Session 1

**GitHub repo is live and all files pushed.**  
SSH access to the Docker host has not yet been provided.  
Ready to begin **Phase 1** as soon as SSH access is available.

### Phase 1 Tasks (to be coded next)
1. `docker-compose.yml` — all services stubbed with correct networking
2. `docker-compose.gpu.yml` — GPU worker override
3. PostgreSQL schema + Alembic migration setup
4. RabbitMQ `definitions.json` — pre-configure all queues + DLX
5. MinIO bucket init script
6. Orchestrator FastAPI stub (health endpoint, settings loading)

---

## Session History

- **2026-06-05 Session 1:** Full architecture spec, all design decisions, GitHub repo setup, README, .env.example, .gitignore, all docs pushed to github.com/dabeckham/sentinel-pipeline.

---
