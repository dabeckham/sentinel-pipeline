# Sentinel Pipeline

A distributed, containerized video analysis system that ingests motion-triggered camera footage, detects and classifies objects frame-by-frame, tracks them across time, and stores everything for review through a browser-based UI.

---

## What It Does

Cameras FTP motion-triggered video clips to a network location. Sentinel Pipeline picks them up automatically and runs them through a multi-stage analysis pipeline:

```
FTP folder → [Ingest Queue] → Motion Detection → [Motion Queue] → Object Classification → [Result Queue] → Database + Storage
```

Results — with snapshot thumbnails of every detected object — are browsable through a web UI. Workers (motion detectors and object classifiers) run as Docker containers and can be distributed across multiple machines and GPUs.

---

## Architecture at a Glance

| Component | Technology | Role |
|---|---|---|
| **Orchestrator** | Python / FastAPI | File watcher, queue manager, REST + WebSocket API |
| **MD Workers** | Python / OpenCV MOG2 | Motion detection, frame cropping |
| **OC Workers** | Python / YOLO26 + ByteTrack | Object classification, cross-frame tracking |
| **Message Broker** | RabbitMQ | Durable job queues with dead-letter routing |
| **Database** | PostgreSQL 16 | Job, track, and detection metadata |
| **Object Storage** | MinIO | Raw frames, crops, and review snapshots |
| **UI** | React 18 / Vite / Tailwind | Ingest control, live status, review, config |

---

## Features

- **Queue-based pipeline** — each stage is independently scalable; workers can run on any machine on the network
- **Distributed workers** — MD and OC containers deployable across multiple hosts and GPUs
- **Object tracking** — ByteTrack assigns persistent IDs to objects across frames
- **Snapshot review** — best-confidence frame per tracked object saved for quick UI review
- **Full frame sequence** — click any detection to step through every frame in its track
- **Multi-user auth** — role-based access control (admin / operator / viewer) with optional LAN trust mode
- **Historical processing** — designed first for bulk processing of years of archived footage

---

## Project Structure

```
sentinel-pipeline/
├── orchestrator/       # FastAPI service — watcher, API, queue consumer
├── md-worker/          # Motion detection worker (OpenCV MOG2)
├── oc-worker/          # Object classification worker (YOLO26 + ByteTrack)
├── ui/                 # React browser UI
├── infra/              # RabbitMQ, MinIO, Postgres config
├── docs/               # Architecture, setup guides, API reference
├── tests/              # Integration and unit tests
├── docker-compose.yml  # Single-host deployment
├── docker-compose.gpu.yml  # GPU worker override
└── .env.example        # Environment variable template
```

---

## Quick Start (coming in Phase 1)

> Prerequisites: Docker Desktop, Docker Compose, Git

```bash
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

Then open `http://localhost:3000` in your browser.

---

## Build Phases

| Phase | Scope | Status |
|---|---|---|
| 1 | Infrastructure skeleton (Docker Compose, DB, RabbitMQ, MinIO) | 🔲 Planned |
| 2 | Core pipeline (watcher → MD → OC → DB) | 🔲 Planned |
| 3 | Auth & REST API | 🔲 Planned |
| 4 | Browser UI | 🔲 Planned |
| 5 | Hardening (DLQ, retry, logging, tests) | 🔲 Planned |
| 6 | RTSP live stream support | 🔲 Future |

---

## Tech Stack

- **Python 3.12** — orchestrator, MD workers, OC workers
- **YOLO26** (Ultralytics) — object detection and classification
- **ByteTrack** — multi-object tracking across frames
- **RabbitMQ** — message broker (AMQP)
- **PostgreSQL 16** — metadata storage
- **MinIO** — S3-compatible frame and snapshot storage
- **React 18 + Vite + TailwindCSS** — browser UI
- **Docker / Docker Compose** — containerized deployment

---

## Documentation

- [Architecture Outline](docs/architecture_outline.md)
- [Repository Structure](docs/repo_structure.md)
- [GitHub Setup Guide](docs/github_setup_guide.md)
- API Reference — _coming in Phase 3_
- Queue Payload Schemas — _coming in Phase 2_

---

## License

MIT
