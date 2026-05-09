"""Worker task for backend-native extension actions."""

from __future__ import annotations

from typing import Any

from app.adapters import backend_actions
from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.paths import Paths
from app.db.models import Task
from app.workers._task_io import read_state
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler


@task_handler("backend_action")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    action_id = str(inputs["action_id"])
    project_id = str(inputs["project_id"])
    action_inputs = dict(spec.get("inputs") or {})

    workspace = (
        Paths(get_settings()).job_root(task.tenant_id, project_id, task.job_id)
        / "backend_actions"
        / task.task_id
    )
    workspace.mkdir(parents=True, exist_ok=True)

    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("backend_action")
        progress.phase_progress("backend_action", current=0, total=1)

    result = backend_actions.run_backend_action(
        action_id,
        action_inputs,
        workspace=workspace,
        progress=progress,
    )

    if progress is not None:
        progress.phase_progress("backend_action", current=1, total=1)
        progress.phase_completed("backend_action")

    backend = get_backend()
    return {
        "action_id": action_id,
        "backend": str(getattr(backend, "name", "unknown")),
        "workspace": str(workspace),
        "result": result,
    }
