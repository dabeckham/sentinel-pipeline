# Sentinel Pipeline — Disaster Recovery
*v0.5.0 — Last updated: 2026-06-06*

---

## What Needs to Be Protected

| Data | Location | Criticality | Notes |
|---|---|---|---|
| `.env` | `~/sentinel-pipeline/.env` on host | **CRITICAL** | All secrets; if lost, all services fail. Cannot be regenerated — existing DB/MinIO data becomes inaccessible. |
| PostgreSQL | Docker volume `sentinel_postgres-data` | **Critical** | All job, track, detection metadata. |
| MinIO snapshots & crops | Docker volume `sentinel_minio-data` | **High** | Frame snapshots. Source videos still on NAS and can be reprocessed. |
| RabbitMQ state | Docker volume `sentinel_rabbitmq-data` | **Low** | In-flight messages only. Queues auto-recreate on start; lost jobs auto-recover via startup scan. |
| Source videos | NAS `/volume1/FTP/sentinel-ingest` | **High** | Managed by Synology — configure NAS-side backup separately. |
| Code | GitHub `dabeckham/sentinel-pipeline` | **None** | Always available from git. |

---

## Backup Procedures

### 1. Back Up `.env` — Do This Now

The `.env` file is **not in git** and contains all secrets. If the host dies without a backup, you cannot decrypt or access the PostgreSQL data with a fresh secret.

```bash
# Copy to a secure off-host location
scp dabeckham@192.168.55.10:~/sentinel-pipeline/.env ~/sentinel-env-backup.env
```

Store it in a password manager, encrypted USB, or private vault. Never store it in the same place as the host.

**Required fields to keep:**
- `JWT_SECRET_KEY`
- `RABBITMQ_PASSWORD`
- `POSTGRES_PASSWORD`
- `MINIO_SECRET_KEY` / `MINIO_ACCESS_KEY`

---

### 2. Back Up PostgreSQL

#### Manual snapshot
```bash
# Run from the Docker host
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

#### Automated daily backup (set this up now)
```bash
# Create backup dir
mkdir -p ~/backups

# Add to crontab (crontab -e):
0 3 * * * cd ~/sentinel-pipeline && \
  docker compose exec -T postgres pg_dump -U sentinel sentinel \
  > ~/backups/sentinel_pg_$(date +\%Y\%m\%d).sql && \
  find ~/backups -name "sentinel_pg_*.sql" -mtime +30 -delete
```

Keeps 30 days of daily snapshots. Runs at 3 AM.

---

### 3. Back Up MinIO Snapshots

#### Option A — Mirror to NAS (recommended)
```bash
# Install mc (MinIO client) once
wget https://dl.min.io/client/mc/release/linux-amd64/mc -O /usr/local/bin/mc
chmod +x /usr/local/bin/mc

# Configure alias (use values from .env)
mc alias set sentinel http://localhost:9000 $MINIO_ACCESS_KEY $MINIO_SECRET_KEY

# Mirror snapshot bucket to NAS
mc mirror sentinel/snapshots /mnt/ds-one/sentinel-snapshots-backup/
```

Add to crontab for nightly sync (runs at 2 AM):
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

The restart policy (`restart: unless-stopped`) handles this automatically. Docker restarts the container within seconds.

If a container is crash-looping:
```bash
docker compose logs orchestrator    # read the error
docker compose restart orchestrator
docker compose up -d --force-recreate orchestrator
```

Common causes and fixes:

| Error | Fix |
|---|---|
| `admin_seed_error` / bcrypt fail | Check bcrypt is pinned to 3.2.2 in requirements.txt |
| DB connection refused | Postgres not ready yet — wait 15s and retry |
| `socket.gaierror: Temporary failure in name resolution` | oc-worker on wrong network — must start with both compose files |
| `ACCESS_REFUSED - Login was refused` | oc-worker missing env vars — must start with both compose files |
| Alembic migration error | `docker compose exec orchestrator alembic upgrade head` |

---

### Scenario 2: Planned reboot or power cycle

All containers have `restart: unless-stopped` and will come back automatically. The NAS fstab entry uses `_netdev,nofail` so it mounts before Docker starts.

After any restart, verify:
```bash
df -h | grep sentinel                      # NAS mounted?
docker compose ps                          # all containers Up?
curl http://localhost:8000/api/health      # orchestrator responding?
```

The orchestrator's startup recovery routines run automatically:
- Any jobs stuck in `md_processing`/`oc_processing` are reset and re-queued.
- Any video files in `/ingest` that arrived while the system was down are detected and submitted.

No manual intervention required after a clean reboot.

---

### Scenario 3: Power outage (unclean shutdown)

Same as Scenario 2. The startup recovery handles it:

1. **Stuck jobs** (`recover_stuck_jobs`) — jobs in any in-progress state are reset to `queued` and re-published to the ingest queue. Workers pick them up fresh.
2. **Missed ingest files** (`scan_ingest_missed`) — any files on NFS that arrived during the outage are auto-submitted.
3. **RabbitMQ** — durable queues survive a clean shutdown. On unclean shutdown, any unacknowledged messages (mid-processing) are redelivered automatically by RabbitMQ when the consumer reconnects. Combined with the stuck-job recovery, files process exactly once after recovery.

**No manual action required** after a power outage.

---

### Scenario 4: Docker volume data loss (accidental `docker compose down -v`)

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
# From NAS mirror (Option A):
mc mirror /mnt/ds-one/sentinel-snapshots-backup/ sentinel/snapshots
# Or from tarball (Option B):
docker run --rm \
  -v sentinel_minio-data:/data \
  -v ~/backups:/backup \
  alpine tar xzf /backup/minio_YYYYMMDD.tar.gz -C /
```

**Step 3 — Start everything:**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Any detections processed after the last DB backup will be re-submitted automatically by the startup ingest scan and reprocessed from the NAS source files.

---

### Scenario 5: Complete host failure (new machine)

**Step 1 — Provision new host:**
```bash
# Install Docker, Compose, nfs-common, NVIDIA Container Toolkit
# (see deployment.md Prerequisites)
```

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
cp /path/to/sentinel-env-backup.env .env   # restore from secure storage
```

**Step 4 — Bootstrap RabbitMQ:**
```bash
docker compose up -d rabbitmq
sleep 15
RMQPASS=$(grep RABBITMQ_PASSWORD .env | cut -d= -f2)
docker exec sentinel-rabbitmq rabbitmqctl add_user sentinel "$RMQPASS"
docker exec sentinel-rabbitmq rabbitmqctl set_user_tags sentinel administrator
docker exec sentinel-rabbitmq rabbitmqctl set_permissions -p / sentinel ".*" ".*" ".*"
```

**Step 5 — Restore data:**
Follow Scenario 4 Steps 1–2 above.

**Step 6 — Start stack:**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

---

### Scenario 6: Jobs stuck in dead-letter queue

If messages end up in `dlx.ingest`, `dlx.motion_results`, or `dlx.oc_results` (visible in RabbitMQ management UI or via API):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Check all DLX queue depths
curl -s http://localhost:8000/api/dlx/counts \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Requeue from a specific DLX (adjust queue name as needed)
curl -s -X POST "http://localhost:8000/api/dlx/requeue?queue=dlx.motion_results&limit=500" \
  -H "Authorization: Bearer $TOKEN"
```

Valid queue names: `dlx.ingest`, `dlx.motion_results`, `dlx.oc_results`.

---

### Scenario 7: NAS unreachable

The orchestrator file watcher will log polling errors but will not crash. Existing queued jobs continue processing normally (they read from `/ingest` which is the NFS mount).

When NAS comes back:
```bash
sudo mount -a    # remount if needed
df -h | grep sentinel   # confirm
```

No container restart required. The startup ingest scan will pick up any files that arrived during the outage on next orchestrator restart.

---

## Secret Rotation

### Rotate JWT secret
1. Generate new secret: `openssl rand -hex 32`
2. Update `JWT_SECRET_KEY` in `.env`
3. `docker compose restart orchestrator`
4. All active user sessions are invalidated. Users must log in again.

### Rotate RabbitMQ password
```bash
# Set new password in RabbitMQ
docker exec sentinel-rabbitmq rabbitmqctl change_password sentinel "NewPassword"
# Update .env, then restart all consumers
sed -i 's/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=NewPassword/' .env
docker compose restart orchestrator md-worker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker
```

### Rotate PostgreSQL password
```bash
docker compose exec postgres psql -U postgres \
  -c "ALTER USER sentinel PASSWORD 'NewPassword';"
sed -i 's/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=NewPassword/' .env
docker compose restart orchestrator
```

### Rotate MinIO secret
1. Log in to MinIO Console → http://192.168.55.10:9001 → Identity → Users → sentinel → change secret key
2. Update `MINIO_SECRET_KEY` in `.env`
3. `docker compose restart orchestrator md-worker && docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d oc-worker`

---

## Full-System Health Check

Run after any recovery to confirm everything is working end-to-end:

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
echo "=== DLX Queue Depths ==="
curl -s http://localhost:8000/api/dlx/counts -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "=== DB Ready ==="
docker compose exec postgres pg_isready -U sentinel
```

**Expected healthy state:**
- All containers: `Up (healthy)` or `Up`
- `/api/health`: `{"status":"ok","version":"0.5.0",...}`
- `jobs_processing` and `jobs_queued` drain to 0 over time as backlog clears
- All `dlx.*` counts: `0`

---

## Backup Checklist

Set these up immediately after first deployment:

- [ ] `.env` copied to secure off-host storage
- [ ] PostgreSQL daily cron backup configured (`~/backups/`)
- [ ] MinIO nightly mirror to NAS configured (`mc mirror`)
- [ ] NAS itself has a backup configured (Synology HyperBackup or similar)
