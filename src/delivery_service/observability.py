"""Structured logs deliberately omit request payloads and credentials."""

import json
import logging
import re
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("delivery_http_requests_total", "HTTP responses", ["method", "route", "status"])
LATENCY = Histogram("delivery_http_seconds", "Request latency", ["route"])
OUTBOX_PENDING = Gauge("delivery_outbox_pending", "Undelivered calculation events")
OUTBOX_AGE = Gauge("delivery_outbox_oldest_seconds", "Age of oldest undelivered event")

URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s?#]+@", re.IGNORECASE)
URL_QUERY = re.compile(r"""(\b[a-z][a-z0-9+.-]*://[^\s?#<>"']*)[?#][^\s<>"']*""", re.IGNORECASE)
CREDENTIAL = re.compile(
    r"""(\b(?:authorization|cookie|set-cookie|password|api[_-]key|[\w-]*token)["']?\s*[:=]\s*)[^\r\n]+""",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    text = URL_USERINFO.sub(r"\1***@", text)
    text = URL_QUERY.sub(r"\1", text)
    text = CREDENTIAL.sub(r"\1***", text)
    return re.sub(r"\bgsk_[A-Za-z0-9_-]+", "***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
        }
        for key in (
            "request_id",
            "method",
            "path",
            "route",
            "status",
            "status_code",
            "parcel_id",
            "event_id",
            "attempt",
            "duration_ms",
        ):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info and (error := record.exc_info[1]) is not None:
            data["exception_type"] = type(error).__name__
            data["exception_message"] = redact(str(error))
            data["traceback"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


def configure(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
