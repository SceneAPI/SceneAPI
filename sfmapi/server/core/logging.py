"""structlog configuration + per-job log mirror.

`configure_logging()` wires structlog with a JSONL renderer to stdout.
`bind_request_context(...)`, `bind_job_context(...)` push fields into
`structlog.contextvars` so every log record after the bind carries
`tenant_id, project_id, job_id, task_id` automatically.

`JobFileLogger` mirrors a worker's logs into `jobs/{job_id}/log.jsonl`
so per-job triage doesn't require grepping the global aggregate.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()


@contextmanager
def bind_request_context(
    *,
    request_id: str,
    tenant_id: str | None = None,
) -> Iterator[None]:
    tokens = structlog.contextvars.bind_contextvars(
        request_id=request_id,
        **({"tenant_id": tenant_id} if tenant_id else {}),
    )
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


@contextmanager
def bind_job_context(
    *,
    tenant_id: str,
    project_id: str,
    job_id: str,
    task_id: str | None = None,
    phase: str | None = None,
) -> Iterator[None]:
    fields: dict[str, Any] = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "job_id": job_id,
    }
    if task_id:
        fields["task_id"] = task_id
    if phase:
        fields["phase"] = phase
    tokens = structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


class JobFileLogger:
    """Append-only JSONL mirror of a worker's logs into the job's
    workspace dir."""

    def __init__(self, job_dir: Path) -> None:
        self.path = job_dir / "log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, level: str, event: str, **fields: Any) -> None:
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level.upper(),
            "event": event,
            **fields,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
