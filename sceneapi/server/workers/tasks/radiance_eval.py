"""Radiance evaluation task."""

from __future__ import annotations

from typing import Any

from sceneapi.server.core.errors import ValidationError
from sceneapi.server.db.models import Task
from sceneapi.server.services import radiance_service
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.tasks._registry import task_handler
from sceneapi.server.workers.tasks.radiance_train import (
    _run_container_service_provider,
    _stub_metrics,
    _task_radiance_evaluation_id,
)


async def _on_status(session: Any, task: Task, status: str) -> None:
    """Roll the RadianceEvaluation's status up with the task's."""
    evaluation_id = _task_radiance_evaluation_id(task)
    if evaluation_id is None:
        return
    await radiance_service.mark_radiance_evaluation_status(
        session,
        tenant_id=task.tenant_id,
        evaluation_id=evaluation_id,
        status=status,
    )


async def _on_success(session: Any, task: Task, outputs: dict[str, Any]) -> None:
    """Persist evaluation metrics + artifacts."""
    result = outputs or {}
    evaluation_id = _task_radiance_evaluation_id(task)
    if evaluation_id is None:
        return
    await radiance_service.record_radiance_evaluation_result(
        session,
        tenant_id=task.tenant_id,
        evaluation_id=evaluation_id,
        outputs=result,
    )


@task_handler("radiance_eval", on_status=_on_status, on_success=_on_success)
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    provider = str(spec.get("provider") or "stub")
    if provider != "stub":
        return _run_container_service_provider(
            task,
            provider=provider,
            inputs=inputs,
            spec=spec,
            task_kind="radiance_eval",
            capability="radiance.evaluate",
        )

    radiance_field_id = inputs.get("radiance_field_id")
    evaluation_id = inputs.get("evaluation_id")
    snapshot_seq = inputs.get("snapshot_seq") or spec.get("snapshot_seq") or 1
    if not isinstance(radiance_field_id, str) or not radiance_field_id:
        raise ValidationError("radiance_eval missing radiance_field_id")
    if not isinstance(evaluation_id, str) or not evaluation_id:
        raise ValidationError("radiance_eval missing evaluation_id")
    if not isinstance(snapshot_seq, int):
        raise ValidationError("radiance_eval missing snapshot_seq")

    metrics = _stub_metrics()
    artifacts = [
        {
            "kind": "radiance.evaluation.metrics",
            "name": "metrics.json",
            "media_type": "application/json",
            "artifact_format": "sfmapi.radiance.metrics.v1",
            "summary": metrics,
        }
    ]
    return {
        "radiance_field_id": radiance_field_id,
        "evaluation_id": evaluation_id,
        "snapshot_seq": snapshot_seq,
        "metrics": metrics,
        "artifacts": artifacts,
    }
