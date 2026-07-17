"""ARQ entrypoint — thin shim over ``sfmapi.server.workers.dispatcher``.

The actual lease+dispatch+heartbeat lives in
``dispatcher.execute_task`` so any queue backend (Celery, SQS, in-memory)
can drive the same code path. This module is the ARQ-specific wrapper:
``WorkerSettings`` for the ``arq`` CLI and a ``run_task(ctx, task_id)``
function with ARQ's expected signature.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.logging import configure_logging
from sfmapi.server.workers.dispatcher import WORKER_ID, execute_task

__all__ = ["WORKER_ID", "WorkerSettings", "run_task"]


async def run_task(ctx: dict, task_id: str) -> dict:
    """ARQ-shaped wrapper. ``ctx`` is unused — ARQ passes its job
    context dict here, but execute_task is queue-agnostic."""
    return await execute_task(task_id)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Any]]] = [run_task]
    on_startup = staticmethod(lambda ctx: configure_logging(get_settings().log_level))

    @staticmethod
    def get_redis_url() -> str:
        return get_settings().redis_url
