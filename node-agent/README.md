# Sentinel Node-Agent (Phase 1 — local self-governor)

The per-machine supervisor for Sentinel workers. It probes what the host can
contribute, then **autonomously** brings MD/OC workers on/off the clock based on
live load — never oversubscribing the machine. This is Phase 1 of the
[distributed-workers design](../docs/distributed_workers_design.md): single host,
no remote pieces. It is also the permanent fix for the session-16 thrashing
(8 workers on an 8-core box → load 48).

## How it decides

Every `AGENT_GOVERNOR_INTERVAL_S` (default 15s) the governor:

1. **Probes** load (per physical core), RAM, swap, GPU.
2. **Budgets** a shared core pool = `physical_cores − reserve_cores`, with per-type
   RAM/VRAM ceilings. MD and OC compete for the same cores.
3. **Reads demand** = queue depths (`ingest` → MD, `motion_results` → OC).
4. **Acts** on at most one decision, with cooldown + hysteresis:
   - load/core ≥ `load_high` **or** swap ≥ `swap_high_pct` → **park** one (swap is an emergency, bypasses cooldown).
   - below a type's minimum → **start** one (keep a puller alive).
   - load/core < `load_low` **and** there's demand **and** it still fits the core pool → **start** the most-wanted type.
   - else **hold**.

The core-pool check is the key guard: committed worker cores never exceed
`physical − reserve`, so the host can't thrash.

## Safety: DRY_RUN

Ships with `AGENT_DRY_RUN=true` — it logs every decision but starts/stops nothing.
Deploy in observe mode, watch the `governor_tick` logs against real load, then set
`AGENT_DRY_RUN=false` to let it actuate.

## Run

```bash
# Build + unit-test the decision logic (no host/Docker needed for the tests)
docker build -t sentinel-node-agent:latest ./node-agent
docker run --rm sentinel-node-agent:latest python -m pytest tests/ -q

# Observe-only on the orchestrator host
docker compose -f docker-compose.yml -f docker-compose.node-agent.yml up -d node-agent
docker logs -f sentinel-node-agent     # watch governor_tick decisions
```

## Key settings (all `AGENT_`-prefixed env)

| Setting | Default | Meaning |
|---|---|---|
| `AGENT_DRY_RUN` | `true` | Observe-only; no container actions |
| `AGENT_RESERVE_CORES` | `3` | Physical cores kept for OS + co-tenants (Frigate/Ollama) |
| `AGENT_LOAD_HIGH` / `AGENT_LOAD_LOW` | `0.90` / `0.65` | Park / scale-up watermarks (load per core) |
| `AGENT_SWAP_HIGH_PCT` | `25` | Swap usage that triggers emergency park |
| `AGENT_OC_MIN` / `AGENT_OC_MAX` | `1` / `8` | OC pool bounds |
| `AGENT_MD_MIN` / `AGENT_MD_MAX` | `1` / `8` | MD pool bounds |
| `AGENT_ACTION_COOLDOWN_S` | `45` | Min seconds between scale actions |
| `AGENT_OC_GPU_IDS` | `1` | Physical GPU ids OC workers are placed on |

Cost model (`AGENT_OC_COST_CORES` etc.) is calibrated from session 16 (~1.5
cores/worker) and should be refined empirically per host.

## Architecture (this package)

| Module | Role |
|---|---|
| `resources.py` | Probe CPU/RAM/swap/load (psutil) + GPU (nvidia-smi) |
| `budget.py` | Resources → shared core pool + per-type caps |
| `broker.py` | Demand signal: queue depths via RabbitMQ mgmt API |
| `policy.py` | **Pure** decision function (`decide`) — no I/O, unit-tested |
| `supervisor.py` | Start/stop/adopt worker containers (Docker SDK) |
| `governor.py` | Control loop: probe → decide → act, with cooldown |
| `main.py` | Entry point + logging + signal handling |

Workers are launched to match `docker-compose.yml` exactly; the host `.env` is
bind-mounted to `/app/.env` so the agent never reads secrets — the worker's own
settings load them.
