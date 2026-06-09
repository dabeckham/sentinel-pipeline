# Distributed Video Analysis System — Architecture Outline
*v0.6.0 — Updated 2026-06-09 (session 15)*

---

## 1. System Overview

A distributed, containerized pipeline that:
1. Watches a network path for FTP-uploaded, camera-triggered video files
2. Detects motion regions per frame (Frigate-style weighted-average background model)
3. Classifies and tracks objects across frames (YOLO11s TRT FP16 + ByteTrack)
4. Persists metadata to a database and frame snapshots to object storage
5. Exposes a browser-based UI for job review, worker management, and system metrics
6. Supports multi-user access with role-based auth (admin, operator, viewer)

All inter-process communication is queue-based, enabling horizontal scaling of MD and OC workers. OC workers own entire jobs from start to finish — no state is split across workers.

---

## 2. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| Q1 | Message broker | **RabbitMQ** | Durable queues, per-message ACK, dead-letter routing — essential for crash recovery |
| Q2 | MD algorithm | **Frigate-style weighted-average** | Temporal smoothing reduces false motion; background only absorbs after 10 consecutive motion frames |
| Q3 | OC model | **YOLO11s TRT FP16** | Auto-exported on first run; ~42fps on RTX 3060; 13MB VRAM per worker |
| Q4 | Object tracker | **ByteTrack (supervision 0.22.0)** | Lightweight, no re-ID needed, accurate cross-frame tracking |
| Q5 | Auth | **Multi-user RBAC** (admin / operator / viewer) | Admins can toggle LAN-trust mode on/off via UI |
| Q6 | Storage backend | **MinIO** | S3-compatible, self-hosted, Docker-native |
| Q7 | Worker transport | **Queue-pull** | Workers pull from broker; cleaner for distributed deployment |
| Q8 | Video input | **FTP file-first** | Cameras FTP motion-triggered clips; RTSP streams deferred to future |
| Q9 | MD-to-OC message | **One job descriptor per job** | No frames in queue; OC reads video directly from NAS; ByteTrack sees contiguous frame sequence |
| Q10 | Worker registry | **In-memory + self-healing** | Heartbeats carry type+device; bootstraps on orchestrator restart within 15s |

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser UI                           │
│  (Jobs | Tracks | Workers | Metrics | Users | Config)       │
└────────────────────────┬────────────────────────────────────┘
                         │ REST / WebSocket / SSE
┌────────────────────────▼────────────────────────────────────┐
│                    Orchestrator (FastAPI)                    │
│  - FTP path watcher (PollingObserver — NFS-safe)            │
│  - Job deduplication (SHA-256 hash → DB)                    │
│  - Enqueues jobs, tracks pipeline state                     │
│  - Writes OC results to DB; manages MinIO snapshots         │
│  - REST API + WebSocket + SSE metrics for UI                │
│  - In-memory worker registry (self-healing via heartbeats)  │
│  - User/role management (JWT auth)                          │
└──┬─────────────────────┬──────────────────────┬─────────────┘
   │                     │                      │
   ▼                     ▼                      ▼
RabbitMQ             MinIO                  PostgreSQL
(message broker)     (snapshot storage)     (metadata DB)
   │
   ├──► [ingest]           { job_id, video_path }  → MD Workers
   ├──► [motion_results]   { job_id, video_path,   → OC Workers
   │                          motion_frames:[...],
   │                          fps, camera, time }
   └──► [oc_results]       { detections[], stats } → Orchestrator
                           + worker lifecycle events (online/heartbeat/offline)

┌─────────────────────────────────────────────────────────────┐
│              MD Worker Pool (N containers, CPU)             │
│  - Frigate-style weighted-average motion detection          │
│  - Identifies motion frame indices                          │
│  - Sends ONE job descriptor per job (no frames in queue)    │
│  - Publishes heartbeats every 15s                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              OC Worker Pool (4 containers, GPU)             │
│  - Opens video file directly from NAS mount                 │
│  - CPU H.265 decode overlapped with TRT FP16 inference      │
│  - TRT FP16 (yolo11s.engine, auto-exported, ~42fps)         │
│  - ByteTrack cross-frame tracking (supervision 0.22.0)      │
│  - Saves _best.jpg thumbnail per track                      │
│  - Bundles all detections in one final message              │
│  - Publishes heartbeats every 15s                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Queue Design

| Queue | Producer | Consumer | Payload |
|---|---|---|---|
| `ingest` | Orchestrator | MD Workers | `{ job_id, video_path, source_type }` |
| `motion_results` | MD Workers | OC Workers | `{ job_id, video_path, motion_frames:[indices], video_fps, osd_camera_name, osd_recorded_at }` |
| `oc_results` | OC Workers | Orchestrator | `{ job_id, is_final, detections:[...], elapsed_s, fps, frames_processed }` + worker lifecycle events |
| `dlx.*` | RabbitMQ (auto) | Dead-letter handler | Failed/expired messages from any queue |

**Key change from original design:** `motion_results` carries one message per job (a lightweight descriptor with frame indices), not one message per frame. The OC worker reads the video file directly rather than receiving frames over the queue.

---

## 5. Worker Lifecycle & Self-Healing

Workers publish lifecycle events to the `oc_results` queue via a separate pika connection:

| Event | When | Payload |
|---|---|---|
| `online` | Worker startup | worker_id, worker_type, device |
| `offline` | SIGTERM received | worker_id |
| `heartbeat` | Every 15s | worker_id, worker_type, device |

**Self-healing after orchestrator restart:**
1. Heartbeats include `worker_type` + `device` — the registry bootstraps any unknown worker from its first heartbeat
2. Workers poll the orchestrator for suspension state every 15s; if they receive HTTP 404 (registry lost), they re-publish the `online` event to force full re-registration

Result: panel recovers within 15 seconds of orchestrator restart with no worker restarts.

---

## 6. Authentication & Authorization

### Roles
| Role | Capabilities |
|---|---|
| **admin** | Full access; manage users; toggle LAN-trust mode; runtime config |
| **operator** | Pause/resume/kill jobs; suspend/resume workers |
| **viewer** | Read-only access to results, jobs, and review pages |

### Auth Modes
- **Standard:** JWT-based login (username + password).
- **LAN Trust Mode:** When enabled by an admin, requests from configured IP ranges bypass credential check.

---

## 7. Database Schema

```sql
-- One row per ingested video
jobs (id, file_path, file_hash, status, created_at, completed_at,
      md_started_at, md_completed_at, oc_started_at,
      camera_name, recorded_at, md_worker_id, oc_worker_id,
      error_message)

-- Unique object track across frames
tracks (id, job_id, track_id, class_label, confidence_max,
        first_frame, last_frame, started_at, ended_at,
        snapshot_path, snapshot_bbox,   -- best-shot frame + bbox
        track_type)                     -- 'moving' | 'stationary'

-- Per-frame classification result
detections (id, track_id, job_id, frame_index, class_label,
            confidence, bbox, crop_path, created_at)

-- Users and roles
users (id, username, email, password_hash, role, created_at, last_login)

-- System configuration (key/value)
config (key, value, updated_by, updated_at)

-- Persistent orchestrator state (pipeline settings)
pipeline_settings (key, value, updated_at)
```

---

## 8. MinIO Storage Layout

```
snapshots/
  {job_id}/
    track_{id:06d}_best.jpg          Best-shot thumbnail (most centered frame)
    track_{id:06d}_f{frame:06d}.jpg  Per-detection full frame (playback; clean up with API)
```

`track_best.jpg` is the only permanent thumbnail. `_f{frame}.jpg` files accumulate and should be periodically cleared via `POST /api/snapshots/cleanup`.

`tracks.snapshot_bbox` stores `{x, y, w, h}` from the best-shot frame so the UI's `BboxOverlay` component can draw the detection rectangle accurately.

---

## 9. OC Worker Pipeline Detail

```
Job descriptor arrives from motion_results queue
│
├─ Open video file via OpenCV (CPU H.265 decode, NFS mount)
│
├─ For each motion frame index:
│   ├─ Decode frame
│   ├─ TRT FP16 inference (yolo11s.engine)
│   │   └─ class filter: person/car/truck/bus/motorcycle/bicycle/dog/cat/bird
│   ├─ ByteTrack: match detections to tracks
│   └─ Find best-shot frame (bbox vertical center closest to frame center)
│
├─ Save _best.jpg to MinIO for each track
│
└─ Publish final message: all detections bundled, elapsed_s, fps, frames_processed
```

**Decode + inference overlap:** CPU H.265 decode (~47fps) and TRT inference (~46fps) run on overlapped threads. TRT releases the GIL during its C++ kernel, so CPU decode runs in parallel — net throughput ≈ min(decode, inference) ≈ 42fps end-to-end.

**NVDEC rejected:** File-based NVDEC via subprocess pipe is 18% slower than OpenCV due to pipe overhead. Running `scale_cuda` alongside TRT causes GPU context thrashing (5fps end-to-end). See `docs/decode_inference_research.md` for full benchmark results.

---

## 10. Technology Stack

| Layer | Technology |
|---|---|
| Orchestrator API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic (head: 0008) |
| MD Workers | Python 3.10, OpenCV (Frigate-style weighted-average motion) |
| OC Workers | Python 3.10, Ultralytics YOLO11s TRT FP16, ByteTrack (supervision 0.22.0) |
| Message Broker | RabbitMQ 3.x (pika client) |
| Database | PostgreSQL 16 |
| Object Storage | MinIO |
| UI | React 18, Vite, TailwindCSS |
| Auth | JWT (python-jose), bcrypt 3.2.2 (pinned) |
| Container Runtime | Docker, Docker Compose |
| GPU Workers | ubuntu22.04 + python3.10 + CUDA 12.x, RTX 3060 |

---

## 11. Build Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Infrastructure skeleton (Docker Compose, PostgreSQL, RabbitMQ, MinIO) | ✅ Complete |
| 2 | Core pipeline (watcher → MD → OC → DB) + GPU acceleration | ✅ Complete |
| 3 | Auth & REST API (JWT, RBAC, WebSocket) | ✅ Complete |
| 4 | Browser UI (React 18 + Vite + TailwindCSS) | ✅ Complete |
| 5 | Hardening (DLQ, startup recovery, graceful shutdown, cleanup API) | ✅ Complete |
| 6 | Post-launch (track classification, metrics, TRT pipeline, worker panel, bulk actions) | ✅ Complete v0.6.0 |
| 7 | RTSP live stream worker pool | 🔲 Future |
