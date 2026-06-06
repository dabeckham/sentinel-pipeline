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
| 1 | Infrastructure skeleton: Docker Compose, DB schema, RabbitMQ queues, MinIO buckets, Orchestrator stub | ✅ Complete |
| 2 | Core pipeline: FTP watcher → MD worker → OC worker → DB writer | ✅ Complete — pipeline live, processing real footage |
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

**Phase 1 DEPLOYED AND VERIFIED. Starting Phase 2.**

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

### Verified Services (2026-06-06)
- `http://192.168.55.10:8000/api/health` ✅
- `http://192.168.55.10:15672` RabbitMQ mgmt ✅
- `http://192.168.55.10:9001` MinIO console ✅
- `http://192.168.55.10:3000` UI stub ✅

### Phase 2 Code — All Written (commit a68969d)
All Phase 2 files committed and pushed. See Files Created table below.

### Phase 2 Deployment — Active Debugging
Three bugs encountered and fixed during deployment. Current build (a68969d) is deploying now.

#### Bug 1: Watchdog inotify fails on NFS ✅ Fixed
`/ingest` is NFS4-mounted. inotify doesn't work on NFS.
Fix: switched `Observer` → `PollingObserver` in `orchestrator/app/services/watcher.py`.

#### Bug 2: INGEST_SOURCE_PATH collision ✅ Fixed
`.env` has `INGEST_SOURCE_PATH=/mnt/ds-one/sentinel-ingest` (host path).
Pydantic-settings picked this up and passed it to watchdog inside the container, where the path doesn't exist.
Fix: renamed field `ingest_source_path` → `ingest_watch_path` (maps to env var `INGEST_WATCH_PATH`, not in .env). Defaults to `/ingest`.
Note: Compose v5.1.4 `env_file:` appears to take precedence over `environment:` block — hardcoded field rename is the reliable fix.

#### Bug 3: RabbitMQ auth refused ✅ Fixed
Workers connecting with empty PLAIN credentials — RabbitMQ had NO users at all.
Root cause: `RABBITMQ_DEFAULT_USER`/`PASS` were empty when RabbitMQ first started; `.env` was not applied to the initial container.
Fix: manually created `sentinel` user via rabbitmqctl with administrator tag and full permissions on `/` vhost.
```bash
docker exec sentinel-rabbitmq rabbitmqctl add_user sentinel "$PASS"
docker exec sentinel-rabbitmq rabbitmqctl set_user_tags sentinel administrator
docker exec sentinel-rabbitmq rabbitmqctl set_permissions -p / sentinel ".*" ".*" ".*"
```

#### Bug 4: OC worker yolo26s.pt not found ✅ Fixed
`.env` has `YOLO_MODEL=yolo26s` (future model). Worker tried to load it and crashed.
Fix: renamed field `yolo_model` → `oc_model_name` (env var `OC_MODEL_NAME`, not in .env). Defaults to `yolo11s` (auto-downloads from ultralytics).

### Phase 2 Files Created
| File | Description |
|---|---|
| `orchestrator/app/db.py` | SQLAlchemy engine + SessionLocal |
| `orchestrator/app/services/amqp.py` | Thread-safe RabbitMQ publisher, auto-reconnect |
| `orchestrator/app/services/watcher.py` | PollingObserver watches /ingest, deduplicates by SHA-256, creates Job, publishes to ingest queue |
| `orchestrator/app/services/result_consumer.py` | Consumes oc_results, upserts Track, inserts Detection, marks job completed |
| `md-worker/worker/config.py` | Pydantic settings (MOG2 tuning, queues, MinIO) |
| `md-worker/worker/motion.py` | MOG2 background subtraction, returns crops per motion frame |
| `md-worker/worker/minio_client.py` | Upload crops as JPEG to MinIO |
| `md-worker/worker/main.py` | Consumes ingest queue, runs MOG2, publishes motion_results with is_final |
| `oc-worker/worker/config.py` | Pydantic settings (model=yolo11s, thresholds, GPU) |
| `oc-worker/worker/detector.py` | YOLO inference on crops + supervision ByteTrack per-job in-memory |
| `oc-worker/worker/minio_client.py` | Download crops + upload snapshots from/to MinIO |
| `oc-worker/worker/main.py` | Consumes motion_results, classifies crops, tracks, publishes oc_results |

---

## Session History

- **2026-06-05 Session 1:** Full architecture spec, all design decisions, GitHub repo setup, README, .env.example, .gitignore, all docs pushed to github.com/dabeckham/sentinel-pipeline.
- **2026-06-05 Session 1 cont.:** Phase 1 complete — full infrastructure skeleton built and ready to deploy.
- **2026-06-06 Session 2:** Deployment completed. Fixed .gitignore models/ scope bug, added PYTHONPATH to Dockerfile, committed orchestrator models. Full stack verified on 192.168.55.10.
- **2026-06-06 Session 3:** Phase 2 all code written (orchestrator watcher+consumer, MD worker MOG2, OC worker YOLO11+ByteTrack). Fixed: inotify/NFS, INGEST_SOURCE_PATH collision, yolo26s model name, RabbitMQ no-users (manual rabbitmqctl). Pipeline fully deployed and verified end-to-end.
- **2026-06-06 Session 4:** Phase 2 verified live on real driveway footage. All 5 services healthy. Jobs completing. Detections written to PostgreSQL (car/truck/bus, confidence 0.85-0.93). Snapshots in MinIO. Fixed ByteTrack IndexError (commit 0565647) — rebuild in progress. Backlog ~500 queued jobs draining.
- **2026-06-06 Session 5:** Unintended power outage hit all systems mid-session. GPU acceleration enabled (RTX 3060 GPU 1, CUDA). Fixed CUDA_VISIBLE_DEVICES bug (#8), supervision missing from GPU requirements (#7). 12-video smoke test completed: 95 tracks, 1310 detections. Phase 2 fully verified. GitHub issue tracking established (issues #1-12). Discord help channel set up — Claude must ping when blocked. Memory files created for project continuity. Pipeline auto-recovered via Docker restart policy. 41 of 529 jobs had completed before outage (170 tracks, 2358 detections). RabbitMQ durable queues and PostgreSQL survived intact. Two jobs left stuck in `oc_processing` (mid-flight at power loss). ByteTrack fix rebuild interrupted — needs re-run. Discord webhook integration set up for status notifications. Graceful crash/power-loss recovery added to Phase 5 scope (see below).

---

## ✅ Phase 2 COMPLETE — If Resuming

SSH access: `ssh -i ~/.ssh/claude_cowork dabeckham@192.168.55.10`

### Current State (2026-06-06 ~10:35 UTC)
All services healthy. Pipeline is live and processing real driveway camera footage.

**Container status:**
- `sentinel-orchestrator` — Up, watching /ingest, queuing jobs
- `sentinel-md-worker` — Up, processing MOG2 motion detection  
- `sentinel-oc-worker` — Up, running yolo11s.pt (CPU), classifying cars/trucks/buses
- `sentinel-rabbitmq` — Healthy
- `sentinel-postgres` — Healthy
- `sentinel-minio` — Healthy

**Backlog:** ~500 queued jobs still draining (ingest directory had many test files).

**GPU acceleration:** Deploy with `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` — targets GPU 1 (RTX 3060, 12GB). CUDA_VISIBLE_DEVICES=0 inside container (Docker remaps physical GPU 1 → container GPU 0).

**Verified data in PostgreSQL:**
- Cars (confidence 0.93), Trucks (0.88), Buses (0.52) detected in real footage
- Tracks and Detections tables populated
- Snapshot paths in MinIO snapshots bucket

### NEXT SESSION — START HERE: GPU 1 Acceleration

**Priority #1 before anything else.**

CPU mode demonstrated a 962-frame job taking ~24 minutes. GPU 1 (RTX 3060 12GB, idle) will do the same job in ~30 seconds.

#### What already exists (Phase 1):
- `docker-compose.gpu.yml` — Compose override, sets `NVIDIA_VISIBLE_DEVICES=1`, uses gpu Dockerfile
- `oc-worker/Dockerfile.gpu` — `nvidia/cuda:12.4.1-runtime` base
- `oc-worker/requirements.gpu.txt` — torch cu124 + tensorrt 10.4

#### What needs to happen:
1. Build the GPU image: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build --no-cache oc-worker`
2. Redeploy with GPU override: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --force-recreate oc-worker`
3. Verify CUDA visible: `docker exec sentinel-oc-worker python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
4. Confirm GPU 1 only (not GPU 0 — that's Frigate): check `NVIDIA_VISIBLE_DEVICES=1` in container env
5. Run a test job and compare timing vs CPU baseline (~24 min → target <1 min)

#### Key risk:
- `nvidia-container-toolkit` must be installed on host — verify with `nvidia-smi` inside a test container before building
- If toolkit missing: `sudo apt install nvidia-container-toolkit && sudo systemctl restart docker`

### Phase 3 (after GPU)
Auth & REST API: JWT, roles (admin/operator/viewer), LAN trust mode, all endpoints, WebSocket for real-time job status.

---

## Phase 5 — Graceful Crash & Power-Loss Recovery (Detailed Spec)

**User requirement (2026-06-06):** Each containerized node must detect and gracefully recover from unexpected shutdowns — including mid-task crashes, container restarts, and full building power outages where all services go down simultaneously.

### Problem Statement

Current failure modes with no recovery logic:
1. **Job stuck in `oc_processing`** — oc-worker acks the message, updates job status to `oc_processing`, then dies mid-frame. On restart, the message is gone from RabbitMQ (already acked). Job never completes. Currently 2 jobs in this state from the 2026-06-06 outage.
2. **Job stuck in `queued` after md-worker crash** — md-worker pulls from `ingest`, marks nothing in DB (status stays `queued`), dies mid-video. Message is nacked on reconnect (pika closes channel), message requeues — this one *already works* because md-worker uses `basic_ack` only on completion.
3. **Orphaned motion_results frames** — if oc-worker crashes mid-job, the remaining unprocessed motion_results for that job are still in the queue and will be processed after restart. But the ByteTrack state (in-memory, keyed by job_id) is lost — tracker resets mid-stream, causing track_id discontinuities.
4. **Orchestrator result_consumer crash** — oc_results messages acked before DB write fails → detection lost silently.
5. **Full power outage** — all of the above simultaneously, plus RabbitMQ needs to confirm all queues+messages are durable (currently declared durable ✅).

### Recovery Design (to implement in Phase 5)

#### A. Orchestrator — Startup Recovery Sweep
On `lifespan` startup (before serving requests), run a DB query:
```python
# Find jobs stuck in non-terminal states for > N minutes
stale = session.query(Job).filter(
    Job.status.in_(["oc_processing", "md_processing"]),
    Job.updated_at < datetime.utcnow() - timedelta(minutes=10)
).all()
for job in stale:
    job.status = "queued"  # reset to queued
    # re-publish to ingest queue so md-worker re-processes
    publisher.publish("ingest", {"job_id": job.id, "video_path": job.file_path, ...})
```
This covers the "full outage" case — on restart, orchestrator automatically re-queues any job that never finished.

#### B. MD Worker — Idempotent Processing
MD worker should set `job.status = "md_processing"` when it starts a job (currently not written). On crash+restart, RabbitMQ requeues (already works because pika nacks on channel close). But if orchestrator's recovery sweep fires first, the job gets re-queued redundantly. Fix: md-worker should check `job.status != "queued"` before processing and nack+discard duplicates.

#### C. OC Worker — Pre-ack Checkpoint + Restart Recovery
Two options:
1. **Option 1 (simpler): Nack on startup for stale oc_processing jobs** — oc-worker startup queries DB for jobs in `oc_processing` and resets them to `queued` so orchestrator's sweep re-queues them. Lost frames go to DLX but job re-runs cleanly.
2. **Option 2 (complex): At-least-once with dedup** — delay ack until after DB write + MinIO upload. Requires idempotent DB upserts (already done for tracks) and MinIO put-if-not-exists. This is the correct long-term solution.

**Recommendation:** Implement Option 2 for oc-worker (ack after write), Option 1 for orchestrator sweep.

#### D. Result Consumer (Orchestrator) — Ack After Write
Currently `ch.basic_ack()` is called before the DB session commits. Swap order: commit first, ack after. If commit fails, message stays in queue and retries.

#### E. RabbitMQ Queue Durability — Already Correct ✅
All queues declared `durable=True` in `infra/rabbitmq/definitions.json`. Messages survive RabbitMQ restart. No change needed.

#### F. Health-check Liveness vs Readiness
Add a `/api/ready` endpoint that returns 503 until the startup recovery sweep completes. Docker healthcheck uses `/api/health` (liveness); load balancer uses `/api/ready` (readiness). Prevents jobs being sent to a node that's still recovering.

### Immediate Fix Needed (Before Phase 5)
Re-run ByteTrack fix build:
```bash
cd ~/sentinel-pipeline && docker compose build --no-cache oc-worker && docker compose up -d --force-recreate oc-worker
```
Manually reset the 2 stuck jobs:
```sql
UPDATE jobs SET status='queued' WHERE status='oc_processing';
-- Then re-publish those job_ids to the ingest queue via orchestrator admin endpoint (Phase 3) or manual rabbitmqadmin publish
```

---

---

## Docker Host Info
- IP: 192.168.55.10, user: dabeckham
- SSH key already set up from Docker host to GitHub (ed25519)
- Repo cloned at: `~/sentinel-pipeline`
- `.env` file already configured with real secrets (do not overwrite)
- NAS mounted at: `/mnt/ds-one/sentinel-ingest` (NFS from 192.168.55.55)
- Existing containers NOT part of this project: frigate, ollama, nginx-proxy (do not touch)
- GPU 1 is target for OC workers (GPU 0 used by Frigate)

---
