"""
Self-generated, persistent agent identity.

Each machine's node-agent owns a stable agent_id: generated once (UUID) and
persisted to the agent's state dir, so it survives agent restarts AND an
orchestrator DB wipe — the machine keeps its identity. At enrollment (Phase 2)
the orchestrator simply accepts whatever the agent presents, gated by auth.

Workers spawned by this agent are tagged with AGENT_ID and report it in their
lifecycle events, giving the orchestrator the broker/agent/worker hierarchy.
"""
import os
import uuid
from functools import lru_cache

import structlog

from agent.config import get_settings

log = structlog.get_logger()


@lru_cache(maxsize=1)
def get_agent_id() -> str:
    s = get_settings()
    path = os.path.join(s.agent_state_dir, "agent_id")
    try:
        if os.path.exists(path):
            existing = open(path).read().strip()
            if existing:
                return existing
        os.makedirs(s.agent_state_dir, exist_ok=True)
        new_id = f"agent-{uuid.uuid4().hex[:12]}"
        with open(path, "w") as f:
            f.write(new_id)
        log.info("agent_id_generated", agent_id=new_id, path=path)
        return new_id
    except Exception as exc:  # noqa: BLE001
        # Persisting failed (e.g. no state volume) — run with an ephemeral id
        # rather than crash. It just won't survive a restart.
        ephemeral = f"agent-{uuid.uuid4().hex[:12]}"
        log.warning("agent_id_persist_failed", error=str(exc), ephemeral=ephemeral)
        return ephemeral
