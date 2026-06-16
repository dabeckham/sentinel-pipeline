# Sentinel Pipeline — Deployment Guide
*v0.6.0 — All 5 phases complete — Last updated: 2026-06-09 (session 15)*

---

## Infrastructure

| Component | Host | Details |
|---|---|---|
| Docker host | 192.168.55.10 (dabeckham) | i9-9900k, 2× RTX 3060 12GB |
| NAS (ingest source) | 192.168.55.55 | Synology DS, NFS share, read-only |
| GitHub repo | github.com/dabeckham/sentinel-pipeline | main branch |
| Project dir on host | `~/sentinel-pipeline` | |

### GPU Layout
| GPU | Physical Index | Default Use | Sentinel Use |
|---|---|---|---|
| RTX 3060 | 0 | Frigate (embeddings, detector, ffmpeg) ~5 GB | oc-worker-2 (shared — watch VRAM) |
| RTX 3060 | 1 | Ollama (unloads when idle) | oc-worker, oc-worker-3, oc-worker-4 (primary) |

> Inside the container, Docker always remaps the assigned GPU to device `0`. Set `CUDA_VISIBLE_DEVICES=0` inside the container regardless of which physical GPU is assigned.  
> Monitor GPU 0 VRAM in the MetricsBar — if Frigate starts starving, stop oc-worker-2 with `docker stop sentinel-oc-worker-2`.

### NAS Mount
```
NAS export:  192.168.55.55:/volume1/FTP/sentinel-ingest
Mount point: /mnt/ds-one/sentinel-ingest     (host)
Container:   /ingest                          (bind mount, read-only)
Type:        NFS4, read-only
fstab:       192.168.55.55:/volume1/FTP/sentinel-ingest /mnt/ds-one/sentinel-ingest nfs ro,defaults,_netdev,nofail 0 0
```

> **inotify does not work on NFS.** The orchestrator uses `PollingObserver` (not inotify). Do not switch to the default `Observer`.

---

## Service URLs

| Service | URL | Auth |
|---|---|---|
| Browser UI | http://192.168.55.10:3000 | app login |
| Orchestrator API | http://192.168.55.10:8000 | JWT bearer token |
| API Docs (Swagger) | http://192.168.55.10:8000/docs | — |
| RabbitMQ Management | http://192.168.55.10:15672 | RABBITMQ_USER / RABBITMQ_PASSWORD |
| MinIO Console | http://192.168.55.10:9001 | MINIO_ACCESS_KEY / MINIO_SECRET_KEY |
| PostgreSQL | 192.168.55.10:5432 | POSTGRES_USER / POSTGRES_PASSWORD |

---

## Prerequisites

```bash
# Docker 29.5+, Compose v5+
docker --version && docker compose version

# NFS client
sudo apt-get install -y nfs-common

# NVIDIA Container Toolkit (for GPU workers)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

---

## First-Time Deployment

### 1. Clone repository
```bash
cd ~
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
```

### 2. Mount the NAS
```bash
sudo mkdir -p /mnt/ds-one/sentinel-ingest
echo "192.168.55.55:/volume1/FTP/sentinel-ingest /mnt/ds-one/sentinel-ingest nfs ro,defaults,_netdev,nofail 0 0" \
  | sudo tee -a /etc/fstab
sudo mount -a
df -h | grep sentinel   # confirm mounted
```

### 3. Create .env
```bash
cp .env.example .env
nano .env
```

Set these values (generate secrets with `openssl rand -hex 32`):

```ini
# Secrets — generate fresh for every deployment
JWT_SECRET_KEY=<32-byte hex>
RABBITMQ_PASSWORD=<32-byte hex>
POSTGRES_PASSWORD=<32-byte hex>
MINIO_SECRET_KEY=<32-byte hex>
MINIO_ACCESS_KEY=sentinel           # username, not a secret

# Paths
INGEST_SOURCE_PATH=/mnt/ds-one/sentinel-ingest   # host-side mount
INGEST_WATCH_PATH=/ingest                         # container-side

# GPU (see GPU Layout above)
GPU_DEVICE_ID=1
```

> ⚠️ `.env` is in `.gitignore`. **Never commit it.** Keep a secure copy off the host.

### 4. Bootstrap RabbitMQ users

On the very first deployment, RabbitMQ has no application user. Create it using the env vars inside the container — no credentials in the terminal:

```bash
docker compose up -d rabbitmq
sleep 15

docker exec sentinel-rabbitmq bash -c "
  rabbitmqctl add_user \$RABBITMQ_DEFAULT_USER \$RABBITMQ_DEFAULT_PASS &&
  rabbitmqctl set_user_tags \$RABBITMQ_DEFAULT_USER administrator &&
  rabbitmqctl set_permissions -p / \$RABBITMQ_DEFAULT_USER '.*' '.*' '.*'"
```

> ⚠️ **`RABBITMQ_DEFAULT_USER/PASS` env vars only apply on the very first container startup (empty Mnesia).** On any subsequent restart, use this same `add_user` command. `change_password` silently does nothing if the user doesn't exist.

### 5. Create the YOLO model cache volume

The OC worker auto-exports `yolo11s.pt → yolo11s.engine` (TRT FP16) on first run and caches it in a named volume. Create the volume before first start:

```bash
docker volume create sentinel-pipeline_yolo-models
```

The 4-minute TRT export runs once. All 4 OC worker containers share the volume — only one worker does the export.

### 6. Start all services

```bash
# GPU mode — automatic; docker-compose.override.yml is auto-merged
docker compose up -d

# Verify all containers are up
docker compose ps
```

> ✅ **No `-f` flags needed.** `docker-compose.override.yml` is automatically merged by Docker Compose. The full GPU stack (4 OC workers) starts with plain `docker compose up -d`.

### 7. Verify deployment
```bash
# API health
curl http://localhost:8000/api/health

# Get a JWT token and check stats
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Check workers registered (should see 4 OC workers + MD worker within ~15s)
curl -s http://localhost:8000/api/workers -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### 8. Change default admin password
Log in to http://192.168.55.10:3000 (admin / changeme) and change immediately.

---

## Updating the Stack

### Normal update (code changes only)
```bash
cd ~/sentinel-pipeline
git pull

# Orchestrator (source baked into image)
docker compose build orchestrator && docker compose up -d orchestrator

# OC workers — one build updates all 4 containers
docker compose build oc-worker --build-arg CACHEBUST=$(date +%s)
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4

# MD worker
docker compose build md-worker && docker compose up -d md-worker

# UI
docker compose build ui && docker compose up -d ui
```

> **CACHEBUST:** `Dockerfile.gpu` declares `ARG CACHEBUST=1` before `COPY . .`. Pass `--build-arg CACHEBUST=$(date +%s)` to force code to be re-copied without a full `--no-cache` rebuild (which re-downloads TRT/CUDA layers and takes 10+ minutes).

> **One image, four containers:** Workers 2/3/4 use `image: sentinel-oc-worker-gpu:latest` — they cannot fall behind the primary. Building `oc-worker` updates all four.

### Full clean rebuild
```bash
git pull
docker compose build --no-cache
docker compose build --no-cache oc-worker
docker compose up -d
```

### Restarting a single service
```bash
docker compose restart orchestrator
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4
```

---

## Startup Recovery (automatic)

On every orchestrator startup, two recovery routines run automatically:

1. **`recover_stuck_jobs()`** — finds jobs in `queued`, `md_processing`, or `oc_processing` state, resets to `queued`, re-publishes to ingest queue. Runs **synchronously** before the API serves (DB-only, fast).
2. **`scan_ingest_missed()`** — walks `/ingest`, SHA-256 hashes each video file, creates a job for any file with no DB record. Runs in a **background daemon thread** (session 16) so it never blocks the API — hashing a full day of clips over NFS takes minutes. The file watcher starts first, so new arrivals are handled live while the scan backfills pre-existing files. Dedup is race-safe via the `jobs.file_hash` UNIQUE constraint (migration 0009). Safe to run repeatedly.

> If the pipeline is already backed up at startup, `startup_health_check()` pauses the watcher and the scan is deferred until the health monitor calls `resume_watcher()` once the backlog clears.

---

## Worker Self-Healing

The worker registry is in-memory inside the orchestrator. After an orchestrator restart, workers re-register automatically within one heartbeat cycle (~15 seconds):

1. **Heartbeats carry type+device** — registry bootstraps any unknown worker from heartbeat data
2. **Workers detect 404** — if the status poll returns 404 (registry lost), the worker re-publishes its `online` event for full re-registration

No worker restarts required after orchestrator restarts.

---

## Scaling Workers

### 4 OC workers (current default)

All 4 are defined in `docker-compose.override.yml` and start automatically with `docker compose up -d`:
- `oc-worker`, `oc-worker-3`, `oc-worker-4` → GPU 1
- `oc-worker-2` → GPU 0 (shared with Frigate)

### Stop GPU 0 worker if Frigate starves
```bash
docker stop sentinel-oc-worker-2
docker start sentinel-oc-worker-2   # when ready to resume
```

### More MD workers
```bash
docker compose up -d --scale md-worker=3
```

---

## Monitoring

### Queue depths
http://192.168.55.10:15672 → Queues tab

Watch:
- `ingest` — files waiting for MD worker
- `motion_results` — job descriptors waiting for OC worker
- `oc_results` — results + worker events waiting to be written
- `dlx.*` — dead-letter queues; these should normally be empty

### Worker panel
The UI at http://192.168.55.10:3000 shows a live worker panel on the Pipeline Status page. Labels: `OC-GPU-1`, `MD-CPU-1`, etc. Status dots: green=idle, yellow=processing, red=suspended. Hover for stats callout.

### Snapshot storage cleanup
```bash
TOKEN=...  # get from login
curl -s -X POST http://localhost:8000/api/snapshots/cleanup \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Logs
```bash
docker compose logs -f orchestrator
docker compose logs -f md-worker
docker compose logs -f oc-worker
```

### GPU utilization
```bash
nvidia-smi dmon -s u    # live utilization
nvidia-smi             # snapshot
```

---

## Database Migrations

Migrations run automatically on orchestrator startup via Alembic.

**Current migration head: `0009`**

| Revision | Description |
|---|---|
| 0001 | Initial schema (jobs, tracks, detections, users) |
| 0002 | OSD metadata (camera_name, recorded_at, started_at, ended_at on tracks/jobs) |
| 0003 | snapshot_bbox JSON column on tracks |
| 0004 | track_type String(16) column + index on tracks |
| 0005 | md_complete enum value; md_started_at, md_completed_at, oc_started_at on jobs |
| 0006 | md_worker_id, oc_worker_id columns on jobs |
| 0007 | `paused` value added to jobstatus enum |
| 0008 | pipeline_settings key-value table |
| 0009 | `jobs.file_hash` UNIQUE — race-safe ingest dedup |

To run or inspect manually:
```bash
docker exec sentinel-orchestrator bash -c 'cd /app && alembic upgrade head'
docker exec sentinel-orchestrator bash -c 'cd /app && alembic current'
docker exec sentinel-orchestrator bash -c 'cd /app && alembic history'
```

> ⚠️ `docker compose exec orchestrator alembic upgrade head` exits silently without applying migrations — wrong working directory. Always use `docker exec ... bash -c 'cd /app && alembic ...'`.

---

## Stopping the Stack

```bash
# Stop, keep volumes
docker compose down

# ⚠️ DESTRUCTIVE — stop and delete ALL data volumes
docker compose down -v
```

---

## Coexistence with Frigate and Ollama

Sentinel uses the `sentinel-net` bridge network. It does not share networks with Frigate or Ollama. Port conflicts to watch:

| Port | Sentinel Service |
|---|---|
| 3000 | UI (nginx) |
| 8000 | Orchestrator API |
| 5432 | PostgreSQL |
| 5672 / 15672 | RabbitMQ AMQP / Management |
| 9000 / 9001 | MinIO API / Console |

**Do not touch:** `frigate`, `ollama`, `nginx-proxy` containers.
