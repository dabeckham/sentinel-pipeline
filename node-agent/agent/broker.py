"""
Demand signal — current queue depths via the RabbitMQ management HTTP API.

The governor scales a worker type UP only when there is work waiting for it:
  - MD demand  = depth of the `ingest` queue
  - OC demand  = depth of the `motion_results` queue

Read-only. Fails soft (returns None) if the broker is unreachable, in which case
the governor holds its current size rather than guessing.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import json
import base64

import structlog

from agent.config import Settings

log = structlog.get_logger()


def _queue_depth(s: Settings, queue: str) -> int | None:
    vhost = urllib.parse.quote("/", safe="")
    url = f"{s.rabbitmq_mgmt_url}/api/queues/{vhost}/{queue}"
    token = base64.b64encode(f"{s.rabbitmq_user}:{s.rabbitmq_password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
        return int(data.get("messages", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("queue_depth_failed", queue=queue, error=str(exc))
        return None


def demand(s: Settings) -> dict[str, int | None]:
    """Return {'md': <ingest depth>, 'oc': <motion_results depth>} (None if unknown)."""
    return {
        "md": _queue_depth(s, s.queue_ingest),
        "oc": _queue_depth(s, s.queue_motion_results),
    }
