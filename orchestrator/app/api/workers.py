"""Workers API — suspend/resume and worker state listing."""
from fastapi import APIRouter, HTTPException

from fastapi import Depends
from app.auth.deps import require_admin

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("")
def list_workers(_user=Depends(require_admin)):
    """Return all known workers with their current state and stats."""
    from app.services import worker_registry
    return worker_registry.get_all()


@router.post("/{worker_id}/suspend")
def suspend_worker(worker_id: str, _user=Depends(require_admin)):
    """
    Suspend a worker — it will nack+requeue any new jobs it picks up.
    Jobs in progress are NOT interrupted; suspension takes effect after
    the current job completes (within one heartbeat interval, ~15s).
    """
    from app.services import worker_registry
    all_workers = {w["worker_id"] for w in worker_registry.get_all()}
    if worker_id not in all_workers:
        raise HTTPException(status_code=404, detail="Worker not found")
    worker_registry.suspend(worker_id)
    return {"worker_id": worker_id, "suspended": True}


@router.post("/{worker_id}/resume")
def resume_worker(worker_id: str, _user=Depends(require_admin)):
    """Resume a suspended worker — it will start accepting new jobs again."""
    from app.services import worker_registry
    all_workers = {w["worker_id"] for w in worker_registry.get_all()}
    if worker_id not in all_workers:
        raise HTTPException(status_code=404, detail="Worker not found")
    worker_registry.resume(worker_id)
    return {"worker_id": worker_id, "suspended": False}
