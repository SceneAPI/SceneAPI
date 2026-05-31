"""Radiance evaluation task."""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.db.models import Task
from app.workers._task_io import read_state
from app.workers.tasks._registry import task_handler
from app.workers.tasks.radiance_train import _run_container_service_provider, _stub_metrics


@task_handler("radiance_eval")
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
