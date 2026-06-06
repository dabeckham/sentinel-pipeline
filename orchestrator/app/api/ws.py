"""WebSocket endpoint for real-time job status updates."""
import asyncio
import json
from typing import Set

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = structlog.get_logger()
router = APIRouter(tags=["websocket"])

# Global connection set — all connected clients receive broadcasts
_connections: Set[WebSocket] = set()


async def broadcast(event: dict):
    """Broadcast a JSON event to all connected WebSocket clients."""
    if not _connections:
        return
    message = json.dumps(event)
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


@router.websocket("/ws/jobs")
async def job_status_ws(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    log.info("ws_client_connected", total=len(_connections))
    try:
        # Send a welcome ping
        await websocket.send_text(json.dumps({"type": "connected", "clients": len(_connections)}))
        # Keep alive — echo any pings from client
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        log.info("ws_client_disconnected", total=len(_connections))
