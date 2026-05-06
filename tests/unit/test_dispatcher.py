"""Worker dispatcher (queue-agnostic execute_task)."""

from __future__ import annotations

import pytest

from app.workers.dispatcher import get_handlers
from app.workers.runner import WORKER_ID, run_task

pytestmark = pytest.mark.unit


def test_handler_registry_has_all_known_kinds() -> None:
    handlers = get_handlers()
    expected = {
        "noop",
        "extract",
        "match",
        "verify",
        "map",
        "ba",
        "triangulate",
        "relocalize",
        "pgo",
        "export",
        "vlad_index",
        "localize",
        "georegister",
        "to_cubemap",
        "dense",
        "render_cubemap",
        "mesh",
        "merge_recons",
        "video_frames",
        "kapture_import",
    }
    assert expected.issubset(handlers.keys())


def test_runner_run_task_delegates_to_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ARQ shim in runner.py must call dispatcher.execute_task —
    if it ever stops doing that, the queue plugins won't actually run
    anything."""
    seen: list[str] = []

    async def fake_execute(task_id: str) -> dict:
        seen.append(task_id)
        return {"status": "fake"}

    import app.workers.runner as runner_mod

    monkeypatch.setattr(runner_mod, "execute_task", fake_execute)

    import asyncio

    result = asyncio.run(run_task({"unused": "ctx"}, "t_dispatcher_test"))
    assert seen == ["t_dispatcher_test"]
    assert result == {"status": "fake"}


def test_worker_id_is_set() -> None:
    assert WORKER_ID
    assert isinstance(WORKER_ID, str)
