"""
MD Worker — Motion Detection
Phase 1 stub: connects to RabbitMQ and logs readiness.
Full implementation in Phase 2.
"""
import structlog
import time
import os

log = structlog.get_logger()


def main():
    log.info("md_worker_starting",
             rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
             queue=os.getenv("QUEUE_INGEST", "ingest"))
    # Phase 2: connect to RabbitMQ and start consuming
    while True:
        log.info("md_worker_waiting", status="stub — Phase 2 implementation pending")
        time.sleep(30)


if __name__ == "__main__":
    main()
