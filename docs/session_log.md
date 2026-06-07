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

## Current Status — ALL 5 PHASES COMPLETE ✅ + Post-Launch Tuning

**Orchestrator version: 0.5.0**  
**Last commit: `85351a8`** — Full-frame snapshots instead of crops (2026-06-08)  
**Alembic migration: 0002** (camera_name, recorded_at, started_at, ended_at columns added)  
**21 GitHub issues total. Issues #18–21 fixed session 9.**

### Live Services
| Service | URL | Status |
|---|---|---|
| Browser UI | http://192.168.55.10:3000 | ✅ Up |
| Orchestrator API | http://192.168.55.10:8000 | ✅ Up (v0.5.0) |
| API Docs | http://192.168.55.10:8000/docs | ✅ Up |
| RabbitMQ Mgmt | http://192.168.55.10:15672 | ✅ Up |
| MinIO Console | http://192.168.55.10:9001 | ✅ Up |

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
| GET | /api/tracks | any | Paginated tracks (filter by camera/class/date, sort, detection_count, started_at/ended_at) |
| GET | /api/tracks/cameras | any | Distinct camera names with associated tracks |
| GET | /api/tracks/{id} | any | Track detail with full detections list |
| GET | /api/snapshots/{path} | any | Proxy MinIO snapshot images to browser (auth required, 1-day cache) |
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
| 2026-06-07 | 7 | Post-launch tuning. Issues #13-16: in-memory crops, debug video, watcher loop fix, MOG2 scale. Docker Hub v0.5.0 push. Track fragmentation fix (bbox merging). Ghost track fix (lost_buffer 90→10). YOLO class filter (vehicles/person/animals only). Confidence 0.45→0.85. OSD OCR (pytesseract first-frame timestamp + camera name, alembic migration 0002). Frigate-style Tracked Objects UI (#17): card grid, filters, detail drawer, snapshot proxy. Fixed SSH — was using wrong username `don`, should be `dabeckham`. |
| 2026-06-07 | 8 | Tracked Objects UI polish. Fixed snapshot images not loading (JWT auth — `<img>` can't send headers; switched to fetch+blob URL). Fixed snapshot not filling tile (absolute inset-0 + fixed-height container). Converted side drawer to centered floating modal. Per-detection snapshot storage in oc-worker (`track_{id}_f{frame}.jpg`). Frame-by-frame playback in modal: play/pause, step back/forward, scrubber, frame counter overlay, clickable detection list rows. |
| 2026-06-08 | 9 | Fixed oc-worker crash loop (setproctitle missing from GPU image — must rebuild with both compose files). Unstuck job #12. MOG2 tuning: var_threshold 16→25, shadows off, merge_dist 30→60, min_contour_area 500→800. Added yolo-models named volume to cache weights. Fixed snapshot playback: SnapshotImg not resetting state on path change, crop_path missing from API response, hasCrops gate for pre-v0.5.1 tracks. Switched to full original frame snapshots (not crops) — MD passes full frame base64 alongside crops; OC saves full frame; crops used only for inference. GitHub issues #18–21 created and closed. |
| 2026-06-07 | 10 | Jobs page real-time updates: WebSocket connection + 10s polling fallback, elapsed status timer (resets on status change, hidden when complete), Live/Polling indicator. Fixed asyncio broadcast bug: `get_event_loop()` from background thread in Python 3.10+ returns dead loop — fixed with `event_loop.py` storing FastAPI's real loop at startup. Fixed pipeline order: was MOG2 blobs → ByteTrack → YOLO (backwards). Correct: YOLO detects within MOG2 crops → translate bbox to full-frame → ByteTrack. Fixed positional index mapping bug in track_frame (ByteTrack output order ≠ input order — now matched by IoU). ByteTrack: match_threshold 0.8→0.3, lost_buffer 10→60. DB and MinIO cleared for fresh start. |
| 2026-06-07 | 11 | Major pipeline rework. (1) Replaced OCR entirely with filename parsing — `CAMNAME_NN_YYYYmmddHHMMSS` → camera name + timestamp, per-frame times from FPS. (2) Switched from per-crop YOLO+ByteTrack to `model.track(full_frame, tracker="botsort.yaml")` — BoT-SORT runs on complete frames for full context + appearance ReID. reset_tracker() on is_final separates jobs cleanly. (3) Best-shot thumbnail: OC worker tracks frame where bbox vertical center is closest to frame center, saves as `_best.jpg`, overwrites when better. Per-detection `_f{frame}.jpg` kept for playback. (4) md_processing status: MD worker publishes status ping to oc_results at job start. (5) Jobs page: removed duplicate WS (Layout owns it), adaptive polling 2s/8s with loadRef/dataRef to fix stale closure. Toast only on completed/failed. (6) Fixed year missing from track dates in UI (fmtTime missing `year: 'numeric'`). Issues #26–#33 fixed. DB/store purged twice for fresh testing. |
| 2026-06-07 | 12 | Studied Frigate source in running container. Replaced MOG2 with Frigate's weighted-average motion detector (avg_frame + avg_delta temporal smoothing, background only absorbs after 10 consecutive motion frames, contrast normalization). Replaced BoT-SORT with Norfair 2.3.0 + Frigate's scale-normalized distance function (normalizes position change by object's own bbox size). Fixed Norfair: only emit tracks with live detections this frame (not Kalman-predicted positions — used frame_index tag in Detection.data). Lowered YOLO confidence 0.85→0.5, raised hit_counter_max 8→30. Fixed model weight caching: volume now at /app/models so yolo11s.pt persists across restarts. Added POST /api/snapshots/cleanup to delete _f{frame}.jpg playback frames (keep only _best.jpg). Added ARG CACHEBUST to Dockerfile.gpu for targeted cache busting. Discord/status notifications. |

---

## Post-Launch Pipeline Behaviour (session 12)

- **Motion detection:** Frigate-style weighted average. `avg_frame` = running background (frame_alpha=0.01). `avg_delta` = temporal smoothing of frame differences (delta_alpha=0.2). Threshold applied to intersection of current delta and avg_delta — single-frame noise ignored. Background only absorbs moving objects after 10 consecutive motion frames. Contrast normalization (4th-96th percentile stretch). Detection at 100px frame height (maintain aspect ratio), merge_dist=10, min_contour_area=10 (motion-frame pixels).
- **Classification + Tracking:** YOLO11s (model() detect-only, conf≥0.5) + Norfair 2.3.0 with Frigate's scale-normalized distance function (R=3.4, hit_counter_max=30). Only tracks matched to a live YOLO detection this frame are emitted — Kalman predictions don't create DB rows. Tracker reset between jobs on `is_final`.
- **Camera metadata:** Parsed from filename — `CAMNAME_NN_YYYYmmddHHMMSS` → camera name + recording start time. No OCR.
- **Track timestamps:** `tracks.started_at` / `tracks.ended_at` = `recorded_at + frame_offset_ms`
- **Debug video:** `MD_DEBUG_VIDEO=true` in compose → `_debug.mp4` written to `/ingest/debug/` at 640×360

## Known Gotchas (updated)

1. **SSH username is `dabeckham`** — not `don`. Always: `ssh -i ~/.ssh/claude_cowork dabeckham@192.168.55.10`
2. **oc-worker must use both compose files** — standalone GPU compose joins wrong network and loses env vars
3. **bcrypt pinned to 3.2.2** — passlib 1.7.4 incompatible with bcrypt 4.x
4. **Dockerfile.gpu uses ubuntu22.04 + python3.10** — deadsnakes PPA unreachable; ubuntu24.04 CUDA conflicts with torch+cu124
5. **NFS + inotify = silent failure** — always use PollingObserver
6. **CUDA_VISIBLE_DEVICES must be "0" inside container** — Docker remaps physical GPU 1 → container GPU 0
7. **nginx proxy_pass + variable = path doubling** — use `proxy_pass http://$var:8000;` (no path) + `resolver 127.0.0.11 valid=10s`
8. **NFS remounted rw** — required for md-worker debug video; docker-compose.yml has `/ingest:rw`

## Snapshot Storage Layout (MinIO `snapshots` bucket)

| Path | Purpose |
|---|---|
| `{job_id}/track_{track_id:06d}.jpg` | Track thumbnail — full original frame of first detection, used on card grid |
| `{job_id}/track_{track_id:06d}_f{frame_index:06d}.jpg` | Per-detection full frame — used for playback in modal |

**Note:** Crops are never stored. MD sends full frame base64 (JPEG q80) alongside crops in motion_results. OC uses crops only for inference; saves full frame as snapshot.

Served via `GET /api/snapshots/{path}` — orchestrator proxies from MinIO with JWT auth.  
Frontend fetches with `Authorization: Bearer` header, converts to blob URL (plain `<img src>` can't carry JWT).

## Tracked Objects UI Summary

- **Card grid** — responsive 2–6 col, thumbnail fills tile, class badge + confidence overlay, camera name, timestamp, duration, detection count, confidence bar
- **Filters** — camera (dynamic from DB), class label, sort order (newest/oldest/confidence/class)
- **Modal on click** — centered floating, blurred backdrop, Escape to close
  - 220px image viewer with per-detection crops
  - Play/pause (250ms/frame), step back/forward, scrubber, frame counter
  - Metadata grid: class, confidence, camera, detections, started, ended, duration, frames
  - Detection list — clickable rows jump to that frame, active row highlighted

## What's Next

- **Verify Norfair tracking quality** — drop fresh camera footage, confirm track count per object is 1 (or close), check DB for fragmented tracks
- **Run cleanup** — call `POST /api/snapshots/cleanup` (admin) to delete accumulated `_f{frame}.jpg` playback frames, keeping only `_best.jpg`
- **Bbox overlay on full frames** — draw bbox rectangle on snapshot thumbnails in UI (coordinates stored in DB)
- **Phase 6:** RTSP live stream worker pool
- **Cron backup:** PostgreSQL daily backup + MinIO nightly mirror

## Known Gotchas (Session 9 additions)

- **oc-worker GPU rebuild**: always use BOTH compose files: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml build oc-worker`
- **yolo-models volume**: must be created once on host before GPU stack starts: `docker volume create sentinel-pipeline_yolo-models`
- **git push**: `git push origin main` from local workspace works directly — credentials configured. No PAT in URL needed.
- **orchestrator has no volume mount** — source baked into image. `docker compose restart` alone won't pick up code changes. Must `docker compose build orchestrator && docker compose up -d orchestrator`.
