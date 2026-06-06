# GitHub Setup Guide
*For the Distributed Video Analysis System*

---

## What We'll Set Up

1. A **GitHub repository** — stores all the code, docs, and config
2. A **GitHub Project board** — tracks issues (tasks) like a Kanban board
3. **Labels and Milestones** — organize work by phase and type

You don't need to know git deeply yet. We'll work through it together one step at a time.

---

## Step 1: Create a GitHub Account (if you don't have one)

Go to https://github.com and sign up if needed. A free account is sufficient.

---

## Step 2: Create the Repository

1. Log in to GitHub
2. Click the **+** button in the top-right corner → **New repository**
3. Fill in:
   - **Repository name:** `sentinel-pipeline`
   - **Description:** `Distributed video ingestion, motion detection, and object classification pipeline`
   - **Visibility:** Choose **Private** (recommended — keeps your system private)
   - ✅ Check **Add a README file**
   - **Add .gitignore:** choose `Python` from the dropdown
   - **License:** MIT (optional, but good practice)
4. Click **Create repository**

---

## Step 3: Create a GitHub Project Board

1. Go to your repository page
2. Click the **Projects** tab at the top
3. Click **Link a project** → **New project**
4. Choose **Board** view (Kanban style)
5. Name it: `Video Analysis Pipeline`
6. Click **Create project**

### Columns to create in the board:
- **Backlog** — everything not yet started
- **In Progress** — actively being worked on
- **In Review** — waiting for review/testing
- **Done** — completed

---

## Step 4: Create Labels

Labels categorize issues by type. Go to your repo → **Issues** tab → **Labels** → **New label**

Create these labels:

| Label | Color | Use for |
|---|---|---|
| `component: orchestrator` | `#0075ca` | Orchestrator service work |
| `component: md-worker` | `#e4e669` | Motion detection worker |
| `component: oc-worker` | `#d93f0b` | Object classification worker |
| `component: ui` | `#0e8a16` | Browser UI |
| `component: infra` | `#6f42c1` | Docker, RabbitMQ, DB, MinIO setup |
| `component: auth` | `#b60205` | Auth and user management |
| `phase: 1` through `phase: 6` | (your choice) | Build phase |
| `priority: high` | `#ee0701` | Must-have |
| `priority: medium` | `#fbca04` | Important but not blocking |
| `type: feature` | `#84b6eb` | New capability |
| `type: bug` | `#fc2929` | Bug fix |
| `type: docs` | `#cfd3d7` | Documentation |

---

## Step 5: Create Milestones

Milestones = our build phases. Go to **Issues** → **Milestones** → **New milestone**

| Milestone | Description |
|---|---|
| Phase 1: Infrastructure Skeleton | Docker Compose, DB schema, RabbitMQ, MinIO |
| Phase 2: Core Pipeline | File watcher → MD → OC → DB |
| Phase 3: Auth & API | JWT auth, REST endpoints, WebSocket |
| Phase 4: Browser UI | React app, all pages |
| Phase 5: Hardening | DLQ, retry, logging, tests |
| Phase 6: RTSP Streams | Future live stream support |

---

## Step 6: Create Issues (from Architecture Outline)

Here are the initial issues to create, organized by phase. For each one:
- Go to **Issues** → **New issue**
- Add the title, description, label, and milestone listed below

### Phase 1 Issues

| Title | Labels | Milestone |
|---|---|---|
| Set up Docker Compose skeleton with all services | `component: infra`, `phase: 1` | Phase 1 |
| Define PostgreSQL schema and Alembic migrations | `component: orchestrator`, `phase: 1` | Phase 1 |
| Configure RabbitMQ queues and dead-letter exchanges | `component: infra`, `phase: 1` | Phase 1 |
| Initialize MinIO buckets and access policies | `component: infra`, `phase: 1` | Phase 1 |
| Create Orchestrator FastAPI stub with health endpoint | `component: orchestrator`, `phase: 1` | Phase 1 |

### Phase 2 Issues

| Title | Labels | Milestone |
|---|---|---|
| Implement FTP path watcher and ingest queue publisher | `component: orchestrator`, `phase: 2` | Phase 2 |
| Build MD Worker: MOG2 motion detection | `component: md-worker`, `phase: 2` | Phase 2 |
| Build OC Worker: YOLO26 inference + ByteTrack | `component: oc-worker`, `phase: 2` | Phase 2 |
| Implement OC result consumer and DB writer | `component: orchestrator`, `phase: 2` | Phase 2 |
| Implement MinIO snapshot storage | `component: orchestrator`, `phase: 2` | Phase 2 |

### Phase 3 Issues

| Title | Labels | Milestone |
|---|---|---|
| Implement JWT auth and role middleware | `component: auth`, `phase: 3` | Phase 3 |
| Implement LAN trust mode middleware | `component: auth`, `phase: 3` | Phase 3 |
| Build REST API endpoints (jobs, tracks, detections) | `component: orchestrator`, `phase: 3` | Phase 3 |
| Build REST API endpoints (workers, config, users) | `component: orchestrator`, `phase: 3` | Phase 3 |
| Implement WebSocket status feed | `component: orchestrator`, `phase: 3` | Phase 3 |

### Phase 4 Issues

| Title | Labels | Milestone |
|---|---|---|
| Scaffold React app (Vite + Tailwind + React Query) | `component: ui`, `phase: 4` | Phase 4 |
| Build Login page and role-gated routes | `component: ui`, `component: auth`, `phase: 4` | Phase 4 |
| Build Ingest page | `component: ui`, `phase: 4` | Phase 4 |
| Build Pipeline Status page (live queue + workers) | `component: ui`, `phase: 4` | Phase 4 |
| Build Results / Review page (snapshots + frame viewer) | `component: ui`, `phase: 4` | Phase 4 |
| Build Configuration page (workers, broker, storage) | `component: ui`, `phase: 4` | Phase 4 |
| Build User Management page (admin only) | `component: ui`, `component: auth`, `phase: 4` | Phase 4 |

### Phase 5 Issues

| Title | Labels | Milestone |
|---|---|---|
| Implement dead-letter queue handler and retry logic | `component: infra`, `phase: 5` | Phase 5 |
| Add job deduplication via content hash | `component: orchestrator`, `phase: 5` | Phase 5 |
| Graceful worker shutdown and job requeue | `component: md-worker`, `component: oc-worker`, `phase: 5` | Phase 5 |
| Add structured JSON logging | `component: infra`, `phase: 5` | Phase 5 |
| Write integration test suite | `type: docs`, `phase: 5` | Phase 5 |

---

## Step 7: Install Git on Your Computer (if not installed)

1. Go to https://git-scm.com/downloads
2. Download and install for your OS
3. Open a terminal and run:
   ```
   git config --global user.name "Your Name"
   git config --global user.email "your@email.com"
   ```

---

## Step 8: Clone the Repository Locally

1. On your GitHub repo page, click the green **Code** button
2. Copy the HTTPS URL — it will be `https://github.com/dabeckham/sentinel-pipeline.git`
3. In a terminal, navigate to where you want the project folder:
   ```
   cd C:\Users\Don\
   git clone https://github.com/dabeckham/sentinel-pipeline.git
   cd sentinel-pipeline
   ```

---

## Going Forward: Our GitHub Workflow

Each time we work on a feature:
1. **We'll create/reference a GitHub issue** for what we're building
2. **I'll write the code** to your workspace folder
3. **You commit and push** with a few simple commands I'll give you each time:
   ```
   git add .
   git commit -m "Brief description of what changed"
   git push
   ```

That's all you need to know for now. I'll guide you through each git command as we go.

---

*End of GitHub Setup Guide*
