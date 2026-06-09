# Sentinel Pipeline — Disaster Recovery
*v0.6.0 — Last updated: 2026-06-09 (session 15)*

---

## What Needs to Be Protected

| Data | Location | Criticality | Notes |
|---|---|---|---|
| `.env` | `~/sentinel-pipeline/.env` on host | **CRITICAL** | All secrets; if lost, all services fail. Cannot be regenerated — existing DB/MinIO data becomes inaccessible. |
| PostgreSQL | Docker volume `sentinel_postgres-data` | **Critical** | All job, track, detection metadata. |
| MinIO snapshots | Docker volume `sentinel_minio-data` | **High** | `_best.jpg` thumbnails + `_f{frame}.jpg` playback frames. Source videos still on NAS — reprocessable. |
| RabbitMQ state | Docker volume `sentinel_rabbitmq-data` | **Low** | In-flight messages only. Queues auto-recreate on start; lost jobs auto-recover via startup scan. |
| Source videos | NAS `/volume1/FTP/sentinel-ingest` | **High** | Managed by Synology — configure NAS-side backup separately. |
| Code | GitHub `dabeckham/sentinel-pipeline` | **None** | Always available from git. |

---

## Backup Procedures

### 1. Back Up `.env` — Do This Now

The `.env` file is **not in git** and contains all secrets. If the host dies without a backup, you cannot access the PostgreSQL data with a fresh secret.

```bash
scp dabeckham@192.168.55.10:~/sentinel-pipeline/.env ~/sentinel-env-backup.env
```

Store in a password manager, encrypted USB, or private vault. Never in the same location as the host.

**Required fields to keep:**
- `JWT_SECRET_KEY`
- `RABBITMQ_DEFAULT_PASS` (or `RABBITMQ_PASSWORD`)
- `POSTGRES_PASSWORD`
- `MINIO_SECRET_KEY` / `MINIO_ACCESS_KEY`

---

### 2. Back Up PostgreSQL

#### Manual snapshot
```bash
docker compose exec -T postgres pg_dump -U sentinel sentinel \
  > ~/backups/sentinel_pg_$(date +%Y%m%d_%H%M%S).sql
```

#### Restore from snapshot
```bash
docker compose up -d postgres
sleep 10
docker compose exec -T postgres psql -U sentinel sentinel \
  < ~/backups/sentinel_pg_20260606_120000.sql
```

#### Automated daily backup
```bash
mkdir -p ~/backups
# Add to crontab (crontab -e):
0 3 * * * cd ~/sentinel-pipeline && \
  docker compose exec -T postgres pg_dump -U sentinel sentinel \
  > ~/backups/sentinel_pg_$(date +\%Y\%m\%d).sql && \
  find ~/backups -name "sentinel_pg_*.sql" -mtime +30 -delete
```

---

### 3. Back Up MinIO Snapshots

#### Option A — Mirror to NAS (recommended)
```bash
wget https://dl.min.io/client/mc/release/linux-amd64/mc -O /usr/local/bin/mc
chmod +x /usr/local/bin/mc
mc alias set sentinel http://localhost:9000 $MINIO_ACCESS_KEY $MINIO_SECRET_KEY
mc mirror sentinel/snapshots /mnt/ds-one/sentinel-snapshots-backup/
```

Add to crontab (2 AM nightly):
```bash
0 2 * * * mc mirror sentinel/snapshots /mnt/ds-one/sentinel-snapshots-backup/
```

#### Option B — Volume tarball
```bash
docker run --rm \
  -v sentinel_minio-data:/data \
  -v ~/backups:/backup \
  alpine tar czf /backup/minio_$(date +%Y%m%d).tar.gz /data
```

---

## Recovery Scenarios

---

### Scenario 1: Single container crash

`restart: unless-stopped` handles this automatically. If crash-looping:
```bash
docker compose logs orchestrator    # read the error
docker compose restart orchestrator
docker compose up -d --force-recreate orchestrator
```

Common causes and fixes:

| Error | Fix |
|---|---|
| `admin_seed_error` / bcrypt fail | bcrypt must be pinned to 3.2.2 in requirements.txt |
| DB connection refused | Postgres not ready yet — wait 15s and retry |
| `socket.gaierror: Temporary failure in name resolution` | Wrong network — did you remove `docker-compose.override.yml`? |
| `ACCESS_REFUSED - Login was refused` | RabbitMQ user missing from Mnesia — see Scenario 8 |
| Alembic migration error | `docker exec sentinel-orchestrator bash -c 'cd /app && alembic upgrade head'` |
| `FileNotFoundError: yolo11s.pt` | Model volume missing — `docker volume create sentinel-pipeline_yolo-models` then restart oc-worker |
| TRT export takes 4 minutes on first start | Normal — yolo11s.engine being built; will be cached on volume after |

---

### Scenario 2: Planned reboot or power cycle

All containers have `restart: unless-stopped` and come back automatically. NAS fstab uses `_netdev,nofail`.

After any restart:
```bash
df -h | grep sentinel                      # NAS mounted?
docker compose ps                          # all containers Up?
curl http://localhost:8000/api/health      # orchestrator responding?
```

Orchestrator startup recovery runs automatically — no manual intervention needed.

---

### Scenario 3: Power outage (unclean shutdown)

Same as Scenario 2. Startup recovery handles it:
1. `recover_stuck_jobs()` — stuck jobs reset and re-queued
2. `scan_ingest_missed()` — NAS files that arrived during outage are auto-submitted
3. RabbitMQ — durable queues survive; unacknowledged messages redelivered on reconnect

**No manual action required.**

---

### Scenario 4: Docker volume data loss

**Step 1 — Restore PostgreSQL:**
```bash
docker compose up -d postgres
sleep 15
docker compose exec -T postgres psql -U sentinel sentinel \
  < ~/backups/sentinel_pg_YYYYMMDD.sql
```

**Step 2 — Restore MinIO:**
```bash
docker compose up -d minio
sleep 10
mc mirror /mnt/ds-one/sentinel-snapshots-backup/ sentinel/snapshots
```

**Step 3 — Run migrations and start everything:**
```bash
docker compose up -d orchestrator
sleep 10
docker exec sentinel-orchestrator bash -c 'cd /app && alembic upgrade head'
docker volume create sentinel-pipeline_yolo-models   # if not already created
docker compose up -d
```

---

### Scenario 5: Complete host failure (new machine)

**Step 1 — Provision new host:**
Install Docker, Compose, nfs-common, NVIDIA Container Toolkit (see deployment.md Prerequisites).

**Step 2 — Mount NAS:**
```bash
sudo mkdir -p /mnt/ds-one/sentinel-ingest
echo "192.168.55.55:/volume1/FTP/sentinel-ingest /mnt/ds-one/sentinel-ingest nfs ro,defaults,_netdev,nofail 0 0" \
  | sudo tee -a /etc/fstab
sudo mount -a
```

**Step 3 — Clone repo and restore `.env`:**
```bash
git clone https://github.com/dabeckham/sentinel-pipeline.git ~/sentinel-pipeline
cd ~/sentinel-pipeline
cp /path/to/sentinel-env-backup.env .env
```

**Step 4 — Bootstrap RabbitMQ** (see Scenario 8 below — same procedure).

**Step 5 — Restore data and start stack:**
Follow Scenario 4 Steps 1–3 above.

---

### Scenario 6: Jobs stuck in dead-letter queue

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/api/dlx/counts \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

curl -s -X POST "http://localhost:8000/api/dlx/requeue?queue=dlx.motion_results&limit=500" \
  -H "Authorization: Bearer $TOKEN"
```

Valid queue names: `dlx.ingest`, `dlx.motion_results`, `dlx.oc_results`.

---

### Scenario 7: NAS unreachable

The orchestrator file watcher logs polling errors but does not crash. Existing queued jobs continue processing. When NAS comes back:
```bash
sudo mount -a
df -h | grep sentinel
```
No container restart required.

---

### Scenario 8: RabbitMQ Mnesia wipe / credential rotation

**The problem:** RabbitMQ stores users in a Mnesia database. If the container is recreated or the volume wiped, all users are gone. `RABBITMQ_DEFAULT_USER/PASS` env vars only apply on the **very first** container startup (empty Mnesia dir) — on subsequent restarts they are ignored.

**The silent trap:** `rabbitmqctl change_password <user> <pass>` silently does nothing if the user doesn't exist. Always verify:
```bash
docker exec sentinel-rabbitmq rabbitmqctl list_users
```

**Recovery — uses only env vars already inside the container (no credentials in terminal):**
```bash
docker exec sentinel-rabbitmq bash -c "
  rabbitmqctl add_user \$RABBITMQ_DEFAULT_USER \$RABBITMQ_DEFAULT_PASS &&
  rabbitmqctl set_user_tags \$RABBITMQ_DEFAULT_USER administrator &&
  rabbitmqctl set_permissions -p / \$RABBITMQ_DEFAULT_USER '.*' '.*' '.*'"
```

After recovery, restart all workers so they reconnect:
```bash
docker compose restart md-worker
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4
```

---

## Secret Rotation

### Rotate JWT secret
1. `openssl rand -hex 32` → update `JWT_SECRET_KEY` in `.env`
2. `docker compose restart orchestrator`
3. All active user sessions invalidated. Users must log in again.

### Rotate RabbitMQ password
1. `openssl rand -hex 32` → update `RABBITMQ_DEFAULT_PASS` in `.env`
2. Run the recovery command from Scenario 8 above (recreates user with new password)
3. Restart all workers:
```bash
docker compose restart md-worker
docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4
```

> **Do NOT** just update `.env` and restart the broker — the Mnesia record still has the old password.

### Rotate PostgreSQL password
```bash
docker compose exec postgres psql -U postgres \
  -c "ALTER USER sentinel PASSWORD 'NewPassword';"
# Update .env — POSTGRES_PASSWORD=NewPassword
docker compose restart orchestrator
```

### Rotate MinIO secret
1. MinIO Console → Identity → Users → sentinel → change secret key
2. Update `MINIO_SECRET_KEY` in `.env`
3. `docker compose restart orchestrator md-worker && docker compose up -d oc-worker oc-worker-2 oc-worker-3 oc-worker-4`

---

## Full-System Health Check

```bash
#!/usr/bin/env bash
echo "=== NAS Mount ==="
df -h | grep sentinel || echo "NOT MOUNTED"

echo ""
echo "=== Containers ==="
docker compose ps

echo ""
echo "=== GPU ==="
nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv

echo ""
echo "=== API Health ==="
curl -s http://localhost:8000/api/health | python3 -m json.tool

echo ""
echo "=== Pipeline Stats ==="
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token','LOGIN_FAILED'))")
curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "=== Workers ==="
curl -s http://localhost:8000/api/workers -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "=== DLX Queue Depths ==="
curl -s http://localhost:8000/api/dlx/counts -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "=== DB Ready ==="
docker compose exec postgres pg_isready -U sentinel
```

---

## Backup Checklist

Set these up immediately after first deployment:

- [ ] `.env` copied to secure off-host storage
- [ ] PostgreSQL daily cron backup configured (`~/backups/`)
- [ ] MinIO nightly mirror to NAS configured (`mc mirror`)
- [ ] NAS itself has a backup configured (Synology HyperBackup or similar)
