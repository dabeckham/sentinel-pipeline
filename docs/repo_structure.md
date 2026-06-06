# Repository Structure

```
video-analysis-system/
│
├── docker-compose.yml          # Single-host deployment (all services)
├── docker-compose.gpu.yml      # Override for GPU worker variants
├── .env.example                # Template for environment variables
├── README.md                   # Project overview + quick start
│
├── orchestrator/               # Main API + file watcher + queue consumer
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint
│   │   ├── config.py           # Settings (loaded from env)
│   │   ├── auth/               # JWT, roles, LAN trust middleware
│   │   ├── api/                # REST route handlers
│   │   │   ├── jobs.py
│   │   │   ├── tracks.py
│   │   │   ├── detections.py
│   │   │   ├── workers.py
│   │   │   ├── config.py
│   │   │   └── users.py
│   │   ├── watcher/            # FTP path file watcher
│   │   ├── consumers/          # RabbitMQ consumers (oc_results)
│   │   ├── publishers/         # RabbitMQ publishers (ingest queue)
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── schemas/            # Pydantic request/response schemas
│   │   ├── storage/            # MinIO client helpers
│   │   └── ws/                 # WebSocket status feed
│   └── alembic/                # DB migrations
│       └── versions/
│
├── md-worker/                  # Motion Detection worker
│   ├── Dockerfile
│   ├── requirements.txt
│   └── worker/
│       ├── main.py             # Worker entrypoint (consume → process → publish)
│       ├── detector.py         # MOG2 motion detection logic
│       ├── publisher.py        # motion_results publisher
│       └── config.py
│
├── oc-worker/                  # Object Classification worker
│   ├── Dockerfile
│   ├── Dockerfile.gpu          # CUDA variant
│   ├── requirements.txt
│   └── worker/
│       ├── main.py             # Worker entrypoint
│       ├── classifier.py       # YOLO26 inference
│       ├── tracker.py          # ByteTrack integration
│       ├── publisher.py        # oc_results publisher
│       └── config.py
│
├── ui/                         # Browser UI (React + Vite)
│   ├── Dockerfile              # Nginx production container
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                # React Query hooks / API client
│       ├── components/         # Shared UI components
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Ingest.tsx
│       │   ├── PipelineStatus.tsx
│       │   ├── Review.tsx
│       │   ├── Configuration.tsx
│       │   └── UserManagement.tsx
│       └── auth/               # Auth context, role guards
│
├── infra/                      # Infrastructure config files
│   ├── rabbitmq/
│   │   └── definitions.json    # Pre-configured queues + DLX
│   ├── postgres/
│   │   └── init.sql            # DB init (if needed beyond Alembic)
│   └── minio/
│       └── init.sh             # Bucket creation script
│
├── docs/
│   ├── architecture_outline.md
│   ├── github_setup_guide.md
│   ├── repo_structure.md       # (this file)
│   ├── api_spec.md             # REST API reference (to be written)
│   ├── queue_payloads.md       # Queue message schemas (to be written)
│   └── session_log.md
│
└── tests/
    ├── integration/
    └── unit/
```
