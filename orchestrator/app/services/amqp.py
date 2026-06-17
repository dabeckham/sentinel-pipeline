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


def purge_queue(queue: str) -> int:
    """Purge all messages from a queue. Returns message count purged."""
    settings = get_settings()
    try:
        conn = pika.BlockingConnection(settings.rabbitmq_params())
        ch = conn.channel()
        result = ch.queue_purge(queue)
        conn.close()
        purged = result.method.message_count
        log.info("amqp_queue_purged", queue=queue, messages=purged)
        return purged
    except Exception as exc:
        log.error("amqp_purge_failed", queue=queue, error=str(exc))
        return 0


def declare_durable(queue: str) -> None:
    """Idempotently declare a durable queue so publishes aren't silently dropped
    when no consumer has declared it yet. Must match the consumer's durable=true."""
    settings = get_settings()
    conn = pika.BlockingConnection(settings.rabbitmq_params())
    try:
        conn.channel().queue_declare(queue=queue, durable=True)
    finally:
        conn.close()
