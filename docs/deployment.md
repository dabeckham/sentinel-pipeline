# Sentinel Pipeline — Deployment Guide
*v0.5.0 — All 5 phases complete — Last updated: 2026-06-07 (session 13)*

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
| RTX 3060 | 0 | Frigate (embeddings, detector, ffmpeg) ~5 GB | Do not target |
| RTX 3060 | 1 | Ollama (unloads when idle) | OC workers (default) |

> **Rule:** Never set `GPU_DEVICE_ID=0` unless you have confirmed Frigate is stopped.
> Inside the container, Docker always remaps the assigned GPU to device `0`. Set `CUDA_VISIBLE_DEVICES=0` (not `1`) in the container environment.

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
# Add to /etc/fstab:
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
RABBITMQ_PASSWORD=<16-byte hex>
POSTGRES_PASSWORD=<16-byte hex>
MINIO_SECRET_KEY=<16-byte hex>
MINIO_ACCESS_KEY=sentinel           # username, not a secret

# Paths
INGEST_SOURCE_PATH=/mnt/ds-one/sentinel-ingest   # host-side, not used by containers
INGEST_WATCH_PATH=/ingest                         # container-side

# GPU (see GPU Layout above)
GPU_DEVICE_ID=1

# Optional: override default admin password on first boot
# ADMIN_DEFAULT_PASSWORD=changeme   # change this immediately after first login
```

> ⚠️ `.env` is in `.gitignore`. **Never commit it.** Keep a secure copy off the host (see DR doc).

### 4. Bootstrap RabbitMQ users
On the very first deployment, RabbitMQ creates the `guest` user only. The Sentinel user must be created manually once:

```bash
docker compose up -d rabbitmq
sleep 15

RMQPASS=$(grep RABBITMQ_PASSWORD ~/sentinel-pipeline/.env | cut -d= -f2)
docker exec sentinel-rabbitmq rabbitmqctl add_user sentinel "$RMQPASS"
docker exec sentinel-rabbitmq rabbitmqctl set_user_tags sentinel administrator
docker exec sentinel-rabbitmq rabbitmqctl set_permissions -p / sentinel ".*" ".*" ".*"
```

### 5. Create the YOLO model cache volume

The OC worker caches `yolo11s.pt` in a named volume at `/app/models`. Create it once before first start:

```bash
docker volume create sentinel-pipeline_yolo-models
```

The model (~20 MB) downloads on the first job and is reused across restarts. Without this volume it re-downloads every container start.

### 6. Start all services

**GPU mode (recommended — OC workers on GPU 1):**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

**CPU-only mode:**
```bash
docker compose up -d
```

> ⚠️ **Always use both compose files together for GPU mode.** Running `docker-compose.gpu.yml` alone causes the oc-worker to join the wrong Docker network (DNS for `rabbitmq` fails) and to miss env vars (RabbitMQ auth fails).

### 7. Verify deployment
```bash
# All containers healthy
docker compose ps

# API health (should show version 0.5.0)
curl http://localhost:8000/api/health

# Get a JWT token and check stats
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Browser UI
open http://192.168.55.10:3000
```

### 8. Change default admin password
Log in to the UI at http://192.168.55.10:3000 (admin / changeme) and change the password immediately, or via API:
```bash
# Get token first (see step 6), then:
curl -s -X PATCH http://localhost:8000/api/users/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password":"YourNewSecurePassword"}'
```

---

## Updating the Stack

### Normal update (code changes only)
```bash
cd ~/sentinel-pipeline
git pull

# Orchestrator (source baked into image — restart alone won't pick up changes)
docker compose build orchestrator && docker compose up -d orchestrator

# OC worker — MUST use both compose files; use CACHEBUST to bust the COPY layer
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build \
  --build-arg CACHEBUST=$(date +%s) oc-worker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker

# MD worker
docker compose build md-worker && docker compose up -d md-worker

# UI
docker compose build ui && docker compose up -d ui
```

### Full clean rebuild (when system deps or requirements.txt changed)
```bash
git pull
docker compose build --no-cache
docker compose -f docker-compose.yml -f docker-compose.gpu.yml build --no-cache oc-worker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

> **Note on `--no-cache` for oc-worker:** The GPU Dockerfile uses `ubuntu22.04` + `python3.10`. The `deadsnakes` PPA is not used (unreachable from the build host). `--no-cache` is safe and will install python3.10 from default ubuntu repos. `torch+cu124` installs from PyPI without issues.

> **CACHEBUST arg:** `docker-compose.gpu.yml` declares `ARG CACHEBUST=1` before the `COPY . .` layer. Pass `--build-arg CACHEBUST=$(date +%s)` to force code to be re-copied without doing a full `--no-cache` rebuild (which re-downloads torch/CUDA layers and takes 10+ minutes).

### Restarting a single service
```bash
docker compose restart orchestrator
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker
```

---

## Startup Recovery (automatic)

On every orchestrator startup, two recovery routines run automatically before the file watcher starts:

1. **`recover_stuck_jobs()`** — finds jobs in `queued`, `md_processing`, or `oc_processing` state from the previous run, resets them to `queued`, and re-publishes to the ingest queue. Handles power-loss and worker crashes transparently.

2. **`scan_ingest_missed()`** — walks `/ingest`, SHA-256 hashes each video file, and creates a job for any file with no DB record. Handles files that arrived while the orchestrator was down. Safe to run repeatedly — the hash check prevents double-ingestion.

You will see these log lines on startup:
```
startup_recovery_no_stuck_jobs            (nothing stuck)
startup_recovery_requeueing job_id=X      (recovering a stuck job)
startup_scan_found_files count=12         (pre-existing files found)
startup_scan_new_file path=/ingest/...    (each new file submitted)
startup_scan_complete new_jobs=12
```

---

## Scaling Workers

### More MD workers
```bash
docker compose up -d --scale md-worker=3
```

### More OC workers (CPU)
```bash
docker compose up -d --scale oc-worker=4
```

### More OC workers (GPU) — add a second GPU compose override first
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --scale oc-worker=2
```

---

## Monitoring

### Queue depths (RabbitMQ management UI)
http://192.168.55.10:15672 → Queues tab

Watch:
- `ingest` — files waiting for MD worker
- `motion_results` — frames waiting for OC worker
- `oc_results` — results waiting to be written to DB
- `dlx.*` — dead-letter queues; these should normally be empty

### Snapshot storage cleanup

The OC worker saves a full-frame snapshot for every detection (`track_{id}_f{frame}.jpg`) for in-browser playback. These accumulate quickly. Periodically clean them up, keeping only the best-shot thumbnail (`_best.jpg`) per track:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Cleanup all jobs (dry-run: check response before running in prod)
curl -s -X POST http://localhost:8000/api/snapshots/cleanup \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Cleanup single job
curl -s -X POST "http://localhost:8000/api/snapshots/cleanup?job_id=42" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Response includes `deleted` (count), `freed_bytes`, and `errors`.

### Requeue dead-lettered messages (admin only)
```bash
# Check counts
curl -s http://localhost:8000/api/dlx/counts \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Requeue up to 100 messages from dlx.ingest
curl -s -X POST "http://localhost:8000/api/dlx/requeue?queue=dlx.ingest&limit=100" \
  -H "Authorization: Bearer $TOKEN"
```

### Logs
```bash
docker compose logs -f orchestrator
docker compose logs -f md-worker
docker compose logs -f oc-worker
docker compose logs --tail=100 orchestrator
```

### GPU utilization
```bash
nvidia-smi dmon -s u    # live utilization
nvidia-smi             # snapshot
```

---

## Database Migrations

Migrations run automatically on orchestrator startup via Alembic.

**Current migration head: `0003`** (adds `snapshot_bbox` JSON column to `tracks`)

| Revision | Description |
|---|---|
| 0001 | Initial schema (jobs, tracks, detections, users) |
| 0002 | OSD metadata (camera_name, recorded_at, started_at, ended_at on tracks/jobs) |
| 0003 | snapshot_bbox JSON column on tracks (best-shot frame bbox for UI overlay) |

To run or inspect manually:
```bash
# Must run from /app inside the container — docker compose exec doesn't set the CWD
docker exec sentinel-orchestrator bash -c 'cd /app && alembic upgrade head'
docker exec sentinel-orchestrator bash -c 'cd /app && alembic current'
docker exec sentinel-orchestrator bash -c 'cd /app && alembic history'
```

> ⚠️ `docker compose exec orchestrator alembic upgrade head` exits silently without error but does **not** apply migrations — the working directory is wrong. Always use `docker exec ... bash -c 'cd /app && alembic ...'`.

---

## Stopping the Stack

```bash
# Stop, keep volumes
docker compose down

# Stop GPU worker separately first if needed
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down

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

If any conflict exists, remap the host port in `.env` (e.g. `POSTGRES_PORT=5433`).

**Do not touch:** `frigate`, `ollama`, `nginx-proxy` containers.
