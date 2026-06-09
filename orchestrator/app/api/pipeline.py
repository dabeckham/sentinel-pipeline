"""
Pipeline health + watcher control endpoints.

GET  /api/pipeline/status   — current health state (UI fetches on page load to restore alert banner)
POST /api/pipeline/watcher/pause   — manually pause the file watcher
POST /api/pipeline/watcher/resume  — manually resume the file watcher
"""
from fastapi import APIRouter, Depends
from app.auth.deps import require_admin

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/status")
def pipeline_status():
    """Return current pipeline health and watcher state."""
    from app.services.health_monitor import get_pipeline_status
    return get_pipeline_status()


@router.post("/watcher/pause")
def watcher_pause(_=Depends(require_admin)):
    """Manually pause the file watcher. Health monitor will not auto-resume until /watcher/resume."""
    from app.services.health_monitor import manual_pause_watcher
    manual_pause_watcher()
    return {"ok": True, "watcher_paused": True}


@router.post("/watcher/resume")
def watcher_resume(_=Depends(require_admin)):
    """Manually resume the file watcher and scan for missed files."""
    from app.services.health_monitor import manual_resume_watcher
    manual_resume_watcher()
    return {"ok": True, "watcher_paused": False}
