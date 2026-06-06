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
**Docker host:** 192.168.55.10 (user: dabeckham)  
**SSH:** `ssh -i ~/.ssh/claude_cowork dabeckham@192.168.55.10`

### What This System Does
Distributed, containerized video analysis pipeline. Cameras FTP motion-triggered video clips to a NAS. The system ingests them, detects motion (MOG2), classifies and tracks objects (YOLO11 + ByteTrack), stores metadata + snapshots, and exposes a browser UI for review.

---

## Current Status — ALL 5 PHASES COMPLETE ✅

**Orchestrator version: 0.5.0**  
**Last commit: `e45f37f`** — nginx DNS fix (2026-06-06)  
**All 12 GitHub issues closed.**

### Live Services
| Service | URL | Status |
|---|---|---|
| Browser UI | http://192.168.55.10:3000 | ✅ Up |
| Orchestrator API | http://192.168.55.10:8000 | ✅ Up (v0.5.0) |
| API Docs | http://192.168.55.10:8000/docs | ✅ Up |
| RabbitMQ Mgmt | http://192.168.55.10:15672 | ✅ Up |
| MinIO Console | http://192.168.55.10:9001 | ✅ Up |

### Pipeline Stats (as of 2026-06-06 ~21:00 UTC)
- 78 total jobs, 78 completed
- 378 tracks, 5281 detections
- Class breakdown: car (192), truck (73), bus (23), train (21), parking meter (18), person (13), boat (13), plus misc false positives

---

## Phase Summary

| Phase | Feature | Version | Status |
|---|---|---|---|
| 1 | Infrastructure skeleton (Docker, DB, queues, MinIO) | v0.1.0 | ✅ Complete |
| 2 | Core pipeline (FTP→MD→OC→DB) + GPU (RTX 3060) | v0.2.0 | ✅ Complete |
| 3 | Auth & REST API (JWT, RBAC, WebSocket) | v0.3.0 | ✅ Complete |
| 4 | Browser UI (React 18 + Vite + TailwindCSS) | v0.4.0 | ✅ Complete |
| 5 | Crash/power-loss recovery | v0.5.0 | ✅ Complete |
| 6 | RTSP live streams | — | 🔲 Future |

---

## Architecture

**Queue flow:** `ingest → motion_results → oc_results → DB`  
Each queue has a DLX dead-letter exchange (`dlx.ingest`, `dlx.motion_results`, `dlx.oc_results`).

**Tech stack:**
| Layer | Technology |
|---|---|
| Orchestrator | Python 3.12, FastAPI v0.5.0 |
| MD Workers | Python 3.10, OpenCV MOG2 |
| OC Workers | Python 3.10, YOLO11s, ByteTrack (supervision 0.22.0), RTX 3060 GPU 1 |
| Auth | JWT (python-jose), bcrypt 3.2.2 (pinned), RBAC: admin/operator/viewer |
| UI | React 18, Vite, TailwindCSS, nginx reverse proxy |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic |
| Object Storage | MinIO |
| Broker | RabbitMQ (pika, durable queues, DLX) |

---

## Starting the Stack

```bash
# ALWAYS use both compose files together for GPU mode
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# Restart a single service
docker compose restart orchestrator
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker
```

> ⚠️ **Critical:** oc-worker MUST be started with both compose files.  
> Running `docker-compose.gpu.yml` alone = wrong Docker network (DNS fails) + missing env vars (RabbitMQ auth fails).

---

## Key Credentials & Access

- **UI login:** admin / (set in session — check credentials_access.md in memory files)
- **API auth:** POST /api/auth/login → JWT bearer token
- **GitHub PAT:** see credentials_access.md (expires 2026-12-31)
- **Discord webhooks:** see credentials_access.md

---

## API Endpoints (v0.5.0)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | /api/auth/login | none | Get JWT token |
| GET | /api/health | none | Version + liveness |
| GET | /api/stats | any | Job/track/detection counts + class breakdown |
| GET | /api/jobs | any | Paginated job list (filter by status) |
| GET | /api/jobs/{id} | any | Single job |
| GET | /api/jobs/{id}/tracks | any | Tracks for a job |
| GET | /api/tracks | any | Paginated tracks (filter by class_label) |
| GET/PUT | /api/config | admin | Runtime config (LAN trust toggle etc) |
| GET/POST/PATCH/DELETE | /api/users | admin | User CRUD |
| GET | /api/dlx/counts | admin | DLX queue depths |
| POST | /api/dlx/requeue | admin | Move DLX messages back to source queue |
| WS | /ws/jobs | any | Live job status events |

---

## Phase 5 — What Was Built

### Startup Recovery (orchestrator startup, before watcher starts)
1. **`recover_stuck_jobs()`** — finds jobs in `queued`/`md_processing`/`oc_processing`, resets to `queued`, re-publishes to ingest queue. Handles power outage and worker crashes transparently.
2. **`scan_ingest_missed()`** — walks `/ingest`, SHA-256 hashes each file, creates a job for any not in DB. Handles files that arrived while orchestrator was down.

### Graceful SIGTERM (workers)
Both md-worker and oc-worker install SIGTERM + SIGINT handlers: call `ch.stop_consuming()`, finish current message, close connection, exit. Docker `stop` now drains cleanly instead of SIGKILL mid-frame.

### DLX Requeue API
- `GET /api/dlx/counts` — message counts for all three DLX queues
- `POST /api/dlx/requeue?queue=dlx.ingest&limit=100` — moves messages back to source queue, strips x-death headers

---

## Known Gotchas (lessons learned the hard way)

1. **oc-worker must use both compose files** — standalone GPU compose joins wrong network and loses env vars
2. **bcrypt pinned to 3.2.2** — passlib 1.7.4 incompatible with bcrypt 4.x (removed `__about__`)
3. **Dockerfile.gpu uses ubuntu22.04 + python3.10** — deadsnakes PPA unreachable from build host; ubuntu24.04 CUDA image conflicts with `torch+cu124`; tensorrt removed (not needed for PyTorch inference)
4. **NFS + inotify = silent failure** — always use PollingObserver
5. **CUDA_VISIBLE_DEVICES must be "0" inside container** — Docker remaps physical GPU 1 → container GPU 0
6. **nginx proxy_pass + variable = path doubling** — `proxy_pass $var/api/` doubles the path; use `proxy_pass http://$var:8000;` (no path) + `resolver 127.0.0.11 valid=10s` for dynamic DNS
7. **RabbitMQ first deploy** — `RABBITMQ_DEFAULT_USER/PASS` requires manual `rabbitmqctl` user creation on first start
8. **Admin seeding** — only runs if users table is empty; safe to restart repeatedly

---

## Session History

| Date | Session | Summary |
|---|---|---|
| 2026-06-05 | 1 | Architecture spec, design decisions, GitHub setup, README, all docs |
| 2026-06-05 | 1 cont | Phase 1 complete — infrastructure skeleton built |
| 2026-06-06 | 2 | Phase 1 deployed. Fixed .gitignore models/ scope, PYTHONPATH. Stack verified on host |
| 2026-06-06 | 3 | Phase 2 code written + deployed. Fixed: inotify/NFS, env var collision, model name, RabbitMQ users. Pipeline live end-to-end |
| 2026-06-06 | 4 | Phase 2 verified on real driveway footage. ByteTrack IndexError fixed. GPU acceleration enabled (6x speedup) |
| 2026-06-06 | 5 | Power outage recovery. 12-video smoke test (95 tracks, 1310 detections). GitHub issues #1-12 created. Discord webhooks set up. Memory files created |
| 2026-06-06 | 6 | Phase 3 (Auth/API) complete. Fixed bcrypt/passlib. Admin seeded. All endpoints verified. Phase 4 (UI) built and deployed. Phase 5 (recovery) built and deployed. All 12 issues closed. Deployment + DR docs updated. Fixed nginx 502 (stale DNS + path doubling) |

---

## What's Next (Phase 6+)

No immediate tasks. Possible future work:
- **Phase 6:** RTSP live stream worker pool
- **Cron backup:** Set up PostgreSQL daily backup and MinIO nightly mirror (see DR doc checklist)
- **LAN trust mode:** Currently `false` in config — can enable via `PUT /api/config {"lan_trust_enabled": "true"}` to skip JWT from 192.168.x.x
- **False positive tuning:** YOLO11s is detecting parking meters, trains, zebras in driveway footage — consider confidence threshold tuning or model upgrade
- **Dashboard UX:** Class breakdown shows false positives prominently — could add a filter or confidence threshold slider
