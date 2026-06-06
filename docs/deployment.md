# Sentinel Pipeline — Deployment Guide
*Keep this document updated as the system evolves.*

---

## Infrastructure

| Component | Host | Notes |
|---|---|---|
| Docker host | 192.168.55.10 | i9-9900k, 2x RTX 3060 12GB |
| NAS (ingest source) | 192.168.55.55 | Synology, NFS share |
| GitHub repo | github.com/dabeckham/sentinel-pipeline | |

### GPU Layout
| GPU | Index | Current Use | Sentinel Use |
|---|---|---|---|
| RTX 3060 | 0 | Frigate (embeddings + detector + 17x ffmpeg) ~5GB | Available if GPU 1 full |
| RTX 3060 | 1 | Ollama (unloads when idle) ~0-11GB | OC workers (default) |

### NAS Mount
```
NAS path:    192.168.55.55:/volume1/FTP/sentinel-ingest
Mount point: /mnt/ds-one/sentinel-ingest
Mount type:  NFS read-only
fstab entry: 192.168.55.55:/volume1/FTP/sentinel-ingest /mnt/ds-one/sentinel-ingest nfs ro,defaults,_netdev,nofail 0 0
```

---

## Prerequisites

- Docker 29.5.2+
- Docker Compose v5+
- NFS client: `sudo apt-get install -y nfs-common`
- NVIDIA Container Toolkit (for GPU workers)
- NAS share mounted (see above)

### Install NVIDIA Container Toolkit (if not already installed)
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

---

## First-Time Deployment

### 1. Clone the repository
```bash
cd ~
git clone https://github.com/dabeckham/sentinel-pipeline.git
cd sentinel-pipeline
```

### 2. Create and configure .env
```bash
cp .env.example .env
nano .env
```

**Required values to set:**
```bash
# Generate secrets:
openssl rand -hex 32    # → JWT_SECRET_KEY
openssl rand -hex 16    # → RABBITMQ_PASSWORD
openssl rand -hex 16    # → POSTGRES_PASSWORD
openssl rand -hex 16    # → MINIO_SECRET_KEY / MINIO_ACCESS_KEY

INGEST_SOURCE_PATH=/mnt/ds-one/sentinel-ingest
```

> ⚠️ Never commit `.env` to git. It is in `.gitignore`.  
> Store a secure copy of `.env` separately (see Disaster Recovery).

### 3. Start the stack (CPU mode)
```bash
docker compose up -d
docker compose logs -f   # watch for errors; Ctrl-C when settled
```

### 4. Start the stack (GPU mode — OC workers on GPU 1)
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

To use GPU 0 instead (e.g. if Ollama has GPU 1 loaded):
```bash
GPU_DEVICE_ID=0 docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

### 5. Verify
```bash
# API health
curl http://localhost:8000/api/health

# All containers running
docker compose ps

# RabbitMQ management UI
# http://192.168.55.10:15672  (login: RABBITMQ_USER / RABBITMQ_PASSWORD from .env)

# MinIO console
# http://192.168.55.10:9001  (login: MINIO_ACCESS_KEY / MINIO_SECRET_KEY from .env)

# UI
# http://192.168.55.10:3000
```

---

## Updating the Stack

```bash
cd ~/sentinel-pipeline
git pull
docker compose build          # rebuild changed images
docker compose up -d          # recreate changed containers only
```

Or for a full clean rebuild:
```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

---

## Scaling Workers

### Add more MD workers
```bash
docker compose up -d --scale md-worker=3
```

### Add more OC workers (CPU)
```bash
docker compose up -d --scale oc-worker=4
```

### Add GPU OC workers
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --scale oc-worker=2
```

---

## Service URLs

| Service | URL | Credentials |
|---|---|---|
| Orchestrator API | http://192.168.55.10:8000 | JWT (Phase 3) |
| API Docs (Swagger) | http://192.168.55.10:8000/docs | — |
| RabbitMQ Management | http://192.168.55.10:15672 | RABBITMQ_USER/PASSWORD |
| MinIO Console | http://192.168.55.10:9001 | MINIO_ACCESS_KEY/SECRET_KEY |
| UI | http://192.168.55.10:3000 | app login (Phase 3) |
| PostgreSQL | 192.168.55.10:5432 | POSTGRES_USER/PASSWORD |

---

## Stopping the Stack

```bash
# Stop but keep data volumes
docker compose down

# Stop and DELETE all data (destructive!)
docker compose down -v
```

---

## Logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f orchestrator
docker compose logs -f md-worker
docker compose logs -f oc-worker

# Last 100 lines
docker compose logs --tail=100 orchestrator
```

---

## Running Database Migrations Manually

Migrations run automatically on orchestrator startup. To run manually:
```bash
docker compose exec orchestrator alembic upgrade head
docker compose exec orchestrator alembic current   # show current revision
docker compose exec orchestrator alembic history   # show migration history
```

---

## Coexistence with Frigate and Ollama

Sentinel containers are on the `sentinel-net` bridge network and do not interfere with Frigate or Ollama. Port conflicts to watch:

| Port | Used by |
|---|---|
| 5432 | Sentinel PostgreSQL — confirm Frigate isn't using host port 5432 |
| 9000/9001 | Sentinel MinIO |
| 5672/15672 | Sentinel RabbitMQ |
| 8000 | Sentinel Orchestrator API |
| 3000 | Sentinel UI |

If any conflict exists, change the `PORT_*` values in `.env`.

---

*Last updated: 2026-06-05 — Phase 1*
