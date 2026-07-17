"""Prometheus metrics surface.

Exported from `/metrics`. Counters/histograms are module-level singletons
(prometheus_client requires this for scrape consistency). Workers and
the API both write to the same registry within their process; in a
multi-process deployment we'd add `multiprocess_dir` — out of scope
for v0.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

job_duration_seconds = Histogram(
    "sfmapi_job_duration_seconds",
    "Wall-clock duration of a Job from start to finish.",
    labelnames=("recipe", "outcome"),
    buckets=(1, 5, 30, 60, 300, 1800, 3600, 7200, 21600),
    registry=REGISTRY,
)

task_duration_seconds = Histogram(
    "sfmapi_task_duration_seconds",
    "Wall-clock duration of a Task by kind + outcome.",
    labelnames=("kind", "outcome"),
    buckets=(0.5, 1, 5, 30, 60, 300, 1800, 3600),
    registry=REGISTRY,
)

queue_depth = Gauge(
    "sfmapi_queue_depth",
    "Number of pending tasks (per kind).",
    labelnames=("kind",),
    registry=REGISTRY,
)

active_jobs = Gauge(
    "sfmapi_active_jobs",
    "Number of jobs currently in 'running' status (per tenant).",
    labelnames=("tenant_id",),
    registry=REGISTRY,
)

storage_bytes = Gauge(
    "sfmapi_storage_bytes",
    "Bytes of workspace storage used (per tenant).",
    labelnames=("tenant_id",),
    registry=REGISTRY,
)

worker_lease_age_seconds = Gauge(
    "sfmapi_worker_lease_age_seconds",
    "Time since the worker last refreshed its lease (per worker_id).",
    labelnames=("worker_id",),
    registry=REGISTRY,
)

errors_total = Counter(
    "sfmapi_errors_total",
    "Total errors classified by domain.",
    labelnames=("error_class",),
    registry=REGISTRY,
)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
