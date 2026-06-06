import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    settings = get_settings()
    log.info("sentinel_orchestrator_starting", version="0.1.0")
    # TODO Phase 2: start file watcher
    # TODO Phase 2: start OC result consumer
    yield
    log.info("sentinel_orchestrator_stopping")


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
