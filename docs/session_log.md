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
Distributed, containerized video analysis pipeline. Cameras FTP motion-triggered video clips to a NAS. The system ingests them, runs Frigate-style motion detection to find motion frames, then OC workers open the video file directly, run TRT FP16 inference + ByteTrack end-to-end, and store metadata + snapshots. A React browser UI handles job review, track browsing, system metrics, and worker management.

---

## Current Status — ALL 5 PHASES COMPLETE ✅ + v0.6.0

**Orchestrator version: 0.6.0**  
**Active branch: `main`** (last commit `acccd5c` — DB-backed ingest switch + OC upload-wait, session 16/17 — 2026-06-16)  
**Alembic migration head: `0010`**  
**Workers are now managed by the NODE-AGENT (not static compose).** It governs them
on local load — currently ~2 MD + 1 OC, load ~5. Do NOT `docker compose up -d`
(would start the retired static worker services). Ingestion is ON (DB-pinned).
Backlog held: ~5,309 jobs `paused`. ~6,700+ `completed`.

### Live Services
| Service | URL | Status |
|---|---|---|
| Browser UI | http://192.168.55.10:3000 | ✅ Up |
| Orchestrator API | http://192.168.55.10:8000 | ✅ Up (v0.6.0) |
| API Docs | http://192.168.55.10:8000/docs | ✅ Up |
| RabbitMQ Mgmt | http://192.168.55.10:15672 | ✅ Up |
| MinIO Console | http://192.168.55.10:9001 | ✅ Up |

---

## Current Tech Stack

| Layer | Technology |
|---|---|
| Orchestrator | Python 3.12, FastAPI |
| MD Workers | Python 3.10, OpenCV — Frigate-style weighted-average motion detection |
| OC Workers | Python 3.10, TRT FP16 (`yolo11s.engine`) + ByteTrack (supervision 0.22.0), 4× GPU |
| Auth | JWT (python-jose), bcrypt 3.2.2 (pinned), RBAC: admin/operator/viewer |
| UI | React 18, Vite, TailwindCSS, nginx reverse proxy |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic (head: 0010) |
| Node-agent | Python — per-machine self-governor (`node-agent/`); probes load, scales MD/OC workers; self-generated persistent agent_id |
| Object Storage | MinIO |
| Broker | RabbitMQ (pika, durable queues, DLX) |

---

## Pipeline Flow (post session 15)

```
Video file → Orchestrator watcher → [ingest queue]
  → MD Worker: Frigate-style motion detection (weighted average background)
               Identifies motion frame indices
               Sends ONE job descriptor to [motion_results queue]:
               { job_id, video_path, motion_frames:[12,13,14,...], video_fps, osd_camera_name, osd_recorded_at }
  → OC Worker: Opens video file directly from NAS mount
               Seeks to each motion frame index
               TRT FP16 inference (yolo11s.engine, ~42fps, ~13MB VRAM/worker)
               ByteTrack cross-frame tracking (supervision)
               Saves _best.jpg thumbnail (most vertically-centered detection frame)
               Sends all detections bundled in one message to [oc_results queue]
  → Orchestrator result_consumer: writes Job/Track/Detection rows, broadcasts WebSocket events
```

**Key design:** MD sends one message per job — no frames in the queue, no ByteTrack state split across workers. One OC worker owns one job start to finish.

---

## Starting the Stack

```bash
# GPU mode — automatic (docker-compose.override.yml auto-merged)
docker compose up -d

# Rebuild OC workers (one image, four containers)
docker compose build oc-worker --build-arg CACHEBUST=$(date +%s)
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4

# Rebuild orchestrator
docker compose build orchestrator && docker compose up -d orchestrator

# Rebuild MD worker
docker compose build md-worker && docker compose up -d md-worker
```

> ✅ **No `-f` flags needed.** `docker-compose.override.yml` is auto-merged by Docker Compose.  
> **One image for all 4 OC workers** — build `oc-worker`, all four update simultaneously.

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

## Alembic Migrations

| Revision | Description |
|---|---|
| 0001 | Initial schema (jobs, tracks, detections, users) |
| 0002 | OSD metadata (camera_name, recorded_at, started_at, ended_at on tracks/jobs) |
| 0003 | snapshot_bbox JSON column on tracks (best-shot frame bbox for UI overlay) |
| 0004 | track_type String(16) column + index on tracks (moving/stationary classification) |
| 0005 | md_complete enum value added to JobStatus; md_started_at, md_completed_at, oc_started_at on jobs |
| 0006 | md_worker_id, oc_worker_id columns on jobs |
| 0007 | `paused` value added to jobstatus enum |
| 0008 | pipeline_settings key-value table for persistent orchestrator state |
| 0009 | `jobs.file_hash` UNIQUE — race-safe ingest dedup |
| 0010 | `jobs.snapshot_path` (one representative image per clip) + `jobs.source_deleted` |

---

## API Endpoints (v0.6.0)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | /api/auth/login | none | Get JWT token |
| GET | /api/health | none | Version + liveness |
| GET | /api/stats | viewer | Job/track/detection counts + class breakdown |
| GET | /api/jobs | viewer | Paginated job list (filter by status, infinite scroll) |
| GET | /api/jobs/{id} | viewer | Single job with stage timestamps |
| POST | /api/jobs/{id}/cancel | operator | Mark active job as failed |
| POST | /api/jobs/{id}/pause | operator | Pause a queued job |
| POST | /api/jobs/{id}/resume | operator | Resume a paused job |
| POST | /api/jobs/bulk/pause | operator | Pause multiple jobs by ID list |
| POST | /api/jobs/bulk/kill | operator | Kill multiple active jobs |
| DELETE | /api/jobs/{id} | admin | Remove a job record |
| GET | /api/tracks | viewer | Paginated tracks (filter by camera/class/track_type/date) |
| GET | /api/tracks/cameras | viewer | Distinct camera names |
| GET | /api/tracks/active-days | viewer | Dates with track data for calendar dots |
| GET | /api/tracks/{id} | viewer | Track detail with full detections list |
| GET | /api/snapshots/{path} | viewer | Proxy MinIO snapshot images |
| POST | /api/snapshots/cleanup | admin | Delete `_f{frame}.jpg` frames (keep `_best.jpg`) |
| GET | /api/metrics/stream | viewer | SSE — CPU/RAM/disk/GPU stats every 2s |
| GET/PUT | /api/config | admin | Runtime config |
| GET/POST/PATCH/DELETE | /api/users | admin | User CRUD |
| GET | /api/dlx/counts | admin | DLX queue depths |
| POST | /api/dlx/requeue | admin | Move DLX messages back to source queue |
| GET | /api/workers | viewer | All known workers with status + stats |
| POST | /api/workers/{id}/suspend | operator | Suspend a worker (nack+requeue on next job) |
| POST | /api/workers/{id}/resume | operator | Resume a suspended worker |
| GET | /api/internal/workers/{id}/status | internal | Worker suspension poll endpoint |
| WS | /ws/jobs | viewer | Live job status events |

---

## Key Credentials & Access

- **UI login:** admin / (check credentials_access.md in memory files)
- **API auth:** POST /api/auth/login → JWT bearer token
- **GitHub PAT:** see credentials_access.md (expires 2026-12-31)
- **Discord webhooks:** see credentials_access.md. **Any code posting to a
  Discord webhook MUST set a non-default `User-Agent` header** — Discord/
  Cloudflare `403`s the default `Python-urllib/x.y` UA (curl's UA is allowed,
  so curl tests pass and mask it). See "Known Gotchas" below.

---

## Known Gotchas (session 15 edition)

1. **SSH username is `dabeckham`** — not `don`. Always: `ssh -i ~/.ssh/claude_cowork dabeckham@192.168.55.10`
2. **`docker compose up -d` starts everything** — `docker-compose.override.yml` is auto-merged; no `-f` flags needed
3. **One image for all 4 OC workers** — build `oc-worker`; workers 2/3/4 use `image: sentinel-oc-worker-gpu:latest`, never have their own `build:` block
4. **bcrypt pinned to 3.2.2** — passlib 1.7.4 incompatible with bcrypt 4.x
5. **Dockerfile.gpu uses ubuntu22.04 + python3.10** — deadsnakes PPA unreachable from build host
6. **NFS + inotify = silent failure** — always use PollingObserver
7. **CUDA_VISIBLE_DEVICES must be "0" inside container** — Docker remaps physical GPU → container GPU 0
8. **TRT engine warmup required** — `YOLO(engine_path, task="detect")` leaves `.names` empty until first `predict()`. Worker runs dummy `predict(np.zeros(...))` immediately after load.
9. **RabbitMQ mnesia wipe** — `change_password` silently does nothing if user doesn't exist. Use `add_user` + `set_user_tags` + `set_permissions`. See disaster_recovery.md Scenario 8.
10. **Worker registry is in-memory** — orchestrator restart wipes it. Self-healing: heartbeats carry type+device and bootstrap unknown workers; workers re-announce on 404 from status poll.
11. **Discord webhook needs a real `User-Agent`** — Discord fronts its API with Cloudflare, which `403 Forbidden`s the default `Python-urllib/x.y` UA. `curl` sends an allowed UA, so curl/shell tests return `204` and hide the bug, while the app silently fails (worse if the post is fire-and-forget under a bare `except`). Always set e.g. `User-Agent: sentinel/1.0 (+...)` in the request headers. Cross-network requirement (discovered in xlnn webui 2026-06-13). Never `except: pass` a webhook post during bring-up — log the status/exception at least once.
11. **git filter-repo removes the remote** — after purging history, must `git remote add origin` and `git push --set-upstream origin main`
12. **`SessionLocal` is `autoflush=False`** — any function that re-queries rows added earlier in the same transaction must `db.flush()` first. This bit track classification hard (session 16): `_classify_tracks` queried `Detection` before the pending rows were flushed, saw nothing, and labeled ~94% of tracks `stationary`. Fixed with a `db.flush()` before `_classify_tracks`.
13. **Startup ingest scan is non-blocking (session 16)** — `scan_ingest_missed()` runs in a daemon thread spawned by `resume_watcher()`, so the lifespan yields immediately and uvicorn serves in ~13s instead of blocking for minutes while it SHA-256-hashes every clip over NFS. The observer starts first, so new arrivals are handled live; the scan only backfills pre-existing files. Dedup is race-safe via the `jobs.file_hash` UNIQUE constraint (migration 0009) — both insert paths catch `IntegrityError` and skip, and the scan commits per file *before* publishing so a race never orphans a queue message. If the pipeline is already backed up at startup, `startup_health_check()` pauses the watcher and the scan is deferred until the health monitor calls `resume_watcher()` — expected behavior.
14. **Workers are node-agent-managed now (session 17)** — the agent (`node-agent/`, container `sentinel-node-agent`) starts/stops worker containers (labels `sentinel.managed=true`, names `sentinel-{oc,md}-managed-N`) based on local load. The static `oc-worker*`/`md-worker` compose services are RETIRED — **do NOT `docker compose up -d`** (it would start them alongside the agent's). After rebuilding a worker image, recycle the agent's worker (`docker rm -f <name>`; the agent respawns it on `:latest`) — agent doesn't hot-update images yet. Agent state (agent_id) persists in the `node-agent-state` volume.
15. **`compose up` reverts env-var overrides → use DB for switches (session 17)** — `docker compose up -d <svc>` reconciles a service's *dependencies* too, and any `${VAR:-default}` env not set in that invocation reverts to default. This silently re-enabled ingestion (`docker compose up -d ui` recreated the orchestrator without `INGEST_WATCH_ENABLED=false`). Persistent toggles now live in the `pipeline_settings` DB table (`pipeline_settings.get_bool/set_bool`), read at runtime. Ingest switch: `GET/POST /api/pipeline/ingest`.
16. **Clips are H.265/HEVC ~11MP (4512×2512)** — Chrome/Edge play HEVC (with OS codec); Firefox/others don't. The video endpoint serves the raw file; non-HEVC browsers need an on-demand H.264 transcode (deferred). Also: OC now **waits for snapshot uploads to land before publishing the final "done"** message (was fire-and-forget → killed/recycled workers lost snapshots). Until done is published+acked the job redelivers — no silent snapshot loss.

---

## Snapshot Storage Layout (MinIO `snapshots` bucket)

| Path | Purpose |
|---|---|
| `{job_id}/track_{track_id:06d}_best.jpg` | Best-shot thumbnail. Overwritten when better frame found. |
| `{job_id}/track_{track_id:06d}_f{frame_index:06d}.jpg` | Per-detection full frame for playback. Clean up with `/api/snapshots/cleanup`. |

`tracks.snapshot_bbox` stores `{x,y,w,h}` from the best-shot frame for the UI's BboxOverlay component.

---

## Worker Panel

Workers self-register via lifecycle events on the `oc_results` queue:
- **`online`** — sent on startup (includes worker_type + device)
- **`heartbeat`** — every 15s (includes worker_type + device)  
- **`offline`** — sent on SIGTERM

The UI worker panel shows labels like `MD-CPU-1`, `OC-GPU-2`, status dots (green=idle, yellow=processing, red=suspended), and a hover stats callout.

**Self-healing:** If the orchestrator restarts and loses the in-memory registry, workers automatically re-register within one heartbeat cycle (~15s) via two mechanisms:
1. Heartbeats include type+device — registry bootstraps unknown workers from heartbeats
2. Workers detect 404 on status poll → re-publish `online` event for full re-registration

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
| 2026-06-07 | 8 | Tracked Objects UI polish. Fixed snapshot images not loading (JWT auth — `<img>` can't send headers; switched to fetch+blob URL). Fixed snapshot not filling tile. Converted side drawer to centered floating modal. Per-detection snapshot storage in oc-worker. Frame-by-frame playback in modal: play/pause, step back/forward, scrubber, frame counter overlay, clickable detection list rows. |
| 2026-06-08 | 9 | Fixed oc-worker crash loop (setproctitle missing from GPU image). Unstuck job #12. MOG2 tuning. Added yolo-models named volume. Fixed snapshot playback. Switched to full original frame snapshots. Issues #18–21 created and closed. |
| 2026-06-07 | 10 | Jobs page real-time updates: WebSocket + 10s polling fallback, elapsed status timer, Live/Polling indicator. Fixed asyncio broadcast bug (event_loop.py). Fixed pipeline order (YOLO → ByteTrack, not backwards). Fixed positional index mapping. ByteTrack match_threshold 0.8→0.3. |
| 2026-06-07 | 11 | Major pipeline rework: filename parsing replaces OCR; BoT-SORT on full frames; best-shot thumbnail; md_processing status ping; Jobs page adaptive polling 2s/8s. Issues #26–#33 fixed. |
| 2026-06-07 | 12 | Replaced MOG2 with Frigate's weighted-average motion detector. Replaced BoT-SORT with Norfair 2.3.0. Fixed YOLO confidence 0.85→0.5. Hit_counter_max 8→30. Added POST /api/snapshots/cleanup. ARG CACHEBUST in Dockerfile.gpu. Discord status notifications. |
| 2026-06-07 | 13 | snapshot_bbox column (migration 0003). BboxOverlay SVG component in UI. Deployed. SSH permission granted. |
| 2026-06-07 | 14 | v0.6.0. Track classification (moving/stationary, migration 0004). Tracks page overhaul: infinite scroll, multi-select filters, date range with calendar. Jobs page: resizable columns, filename, track_count, Kill button, stage timeline hover. MetricsBar SSE. Second OC worker on GPU 0. Migration 0005 (stage timestamps). |
| 2026-06-08–09 | 15 | **TRT FP16 + ByteTrack + job-descriptor architecture.** MD sends one job descriptor per job (not per-frame). OC opens video directly, TRT FP16 ~42fps. Replaced Norfair with ByteTrack (supervision). 4 OC workers (GPU 1: 1,3,4; GPU 0: 2). Single shared Docker image (`sentinel-oc-worker-gpu:latest`) — no more version skew. Renamed `docker-compose.gpu.yml` → `docker-compose.override.yml` (auto-merged, no `-f` needed). Worker lifecycle events (online/offline/heartbeat). Worker panel with labels, status dots, suspend/resume, stats callout. Bulk job pause/kill (migrations 0006, 0007). Pipeline settings table (migration 0008). Self-healing worker registry (heartbeat bootstraps, 404 re-announce). Security: `.env.backup.with.keys` purged from git history, credentials rotated. RabbitMQ mnesia recovery procedure documented. Issues #40–#49 created and closed. |
| 2026-06-09 | 16 | **Moving/stationary classification fix.** Diagnosed that ~94% of tracks were labeled `stationary` (cars sweeping across the whole frame included). Root cause: `SessionLocal` is `autoflush=False`, so `_classify_tracks` queried `Detection` before the pending rows were flushed and saw an empty set → every track fell into the `stationary` default. Fixed with a one-line `db.flush()` before `_classify_tracks`. `scripts/backfill_classify.py` re-classified all 6,087 completed jobs from committed data (moving 2,420 → 4,578). Deferred follow-up: first-to-last metric still misses loiter-and-return + ID-switch merges (path-span metric is a trade-off). Issue #50 created and closed. Commit `9214708`. |
| 2026-06-09 | 16 | **Non-blocking startup.** The lifespan ran `scan_ingest_missed()` synchronously before `yield`, so uvicorn didn't serve until every clip was SHA-256-hashed over NFS (minutes of downtime per restart). Moved the scan to a daemon thread → API serves in ~13s. Made ingest dedup race-safe for the now-fully-concurrent watcher+scan: migration 0009 (`jobs.file_hash` UNIQUE, 0 dups), `IntegrityError` handling in both insert paths, scan commits per file before publishing (no orphaned queue msgs). `recover_stuck_jobs` stays synchronous (DB-only). Issue #51 created and closed. Commit `dbbfab1`. |
| 2026-06-16 | 17 | **Distributed-worker Phase 1 + storage/lifecycle + playback.** (1) Design doc `docs/distributed_workers_design.md` + epic **#52**: broker→agent→worker hierarchy, autonomous nodes, code-vs-protocol versioning. (2) **Node-agent built & LIVE** (`node-agent/`): probes CPU/RAM/swap(rate)/GPU, shared core-pool budget (reserve for Frigate), demand from queue depth, pure `policy.decide()` (8 tests, no-oversubscribe guard), Docker supervisor, governor loop. **Load 48→5; no more thrash.** Self-generated persistent agent_id; workers report it + code_version(git SHA)+protocol_version(semver). Static compose workers RETIRED — agent owns them. (3) Investigated the "5000-file backlog" → was 5,481 **duplicate** jobs (pre-0009); deleted them, all footage already processed. (4) **Slice 1** processed_ lifecycle: orchestrator renames clip→`processed_<name>` on completion + updates file_path; scan skips processed_; `/ingest` mount ro→rw. (5) **Slice 2** (migration 0010): one snapshot per clip — forced scene keyframe for empty clips, collapse no-motion clips to one best-shot (delete redundant). (6) **Slice 3**: `GET /api/jobs/{id}/video` serves clip from NAS; Tracks modal plays real video (fetch→blob), bbox on/off toggle, download button. Clips are **H.265 11MP** — Chrome/Edge play it, others need transcode (deferred). (7) Fixes: "In status" timer (anchor to stage timestamp not created_at); **DB-backed ingest switch** (`pipeline_settings`, survives compose up — env var was the footgun that silently re-enabled ingestion); **OC waits for snapshot uploads before publishing done** (killed/recycled workers no longer lose snapshots — ~6,537 historical gaps accepted). Last commit `acccd5c`. |

---

## What's Next (open items as of session 17)

**Distributed-worker follow-ups (epic #52):**
- **UI: show worker version + agent_id** in the worker panel (data is on `/api/workers`, not displayed yet) + a "stale" badge; then **compatibility gating** (accept/suspend on protocol MAJOR mismatch) and **enrollment auth** (Phase 2). Agent self-generates id, accepted at enrollment gated by auth.
- **Capture the identity/versioning design** in `docs/distributed_workers_design.md` (broker→agent→worker hierarchy; code_version=git SHA observability vs protocol_version=semver gated on MAJOR for mixed-version canary). *Owed — not yet written.*
- Phases 2–4: node enrollment + overlay transport; networked data plane (clips over object store vs NAS mount); installer.

**Slice / UI work:**
- **NEW — Job Details page:** make each row on the Jobs page clickable → a per-job details page with as much info as possible, incl. **workflow details (who/what/when — md_worker_id, oc_worker_id, the stage timestamps, etc.)** and **track cards like the Tracks page but filtered to that job**.
- **Slice 4 — purge-no-motion-source UI:** delete `processed_*` source videos for no-motion clips (keep snapshot + DB row); set `jobs.source_deleted=true`.
- **bbox overlay on the *moving* video** (slice 3 follow-up): needs `video_fps` stored + SVG synced to `currentTime`. Currently the bbox toggle only gates the still overlay.
- **Video transcode** for non-HEVC browsers (clips are H.265 11MP): on-demand H.264 transcode + cache, CPU or GPU (NVENC). Deferred — Don's browser plays HEVC.

**Pre-existing / older:**
- **Track classification metric** (issue #50): first-to-last displacement misses loiter-and-return + ID-switch merges. Path-span metric is a trade-off (would false-positive ID-switch merges). NOTE: historical detection/classification data is **unreliable** (degraded-era processing) — a clean re-baseline is needed before camera-tuning decisions.
- **Camera over-sensitivity:** ll-driveway ~540–1000 clips/day, ~42% no-motion (figure suspect — see above); parsley-gate delivering 0 files. Consider camera motion zones/sensitivity tuning.
- **Dwell time Phase 2/3** (issue #23): similar-bbox search; dwell zones.
- **Phase 7:** RTSP live streams. **Cron backup:** PG daily + MinIO nightly.
