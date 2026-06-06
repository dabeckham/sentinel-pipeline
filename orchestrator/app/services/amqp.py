import json
import threading
import time
import pika
import structlog
from app.config import get_settings

log = structlog.get_logger()


class Publisher:
    """Thread-safe RabbitMQ publisher with auto-reconnect."""

    def __init__(self):
        self._lock = threading.Lock()
        self._conn: pika.BlockingConnection | None = None
        self._channel = None

    def _connect(self):
        settings = get_settings()
        self._conn = pika.BlockingConnection(settings.rabbitmq_params())
        self._channel = self._conn.channel()

    def publish(self, queue: str, message: dict, retries: int = 3):
        with self._lock:
            for attempt in range(retries):
                try:
                    if self._conn is None or self._conn.is_closed:
                        self._connect()
                    self._channel.basic_publish(
                        exchange="",
                        routing_key=queue,
                        body=json.dumps(message),
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            content_type="application/json",
                        ),
                    )
                    return
                except Exception as exc:
                    log.warning("amqp_publish_retry", attempt=attempt + 1, error=str(exc))
                    self._conn = None
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
            log.error("amqp_publish_failed", queue=queue)
            raise RuntimeError(f"Failed to publish to {queue} after {retries} attempts")


_publisher = Publisher()


def publish(queue: str, message: dict):
    _publisher.publish(queue, message)
