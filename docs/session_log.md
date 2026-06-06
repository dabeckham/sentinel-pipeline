# Session Log

---

## Session 1 — 2026-06-05

### Work Completed
- Created `docs/architecture_outline.md` (v0.1): full system architecture for the distributed video analysis pipeline.

### Summary
Drafted the initial architecture outline for a Docker-based distributed video analysis system. The outline covers: Orchestrator service, MD Worker, OC Worker, RabbitMQ message broker, PostgreSQL database, MinIO object storage, and a React UI. Includes queue design, data flow, Docker Compose deployment sketch, open decision questions, proposed build phases, and technology stack.

### Upcoming Tasks
- Review outline with user and resolve open questions (Q1–Q10)
- Finalize technology choices and phase priorities
- Begin coding once spec is approved (SSH access to Docker host TBD)

---

## Session 1 (continued) — 2026-06-05

### Work Completed
- Updated `docs/architecture_outline.md` to v0.2: resolved all 10 open design questions, added RBAC auth design, LAN trust mode, FTP ingestion context, YOLO26 (latest model), ByteTrack explanation, queue DLX design, full DB schema, MinIO layout, and 6 build phases.
- Created `docs/github_setup_guide.md`: step-by-step GitHub repo + project board setup guide, full issue list by phase, labels/milestones plan, and git workflow primer for non-git users.
- Created `docs/repo_structure.md`: full repository folder layout for all services (orchestrator, md-worker, oc-worker, ui, infra, docs, tests).

### Decisions Locked
- Message broker: RabbitMQ
- MD algorithm: MOG2 background subtraction
- OC model: YOLO26 (Ultralytics, Jan 2026)
- Object tracker: ByteTrack
- Auth: Multi-user RBAC (admin/operator/viewer) + toggleable LAN trust mode
- Storage: MinIO (S3-compatible)
- Input: FTP file-first; RTSP deferred to Phase 6

### Upcoming Tasks
- User to create GitHub repository and project board (see github_setup_guide.md)
- Begin Phase 1 coding once repo is set up and SSH access to Docker host is provided

---

## Session 1 (continued) — 2026-06-05

### Work Completed
- Repo named: `sentinel-pipeline` (github.com/dabeckham/sentinel-pipeline)
- Created `README.md`: project overview, architecture table, feature list, phase tracker, quick start stub
- Created `.env.example`: all environment variables with comments for orchestrator, RabbitMQ, PostgreSQL, MinIO, MD worker, OC worker, UI, and ports
- Updated `docs/github_setup_guide.md` with actual repo name and clone URL

### Upcoming Tasks
- User creates GitHub repo at github.com/dabeckham/sentinel-pipeline
- User creates project board, labels, and milestones per github_setup_guide.md
- Begin Phase 1 once repo exists and SSH Docker host access is provided

---
