"""Structured logging, request tracing and Prometheus metrics."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

import asyncpg
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, Histogram, generate_latest

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_EMAIL = re.compile(r"([^@\s\"'<>,;:]+)@([^@\s\"'<>,;:]+\.[A-Za-z]{2,})")

REQUEST_LATENCY = Histogram(
    "inblack_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2.5, 5),
)
ORDERS_BY_STATUS = Gauge("inblack_orders", "Orders by status", ["status"])
QUEUE_DEPTH = Gauge("inblack_queue_depth", "Jobs waiting or running", ["kind"])
DEAD_LETTER = Gauge("inblack_dead_letter_jobs", "Jobs in the dead-letter queue", ["kind"])
JOB_RETRIES = Gauge("inblack_job_retries", "Retries beyond the first attempt", ["kind"])
TOPUP_CALLS = Gauge("inblack_topup_calls", "Calls to the game system", ["action", "outcome"])


def _mask(text: str) -> str:
    # A safety net: code should not log PII, and if it ever does the address is masked here.
    return _EMAIL.sub(lambda match: f"{match.group(1)[:1]}***@{match.group(2)}", text)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": _mask(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exception"] = _mask(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _refresh_gauges(pool: asyncpg.Pool) -> None:
    statuses = await pool.fetch("SELECT status, count(*) AS n FROM orders GROUP BY status")
    ORDERS_BY_STATUS.clear()
    for row in statuses:
        ORDERS_BY_STATUS.labels(row["status"]).set(row["n"])

    jobs = await pool.fetch(
        """
        SELECT kind,
               count(*) FILTER (WHERE status IN ('queued','running')) AS live,
               count(*) FILTER (WHERE status = 'dead') AS dead,
               COALESCE(sum(GREATEST(attempts - 1, 0)), 0) AS retries
        FROM jobs GROUP BY kind
        """
    )
    for row in jobs:
        QUEUE_DEPTH.labels(row["kind"]).set(row["live"])
        DEAD_LETTER.labels(row["kind"]).set(row["dead"])
        JOB_RETRIES.labels(row["kind"]).set(row["retries"])

    calls = await pool.fetch(
        "SELECT action, outcome, count(*) AS n FROM fulfillment_attempts GROUP BY action, outcome"
    )
    for row in calls:
        TOPUP_CALLS.labels(row["action"], row["outcome"]).set(row["n"])


async def render_metrics(pool: asyncpg.Pool) -> tuple[bytes, str]:
    try:
        await _refresh_gauges(pool)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
        logging.getLogger("metrics").warning("database unavailable, serving last known gauges")
    return generate_latest(), CONTENT_TYPE_LATEST
