"""
OC Worker — Object Classification
Phase 1 stub: connects to RabbitMQ and logs readiness.
Full implementation in Phase 2.
"""
import structlog
import time
import os

log = structlog.get_logger()


def main():
    log.info("oc_worker_starting",
             rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
             queue=os.getenv("QUEUE_MOTION_RESULTS", "motion_results"),
             gpu=os.getenv("OC_USE_GPU", "false"),
             model=os.getenv("YOLO_MODEL", "yolo26s"))
    # Phase 2: connect to RabbitMQ and start consuming
    while True:
        log.info("oc_worker_waiting", status="stub — Phase 2 implementation pending")
        time.sleep(30)


if __name__ == "__main__":
    main()
