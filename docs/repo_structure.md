# Repository Structure
*Last updated: 2026-06-09 (session 15)*

```
sentinel-pipeline/
│
├── docker-compose.yml              # Base stack — all services, CPU fallback oc-worker
├── docker-compose.override.yml     # GPU override — auto-merged by Docker Compose
│                                   # 4 OC workers: oc-worker/2/3/4 (GPU 1 + GPU 0)
│                                   # All 4 share image: sentinel-oc-worker-gpu:latest
├── .env.example                    # Environment variable template
├── README.md                       # Project overview + quick start
│
├── orchestrator/                   # FastAPI service — watcher, API, consumers, metrics
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                 # FastAPI app + lifespan (starts watcher, consumers, liveness monitor)
│       ├── config.py               # Settings (loaded from env)
│       ├── db.py                   # SQLAlchemy engine + SessionLocal
│       ├── auth/                   # JWT, roles, LAN trust middleware
│       ├── api/                    # REST route handlers
│       │   ├── jobs.py             # CRUD + bulk pause/kill + cancel/delete
│       │   ├── tracks.py           # Paginated tracks + cameras + active-days
│       │   ├── users.py            # User CRUD (admin)
│       │   ├── workers.py          # Worker list + suspend/resume
│       │   ├── metrics.py          # SSE stream (CPU/RAM/disk/GPU every 2s)
│       │   ├── snapshots.py        # MinIO proxy + cleanup
│       │   ├── dlx.py              # Dead-letter queue counts + requeue
│       │   ├── config.py           # Runtime config
│       │   └── ws.py               # WebSocket broadcast (job events)
│       ├── models/                 # SQLAlchemy ORM models
│       │   ├── job.py              # Job, JobStatus enum (incl. paused)
│       │   ├── track.py            # Track (incl. snapshot_bbox, track_type)
│       │   ├── detection.py        # Detection
│       │   └── user.py             # User
│       ├── schemas/                # Pydantic request/response schemas
│       ├── services/
│       │   ├── result_consumer.py  # Consumes oc_results — writes DB, routes worker events
│       │   ├── worker_registry.py  # In-memory worker state (self-healing on heartbeat)
│       │   ├── ingest_publisher.py # Publishes to ingest queue
│       │   ├── watcher.py          # PollingObserver (NFS-safe) + startup recovery
│       │   └── event_loop.py       # Stores FastAPI loop ref for background-thread broadcasts
│       └── alembic/
│           └── versions/
│               ├── 0001_initial_schema.py
│               ├── 0002_osd_metadata.py
│               ├── 0003_snapshot_bbox.py
│               ├── 0004_track_type.py
│               ├── 0005_stage_timestamps.py
│               ├── 0006_worker_id_columns.py
│               ├── 0007_paused_status.py
│               └── 0008_pipeline_settings.py
│
├── md-worker/                      # Motion Detection worker (CPU)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── worker/
│       ├── main.py                 # Consume ingest → detect motion → publish job descriptor
│       ├── detector.py             # Frigate-style weighted-average background subtraction
│       ├── publisher.py            # Publishes to motion_results queue
│       ├── worker_events.py        # WorkerEventPublisher (online/heartbeat/offline + suspension poll)
│       └── config.py               # Settings
│
├── oc-worker/                      # Object Classification worker (GPU)
│   ├── Dockerfile                  # CPU fallback (unused in production)
│   ├── Dockerfile.gpu              # ubuntu22.04 + python3.10 + CUDA 12 + TRT
│   ├── requirements.txt
│   └── worker/
│       ├── main.py                 # Consume job descriptor → run full TRT+ByteTrack pipeline
│       ├── detector.py             # TRT FP16 inference + CPU decode overlap + ByteTrack
│       ├── publisher.py            # Publishes status updates + final result to oc_results
│       ├── worker_events.py        # WorkerEventPublisher (online/heartbeat/offline + suspension poll)
│       └── config.py               # Settings
│
├── ui/                             # Browser UI (React 18 + Vite + TailwindCSS)
│   ├── Dockerfile                  # nginx production container
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                    # API client hooks (fetch + react-query)
│       ├── components/
│       │   ├── Layout.jsx          # App shell — owns WebSocket connection + toast
│       │   ├── MetricsBar.jsx      # Bottom strip — SSE GPU/CPU/RAM metrics
│       │   ├── PipelineStatus.jsx  # Left panel — live worker cards (labels, dots, callout)
│       │   ├── BboxOverlay.jsx     # SVG bbox drawn over snapshot thumbnails
│       │   ├── SnapshotImg.jsx     # Fetches MinIO image with JWT → blob URL
│       │   └── ...
│       ├── pages/
│       │   ├── Login.jsx
│       │   ├── Jobs.jsx            # Jobs table — infinite scroll, stage timeline, bulk actions
│       │   ├── Tracks.jsx          # Track card grid — filters, infinite scroll, modal player
│       │   ├── Users.jsx           # Admin user management
│       │   └── Dashboard.jsx
│       └── auth/                   # Auth context, role guards
│
├── infra/                          # Infrastructure config files
│   ├── rabbitmq/
│   │   └── definitions.json        # Pre-configured queues + DLX
│   ├── postgres/
│   │   └── init.sql
│   └── minio/
│       └── init.sh                 # Bucket creation script
│
├── docs/
│   ├── architecture_outline.md     # System architecture, design decisions, pipeline detail
│   ├── deployment.md               # Infrastructure, first-time setup, scaling, migrations
│   ├── disaster_recovery.md        # Backup, restore, secret rotation, RabbitMQ recovery
│   ├── session_log.md              # Build history + current state (RECOVERY ENTRY POINT)
│   ├── repo_structure.md           # This file
│   ├── github_setup_guide.md       # GitHub PAT setup
│   └── decode_inference_research.md # Benchmark results for decode strategies
│
├── yolo-test/                      # Standalone TRT benchmark scripts
│   └── docker-compose.test.yml
│
└── scripts/
    └── create_issues.py            # GitHub issue creation helper (unused — use curl via SSH)
```

## Key Files to Know

| File | Why It Matters |
|---|---|
| `docker-compose.override.yml` | Defines all 4 GPU OC workers; auto-merged — no `-f` flags needed |
| `orchestrator/app/services/worker_registry.py` | In-memory worker state; `on_heartbeat()` auto-registers unknown workers |
| `orchestrator/app/services/result_consumer.py` | Routes all oc_results messages (worker events + job results) |
| `oc-worker/worker/detector.py` | TRT FP16 load + warmup + ByteTrack pipeline |
| `oc-worker/worker/worker_events.py` | Self-healing: re-announces on HTTP 404 from orchestrator |
| `md-worker/worker/detector.py` | Frigate-style weighted-average motion detection |
| `ui/src/components/PipelineStatus.jsx` | Worker panel — labels, status dots, suspend/resume, stats callout |
| `docs/session_log.md` | **Start here to resume a session** |
