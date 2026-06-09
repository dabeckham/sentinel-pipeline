import threading
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

log = structlog.get_logger()


def _seed_admin():
    """Create a default admin user if no users exist."""
    from app.db import SessionLocal
    from app.models.user import User, UserRole
    from app.auth.password import hash_password
    import os

    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            password = os.environ.get("ADMIN_DEFAULT_PASSWORD", "changeme")
            user = User(
                username="admin",
                password_hash=hash_password(password),
                role=UserRole.admin,
                is_active=True,
            )
            db.add(user)
            db.commit()
            log.info("admin_user_seeded", username="admin",
                     password_from_env=("ADMIN_DEFAULT_PASSWORD" in os.environ))
    except Exception:
        log.exception("admin_seed_error")
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("sentinel_orchestrator_starting",
             version="0.6.0",
             rabbitmq_host=settings.rabbitmq_host,
             rabbitmq_user=settings.rabbitmq_user,
             ingest_path=settings.ingest_watch_path)

    # Seed default admin if no users exist
    _seed_admin()

    # Recover any jobs left in-flight when the process last died.
    from app.services.startup_recovery import recover_stuck_jobs
    recover_stuck_jobs()

    from app.services.event_loop import set_loop
    from app.services.watcher import start_watcher
    from app.services.result_consumer import start_result_consumer
    from app.services.health_monitor import start_health_monitor, startup_health_check

    import asyncio
    set_loop(asyncio.get_event_loop())

    # Check pipeline health BEFORE starting the watcher.  If the pipeline is
    # already backed up, the watcher stays paused and scan_ingest_missed() is
    # skipped — it will run automatically when the health monitor clears the
    # backlog and calls resume_watcher().  If healthy, start_watcher() calls
    # resume_watcher() which runs scan_ingest_missed() as part of startup.
    startup_health_check()
    start_watcher()

    consumer_thread = threading.Thread(
        target=start_result_consumer, daemon=True, name="oc-result-consumer"
    )
    consumer_thread.start()

    start_health_monitor()

    yield

    log.info("sentinel_orchestrator_stopping")
    from app.services.watcher import pause_watcher
    pause_watcher()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Sentinel Pipeline API",
        description="Distributed video analysis pipeline orchestrator",
        version="0.6.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    from app.api import health
    from app.api import auth, jobs, tracks, stats, users, config_api, ws, dlx, snapshots, metrics

    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(auth.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(tracks.router, prefix="/api")
    app.include_router(snapshots.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(config_api.router, prefix="/api")
    app.include_router(dlx.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(ws.router)  # /ws/jobs — no /api prefix

    return app


app = create_app()
