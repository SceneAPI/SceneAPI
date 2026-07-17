"""No-op task — used in tests to validate the runner pipeline."""

from __future__ import annotations

import time

from sceneapi.server.db.models import Task
from sceneapi.server.workers._task_io import read_extra
from sceneapi.server.workers.tasks._registry import task_handler


@task_handler("noop")
def run(task: Task) -> dict:
    sleep_for = read_extra(task, "sleep_for", 0.0)
    if sleep_for:
        time.sleep(float(sleep_for))
    return {"ok": True, "task_id": task.task_id}
