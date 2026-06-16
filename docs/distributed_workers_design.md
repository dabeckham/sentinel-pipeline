# Distributed, Self-Governing Workers — Architecture Design

*Status: DESIGN (no implementation yet) — drafted session 16, 2026-06-09*
*Control model: **autonomous node** (each machine decides its own worker count)*

---

## 1. Vision

Any machine should be able to download a single installer, join the Sentinel
collective as a **worker node**, and start contributing compute. After install
the node-agent:

1. **Probes** the resources it is willing to contribute (cores, RAM, GPUs).
2. **Enrolls** with the orchestrator and authenticates.
3. **Supervises** its own pool of workers (MD / OC), starting with a safe count.
4. **Self-governs** — monitors its own load and brings workers *on the clock* or
   *off the clock* autonomously, never oversubscribing the host.

The orchestrator coordinates *work*, not *machines*: it owns the queues and a
read-only view of who is connected. Each node owns the decision of how much to
contribute.

### Motivating incident (session 16)
This design is calibrated against a real failure. On the single co-located host
(i9-9900K, 8C/16T) we ran 4 OC + 4 MD workers + Frigate. Demand was ~15 cores on
8 physical cores → **load 48, swap 100% full, OC throughput collapsed from ~42fps
to ~2fps**, and the pipeline made near-zero forward progress. There was no
governor: nothing measured local load and parked workers. **A node-agent with a
local load governor is exactly what prevents this** — and Phase 1 below is, by
itself, the permanent fix.

---

## 2. What Already Exists (the spine we build on)

| Capability | Today | Reused for |
|---|---|---|
| Queue-pull workers (RabbitMQ) | ✅ stateless consumers | Adding a worker anywhere = another consumer |
| Worker self-registration | ✅ `WorkerEventPublisher` online/offline/heartbeat w/ `worker_type`+`device` | Node-spawned workers auto-appear |
| Self-healing registry | ✅ re-announce on 404 | Survives orchestrator restarts across nodes |
| Graceful "off the clock" | ✅ suspend/resume (`/api/workers/{id}/suspend`, 15s poll, nack+requeue) | The drain primitive the governor calls |
| Per-worker telemetry | ✅ fps, jobs, compute time | Node + collective dashboards |
| One process per worker | ✅ container = 1 consumer | Clean unit for the agent to start/stop |

### What does NOT exist
- **Node-agent / installer** — workers are static Docker Compose services on the orchestrator host.
- **Local load self-governance** — nothing reads CPU/load/RAM/GPU to scale workers.
- **A "node" abstraction** — orchestrator tracks individual workers, no machine-level concept, enrollment, or capacity view.
- **Cross-network connectivity** — workers hardcode internal hostnames (`rabbitmq`, `minio`, `orchestrator`) that only resolve on `sentinel-net`.
- **Networked data access** — OC opens the clip directly from the `/ingest` NAS mount (`video_path` in the descriptor). Off-host nodes have no NAS. **This is the hard problem.**

---

## 3. Control Model — Autonomous Node

**The node decides; the orchestrator advises.**

| Concern | Owner | Notes |
|---|---|---|
| How many workers, of which type, run on a machine | **Node-agent** | From local resources + live load |
| When to scale up / park workers | **Node-agent** | Local governor, hysteresis |
| The work queue + job durability | Orchestrator | Unchanged (RabbitMQ) |
| Demand signal (queue depth / backlog pressure) | Orchestrator | **Advisory** — input to the node, not a cap |
| Operational override (drain a specific worker/node for shutdown, security) | Orchestrator | Uses existing suspend; rare, not steady-state control |

A node scales **up** only when it has spare capacity **and** the orchestrator's
advisory demand signal says there is work. It scales **down/parks**
independently on local pressure, regardless of demand. There is no global
scheduler dictating per-node counts — the collective's total throughput is the
emergent sum of autonomous nodes.

---

## 4. Key Abstractions

- **Node** — a machine running the agent. `node_id`, capability profile, worker budget, current worker set, last check-in.
- **Node-agent** — the supervisor process/binary. Probes, enrolls, supervises, governs.
- **Capability profile** — what the node can run: GPU present → OC-capable; CPU → MD-capable (and CPU-OC fallback). Includes core/RAM/VRAM totals and a co-tenant note (e.g., "Frigate present, reserve 3 cores").
- **Contribution budget** — the slice of the machine the operator allows Sentinel to use (e.g., "up to 75% of cores, never touch the last 2"). Hard ceiling the governor respects.
- **Worker** — unchanged: a process pulling a queue. Now started/stopped by the agent, not Compose.

---

## 5. The Node-Agent

### 5.1 Resource probing (install + periodic)
- Physical vs logical cores; total/available RAM; swap presence.
- GPUs: count, model, total/free VRAM (NVML / `nvidia-smi`).
- Co-tenant detection: is Frigate/Ollama/etc. already consuming cores? (configurable reserve.)
- Output: a **capability profile** + a **worker budget** per type.

### 5.2 Worker cost model (calibrated from session 16 — refine empirically)
| Worker | CPU | RAM | VRAM | Notes |
|---|---|---|---|---|
| OC (TRT FP16 + CPU H.265 decode) | ~1.5 cores | ~1.3 GB | ~13 MB | CPU-decode bound; needs a free core to hit ~42fps |
| MD (weighted-avg motion) | ~1.5 cores | ~1–2 GB | — | CPU decode bound |

`budget(type) = floor((usable_cores − reserve) / cost_cores(type))`, then bounded
by RAM and VRAM. **Hard rule learned the hard way: never let total worker cores
exceed physical cores minus reserve.** On the incident host (8 physical, reserve
3 for Frigate) the OC budget would have been ~3, not the 4+4 that thrashed it.

### 5.3 Local load governor (the heart)
A control loop (e.g., every 10–15s) reading: 1/5-min load average, per-core CPU%,
RAM, **swap pressure**, GPU util/VRAM, optional temperature.

- **Scale up** one worker when: load < low-watermark for N cycles **AND** budget
  not exhausted **AND** orchestrator demand signal > 0.
- **Park** (off the clock) one worker when: load > high-watermark, OR swap
  growing, OR thermal limit. Parking = existing **suspend** (graceful drain),
  then stop the process if pressure persists.
- **Hysteresis + cooldown** to prevent flapping (separate up/down watermarks,
  minimum dwell between actions).
- The governor never exceeds the operator's contribution budget.

### 5.4 Worker supervision
- Start workers as local containers (baseline) or subprocesses; restart on crash.
- Graceful SIGTERM drain already supported by workers.
- **Version pinning:** the agent runs the worker image/version the orchestrator
  advertises — carries forward the session-15 lesson (one shared image, no skew).
  Protocol/version mismatch ⇒ node refuses to start workers and reports it.

### 5.5 Check-in
- Node-level heartbeat to the orchestrator: capability profile, budget, running
  worker count by type, current load, headroom, agent/worker versions.
- Reuses the existing worker-heartbeat transport; add a `node_id` envelope.

---

## 6. Orchestrator Changes

- **Node registry** (new): `POST /api/nodes/register` (enroll → `node_id` + scoped
  creds + endpoints), `POST /api/nodes/{id}/checkin`, `GET /api/nodes`.
- **Worker↔node link**: extend the existing registry/heartbeat to carry `node_id`
  (heartbeats already carry `worker_type`+`device`).
- **Advisory demand endpoint**: expose queue depth / backlog pressure (the health
  monitor already computes this) so nodes scale up only when there is work.
- **UI**: a **Nodes panel** (machine, capability, running workers, load, headroom)
  above the existing worker panel; workers nest under their node.
- Authority stays advisory per §3; the existing per-worker suspend remains for
  operational overrides.

---

## 7. Connectivity & Security (Phase 2)

Remote nodes must reach the broker, object storage, and orchestrator API.
- **Recommended baseline: an overlay network** (WireGuard / Tailscale) so off-LAN
  nodes get stable, encrypted reachability without exposing RabbitMQ/MinIO to the
  internet. Lowest-risk first step.
- **AuthN/Z:** enrollment bootstrap token → per-node **scoped** credentials
  (dedicated RabbitMQ user with queue-scoped perms; scoped MinIO keys). Never ship
  the master creds to nodes. Rotate per node; revoke on deregister.
- **TLS** on every off-LAN hop (AMQPS, HTTPS, MinIO TLS).
- Later option: a public, TLS-terminated gateway/relay instead of an overlay, if
  zero-config public participation is ever wanted.

---

## 8. Data Plane (Phase 3 — the gating problem)

**Today:** OC reads `/ingest/...` directly from the NAS. Off-host nodes can't.

**Target:** clips addressable over the network. The descriptor becomes
**transport-agnostic** and carries *both*:
```jsonc
{
  "job_id": 1234,
  "video_path": "/ingest/ll-driveway/.../clip.mp4",   // local fast-path hint
  "fetch_url":  "https://obj/clips/<key>?<presigned>",  // network fallback
  "motion_frames": [12,13,14], "video_fps": 19.97, ...
}
```
A worker uses `video_path` if the file exists locally (co-located nodes — current
fast path, zero change), else fetches `fetch_url`. **The same descriptor works
both co-located and remote** — incremental, no flag day.

- **Source of `fetch_url`:** ingest uploads each clip to object storage (MinIO) and
  the orchestrator presigns per job. Decouples workers from the NAS.
- **Keep whole-clip fetch + worker-side decode** (don't pre-extract frames on the
  orchestrator) — the whole point is to distribute decode/inference load. Remote
  nodes cache the fetched clip for the duration of the job.
- **Locality-aware optimization (later):** prefer routing NAS-resident clips to
  co-located nodes; only ship to remote nodes when local is saturated.
- **Cost note:** clips are large; egress/storage is the real price of going remote.
  Quantify before committing remote nodes to production.

---

## 9. The Installer (Phase 4)

- **Packaging decision (open):** (a) single static binary agent (Go/Rust) that
  manages a container runtime — works on machines without preinstalled Docker, or
  (b) require Docker and ship a compose/agent bundle — simplest, reuses existing
  images. Recommend starting with (b) for our own machines, (a) for "any machine."
- **Bootstrap:** `download → run with <orchestrator_url> <enroll_token> →` agent
  probes, enrolls, pulls the pinned worker image + model, starts contributing.
- **Self-update:** agent tracks the orchestrator-advertised version and updates
  workers to match (no skew).
- **Cross-platform:** Linux first (matches current stack); Windows/macOS later via
  the container path or native binary.

---

## 10. Phasing

| Phase | Deliverable | Needs remote? | Notes |
|---|---|---|---|
| **1. Local self-governor** | Node-agent on the orchestrator host: probe, budget, load-governed scale up/down of *local* workers via suspend + start/stop | No | **Also the permanent fix for the session-16 thrashing.** Highest leverage, lowest risk. |
| **2. Node enrollment + transport** | Node registry, enrollment + scoped creds, overlay network, Nodes UI | Yes | Workers can run on a *second* machine on the overlay |
| **3. Networked data plane** | Clip → object storage; transport-agnostic descriptor; worker fetch+cache | Yes | Unlocks truly remote/off-NAS nodes |
| **4. Installer** | Downloadable agent, bootstrap enroll, self-update | Yes | "Any machine can join" |

Each phase is independently shippable and useful. Phase 1 pays for itself
immediately; later phases are additive.

---

## 11. Open Decisions
1. **Worker runtime on a node:** local containers vs subprocesses (Phase 1).
2. **Installer packaging:** static binary vs Docker-required (Phase 4).
3. **Transport:** overlay network vs public TLS gateway (Phase 2).
4. **Data plane:** confirm object-store-fetch as the mechanism; measure egress (Phase 3).
5. **Cost-model calibration:** validate the per-worker core/RAM/VRAM costs and watermarks empirically on real hosts.

## 12. Risks
- **Flapping** — mitigated by hysteresis + cooldown.
- **Egress/storage cost** of shipping clips to remote nodes — quantify in Phase 3.
- **Broker/storage exposure** — mitigated by overlay + scoped creds + TLS.
- **Version skew across nodes** — agent pins to orchestrator-advertised version (session-15 lesson).
- **Autonomy vs global demand** — acceptable by design (chosen model); orchestrator's advisory demand signal keeps nodes from scaling up into no work.
