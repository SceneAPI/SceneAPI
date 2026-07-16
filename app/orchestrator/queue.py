"""Pluggable task queue.

The orchestrator decides what to enqueue (Task rows from a job DAG);
the Queue decides where execution happens. Three backends ship in v0:

  - ``arq``: enqueues into Redis via ARQ; the worker
    process (``app/workers/runner.py``) consumes from the same pool.
  - ``raw_redis``: LPUSHes plain task ids for the C++ bridge worker's
    BLPOP protocol.
  - ``inline``: tests and ``inline_tasks=True`` dev mode. Calls
    ``run_task`` synchronously in the current event loop. Skips Redis
    entirely.

Selection is via ``settings.queue_backend`` (or the legacy
``settings.inline_tasks=True`` shortcut, which forces ``inline``).
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from typing import Any, Protocol, runtime_checkable

from app.core.config import Settings, get_settings


@runtime_checkable
class Queue(Protocol):
    """Minimal task-queue contract.

    Backends are responsible for delivering ``run_task(task_id)`` to a
    worker. Errors during enqueue must raise; callers handle retries
    by re-submitting the job.
    """

    backend: str

    async def enqueue(self, task_id: str) -> None: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------
#  ARQ
# --------------------------------------------------------------------


class ArqQueue:
    """Enqueue into Redis via ARQ."""

    backend: str = "arq"

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self._pool: Any | None = None

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        from arq import create_pool
        from arq.connections import RedisSettings

        self._pool = await create_pool(RedisSettings.from_dsn(self.s.redis_url))
        return self._pool

    async def enqueue(self, task_id: str) -> None:
        pool = await self._ensure_pool()
        job = await pool.enqueue_job("run_task", task_id)
        if job is None:
            raise RuntimeError(f"failed to enqueue task {task_id}")

    async def health(self) -> bool:
        try:
            pool = await self._ensure_pool()
            await pool.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._pool is not None:
            with contextlib.suppress(Exception):
                await self._pool.close()
            self._pool = None


# --------------------------------------------------------------------
#  Inline (tests + dev)
# --------------------------------------------------------------------


class InlineQueue:
    """Synchronous in-process backend for tests / ``inline_tasks=True``.

    Imports ``run_task`` lazily so adapter modules (pycolmap) only
    load when a task actually fires.
    """

    backend: str = "inline"

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()

    async def enqueue(self, task_id: str) -> None:
        from app.workers.runner import run_task

        await run_task({}, task_id)

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------
#  Raw Redis (C++ bridge worker)
# --------------------------------------------------------------------


class RawRedisQueue:
    """Plain task-id LPUSH backend consumed by bridge/bridge_worker.py."""

    backend: str = "raw_redis"

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self._client: Any | None = None

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        import redis.asyncio as redis  # type: ignore

        self._client = redis.from_url(self.s.redis_url, decode_responses=True)
        return self._client

    async def enqueue(self, task_id: str) -> None:
        client = await self._ensure_client()
        await client.lpush(self.s.queue_key, task_id)

    async def health(self) -> bool:
        try:
            client = await self._ensure_client()
            await client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
            self._client = None


# --------------------------------------------------------------------
#  Factory
# --------------------------------------------------------------------

_BACKENDS: dict[str, type[Any]] = {
    "arq": ArqQueue,
    "inline": InlineQueue,
    "raw_redis": RawRedisQueue,
}
_FORCE_INLINE: ContextVar[bool] = ContextVar("sfmapi_force_inline_queue", default=False)


def force_inline_queue() -> Token[bool]:
    return _FORCE_INLINE.set(True)


def reset_inline_queue(token: Token[bool]) -> None:
    _FORCE_INLINE.reset(token)


def get_queue(settings: Settings | None = None) -> Queue:
    """Build a queue per ``settings.queue_backend`` (with a legacy
    fall-through for ``inline_tasks=True``)."""
    s = settings or get_settings()
    backend = "inline" if (_FORCE_INLINE.get() or s.inline_tasks) else s.queue_backend
    cls = _BACKENDS.get(backend)
    if cls is None:
        raise ValueError(f"unknown queue_backend={backend!r}; valid: {sorted(_BACKENDS)}")
    return cls(s)
