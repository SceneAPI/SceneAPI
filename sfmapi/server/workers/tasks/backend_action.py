"""Worker task for backend-native extension actions."""

from __future__ import annotations

from typing import Any

from sfmapi.server.adapters import backend_actions
from sfmapi.server.core.config import get_settings
from sfmapi.server.core.paths import Paths
from sfmapi.server.db.models import Task
from sfmapi.server.workers._task_io import read_state
from sfmapi.server.workers.backend_resolver import backend_for_stage
from sfmapi.server.workers.progress import get_progress_reporter
from sfmapi.server.workers.tasks._registry import task_handler


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

    backend = backend_for_stage(spec)
    result = backend_actions.run_backend_action(
        action_id,
        action_inputs,
        workspace=workspace,
        progress=progress,
        backend=backend,
    )

    if progress is not None:
        progress.phase_progress("backend_action", current=1, total=1)
        progress.phase_completed("backend_action")

    return {
        "action_id": action_id,
        "backend": str(getattr(backend, "name", "unknown")),
        "workspace": str(workspace),
        "result": result,
    }
