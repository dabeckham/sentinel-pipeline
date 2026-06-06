import threading
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("sentinel_orchestrator_starting",
             version="0.2.0",
             rabbitmq_host=settings.rabbitmq_host,
             rabbitmq_user=settings.rabbitmq_user,
             ingest_path=settings.ingest_watch_path)

    from app.services.watcher import start_watcher
    from app.services.result_consumer import start_result_consumer

    observer = start_watcher()

    consumer_thread = threading.Thread(target=start_result_consumer, daemon=True, name="oc-result-consumer")
    consumer_thread.start()

    yield

    log.info("sentinel_orchestrator_stopping")
    observer.stop()
    observer.join(timeout=5)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Sentinel Pipeline API",
        description="Distributed video analysis pipeline orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers — added incrementally per phase
    from app.api import health
    app.include_router(health.router, prefix="/api", tags=["health"])

    return app


app = create_app()
