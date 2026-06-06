# Distributed Video Analysis System — Architecture Outline
*Draft v0.2 — Updated 2026-06-05*

---

## 1. System Overview

A distributed, containerized pipeline that:
1. Watches a network path for FTP-uploaded, camera-triggered video files
2. Detects motion regions per frame (for precise localization within already-motion-triggered clips)
3. Classifies and tracks objects across frames (YOLO26 + ByteTrack)
4. Persists metadata to a database and frame snapshots to object storage
5. Exposes a browser-based UI for configuration, ingestion control, and result review
6. Supports multi-user access with role-based auth (admin, operator, viewer)

All inter-process communication is queue-based, enabling horizontal scaling of MD and OC workers across multiple hosts/GPUs.

---

## 2. Resolved Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| Q1 | Message broker | **RabbitMQ** | Durable queues, per-message ACK, dead-letter routing — essential for crash recovery on long video jobs |
| Q2 | MD algorithm | **MOG2 background subtraction** | Best for fixed-camera footage; configurable per-job |
| Q3 | OC model | **YOLO26** (Ultralytics, Jan 2026) | NMS-free inference, fastest CPU/GPU performance, best accuracy |
| Q4 | Object tracker | **ByteTrack** | Lightweight, no re-ID features needed, accurate cross-frame tracking |
| Q5 | Auth | **Multi-user RBAC** (admin / operator / viewer) | Admins can toggle LAN-trust mode on/off via UI |
| Q6 | Storage backend | **MinIO** | S3-compatible, self-hosted, Docker-native |
| Q7 | Worker transport | **Queue-pull** | Workers pull from broker; cleaner for distributed deployment |
| Q8 | Video input | **FTP file-first** | Cameras FTP motion-triggered clips; RTSP streams deferred to Phase 5 |
| Q9 | Snapshot selection | **Highest-confidence detection frame** per track | Most useful for review |
| Q10 | Queue state mirroring | **Mirror to Postgres** | Richer UI; managed as a background sync |

---

## 3. Key Technology Notes

### RabbitMQ vs. MQTT (Mosquitto)
These are related but different things:
- **MQTT / Mosquitto** is a *protocol* designed for lightweight IoT pub/sub. It's fire-and-forget — no acknowledgment, no durable queues. Great for sensor data; not suitable for job queuing where you can't afford to lose a message.
- **RabbitMQ** is a full *message broker* that speaks AMQP (and optionally MQTT). It provides durable queues, per-message acknowledgment, routing rules, dead-letter queues, and consumer groups. If a worker crashes mid-job, the message is automatically re-queued. This is what we need.

### YOLO26
Released January 2026 by Ultralytics. Key improvements over YOLOv8/YOLO11:
- NMS-free by default (no post-processing step needed)
- 43% faster CPU inference vs YOLO11
- +2.5 box AP accuracy gain on COCO
- Same `ultralytics` Python package — `pip install ultralytics`

### ByteTrack
A multi-object tracker that assigns a persistent `track_id` to each detected object across video frames. It uses a Kalman filter to predict where an object will be in the next frame, then matches predictions to new detections using IoU (Intersection over Union). Crucially, it doesn't require appearance features (no separate re-ID model), making it fast and easy to deploy. Result: even if an object disappears for a few frames, ByteTrack correctly re-associates it when it reappears.

### FTP Ingestion Context
Since cameras already perform on-device motion detection before FTPing clips, **every incoming file contains motion**. The MD stage within our pipeline still serves a purpose: it pinpoints *which frames and regions* within the clip contain motion, allowing OC workers to focus inference on relevant crops rather than every pixel of every frame. This significantly reduces OC compute load.

---

## 4. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser UI                           │
│  (Ingest | Status | Review | Config | User Management)      │
└────────────────────────┬────────────────────────────────────┘
                         │ REST / WebSocket
┌────────────────────────▼────────────────────────────────────┐
│                    Orchestrator (FastAPI)                    │
│  - FTP path watcher (poll + optional inotify)               │
│  - Job deduplication (content hash → DB)                    │
│  - Enqueues jobs, tracks pipeline state                     │
│  - Writes OC results to DB; manages MinIO snapshots         │
│  - REST API + WebSocket for UI                              │
│  - User/role management (JWT auth)                          │
└──┬─────────────────────┬──────────────────────┬─────────────┘
   │                     │                      │
   ▼                     ▼                      ▼
RabbitMQ             MinIO                  PostgreSQL
(message broker)     (frame/snapshot        (metadata DB)
                      storage)
   │
   ├──► [ingest]          Video paths      → MD Workers
   ├──► [motion_results]  Bounding boxes   → OC Workers
   └──► [oc_results]      Classifications  → Orchestrator

┌─────────────────────────────────────────────────────────────┐
│              MD Worker Pool (N containers)                  │
│  - MOG2 background subtraction                              │
│  - Outputs per-frame bounding boxes + timestamps            │
│  - Saves frame crops to MinIO staging                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              OC Worker Pool (M containers)                  │
│  - YOLO26 inference (CPU or GPU)                            │
│  - ByteTrack cross-frame tracking                           │
│  - Saves best-confidence snapshot per track to MinIO        │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Authentication & Authorization

### Roles
| Role | Capabilities |
|---|---|
| **admin** | Full access; manage users; toggle LAN-trust mode; configure workers/broker/storage |
| **operator** | Start/stop ingest; view all results; export |
| **viewer** | Read-only access to results and review pages |

### Auth Modes
- **Standard:** JWT-based login (username + password). Tokens expire; refresh tokens supported.
- **LAN Trust Mode:** When enabled by an admin, requests from configured IP ranges bypass credential check. Toggleable in the Configuration page by admin-role users only.

### Implementation
- Passwords hashed with bcrypt
- JWT issued by Orchestrator API
- Role stored in JWT claims; verified per endpoint
- LAN trust mode: configurable CIDR ranges stored in DB; checked as middleware

---

## 6. Queue Design

| Queue | Producer | Consumer | Payload |
|---|---|---|---|
| `ingest` | Orchestrator | MD Workers | `{ job_id, video_path, source_type, options }` |
| `motion_results` | MD Workers | OC Workers | `{ job_id, frame_index, timestamp_ms, bounding_boxes[], crop_paths[] }` |
| `oc_results` | OC Workers | Orchestrator | `{ job_id, track_id, frame_index, class_label, confidence, bbox, snapshot_path }` |
| `dlx.*` | RabbitMQ (auto) | Dead-letter handler | Failed/expired messages from any queue |

---

## 7. Database Schema (Draft)

```sql
-- One row per ingested video
jobs (id, file_path, file_hash, source_path, status, created_at, completed_at)

-- Worker heartbeat registry
workers (id, type [md|oc], host, queue_name, status, last_seen_at, model_version)

-- Per-frame motion detections from MD workers
motion_events (id, job_id, frame_index, timestamp_ms, bounding_boxes jsonb)

-- Unique object track across frames
tracks (id, job_id, class_label, confidence_max, first_frame, last_frame,
        snapshot_path, created_at)

-- Per-frame classification result
detections (id, track_id, job_id, frame_index, class_label, confidence,
            bbox jsonb, crop_path, created_at)

-- Users and roles
users (id, username, email, password_hash, role, created_at, last_login)

-- System configuration (key/value)
config (key, value, updated_by, updated_at)
```

---

## 8. MinIO Storage Layout

```
frames-raw/     Full frames at motion timestamps (keyed by job_id/frame_index)
crops/          Bounding-box crops sent to OC workers (keyed by job_id/frame/bbox)
snapshots/      Best-confidence frame per track — shown in UI review page
```

---

## 9. UI Pages

| Page | Description |
|---|---|
| **Ingest** | Source path input, recursion toggle, start/stop, file queue live status |
| **Pipeline Status** | Queue depths, active workers (type, host, model), jobs in-flight, error rate |
| **Results / Review** | Filterable grid of tracks with snapshot thumbnails; click → full frame sequence; export CSV |
| **Configuration** | Register/deregister MD+OC workers; broker URL; storage endpoint; model selection; LAN trust CIDR |
| **User Management** | (Admin only) Create/edit/delete users; assign roles |

---

## 10. Proposed Build Phases

### Phase 1 — Infrastructure Skeleton
- [ ] Repo structure + Docker Compose (all services, stub configs)
- [ ] Database schema + Alembic migrations
- [ ] RabbitMQ setup with all queues + DLX
- [ ] MinIO bucket initialization
- [ ] Orchestrator stub (FastAPI app, health endpoint)

### Phase 2 — Core Pipeline
- [ ] Orchestrator: FTP path watcher + ingest queue publisher
- [ ] MD Worker: MOG2 motion detection + motion_results publisher
- [ ] OC Worker: YOLO26 inference + ByteTrack + oc_results publisher
- [ ] Orchestrator: OC result consumer + DB writer + MinIO snapshot copy

### Phase 3 — Auth & API
- [ ] JWT auth (login, refresh, role middleware)
- [ ] LAN trust mode middleware
- [ ] REST endpoints (jobs, tracks, detections, workers, config, users)
- [ ] WebSocket status feed

### Phase 4 — Browser UI
- [ ] React app scaffold (Vite + TailwindCSS)
- [ ] Auth (login page, role-gated routes)
- [ ] Ingest page
- [ ] Pipeline status page (live queue depths, worker health)
- [ ] Results / Review page (snapshot grid, frame sequence viewer)
- [ ] Configuration page
- [ ] User management page

### Phase 5 — Hardening
- [ ] Dead-letter queue handler + retry logic
- [ ] Job deduplication (content hash)
- [ ] Graceful shutdown / job requeue on worker crash
- [ ] Structured logging (JSON → optional Loki)
- [ ] Integration test suite

### Phase 6 — RTSP Streams (Future)
- [ ] RTSP ingest worker pool (separate from file-based MD workers)
- [ ] Stream segmentation → feed into existing motion_results queue
- [ ] UI: add stream source management

---

## 11. Technology Stack

| Layer | Technology |
|---|---|
| Orchestrator API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic |
| MD Workers | Python 3.12, OpenCV (MOG2) |
| OC Workers | Python 3.12, Ultralytics YOLO26, ByteTrack |
| Message Broker | RabbitMQ 3.x (pika client) |
| Database | PostgreSQL 16 |
| Object Storage | MinIO |
| UI | React 18, Vite, TailwindCSS, React Query, Recharts |
| Auth | JWT (python-jose), bcrypt |
| Container Runtime | Docker, Docker Compose |
| GPU Workers | nvidia/cuda:12.x-runtime base image |

---

*End of Draft v0.2*
