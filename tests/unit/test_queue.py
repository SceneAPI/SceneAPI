from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


async def test_arq_queue_uses_fresh_delivery_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from sceneapi.server.orchestrator.queue import ArqQueue

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakePool:
        async def enqueue_job(self, *args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            return object()

    async def fake_pool(self: ArqQueue) -> FakePool:
        return FakePool()

    monkeypatch.setattr(ArqQueue, "_ensure_pool", fake_pool)

    await ArqQueue().enqueue("task-1")

    assert calls == [(("run_task", "task-1"), {})]


async def test_arq_queue_surfaces_enqueue_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    from sceneapi.server.orchestrator.queue import ArqQueue

    class FakePool:
        async def enqueue_job(self, *args: object, **kwargs: object) -> None:
            return None

    async def fake_pool(self: ArqQueue) -> FakePool:
        return FakePool()

    monkeypatch.setattr(ArqQueue, "_ensure_pool", fake_pool)

    with pytest.raises(RuntimeError, match="failed to enqueue task"):
        await ArqQueue().enqueue("task-1")


async def test_raw_redis_queue_pushes_plain_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.orchestrator.queue import RawRedisQueue

    settings = reset_settings_for_tests(
        queue_backend="raw_redis",
        queue_key="sfmapi:test",
    )
    calls: list[tuple[str, str]] = []

    class FakeClient:
        async def lpush(self, key: str, value: str) -> None:
            calls.append((key, value))

    async def fake_client(self: RawRedisQueue) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(RawRedisQueue, "_ensure_client", fake_client)

    await RawRedisQueue(settings).enqueue("task-1")

    assert calls == [("sfmapi:test", "task-1")]
