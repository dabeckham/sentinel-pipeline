"""
Sentinel node-agent entry point.

Phase 1: the local self-governor. Probes this machine, decides how many MD/OC
workers it can run, and brings them on/off the clock based on live load.

Run (observe-only first): AGENT_DRY_RUN=true python -m agent.main
"""
import logging
import signal
import sys

import structlog

from agent.config import get_settings
from agent.governor import Governor


def _configure_logging():
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def main():
    _configure_logging()
    log = structlog.get_logger()
    s = get_settings()

    # Clean shutdown — leave workers running (they keep pulling); the agent just
    # stops governing. A future agent instance adopts them via the managed label.
    def _bye(*_):
        log.info("node_agent_stopping", node=s.node_name)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    from agent.identity import get_agent_id
    log.info("node_agent_starting", node=s.node_name, agent_id=get_agent_id(), dry_run=s.dry_run)
    Governor(s).run()


if __name__ == "__main__":
    main()
