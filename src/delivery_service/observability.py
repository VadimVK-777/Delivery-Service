"""Structured logs deliberately omit request payloads and credentials."""

import json
import logging
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("delivery_http_requests_total", "HTTP responses", ["method", "route", "status"])
LATENCY = Histogram("delivery_http_seconds", "Request latency", ["route"])
OUTBOX_PENDING = Gauge("delivery_outbox_pending", "Undelivered calculation events")
OUTBOX_AGE = Gauge("delivery_outbox_oldest_seconds", "Age of oldest undelivered event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("request_id", "parcel_id", "event_id", "attempt", "duration_ms"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        # Exception type is useful without accidentally logging a credential-bearing URL.
        if record.exc_info and record.exc_info[0]:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False)


def configure(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
