"""Artifact format conversion task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.backend import require_backend_method
from app.adapters.progress import call_with_optional_progress
from app.adapters.registry import get_backend
from app.core import artifacts as artifact_vocab
from app.core.config import get_settings
from app.core.paths import Paths
from app.db.models import Task
from app.workers._task_io import read_state
from app.workers.progress import get_progress_reporter
from app.workers.tasks._registry import task_handler


def _first_artifact(output: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = output.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return None
    first = artifacts[0]
    return dict(first) if isinstance(first, dict) else None


def _synthesized_artifact(
    *,
    current: dict[str, Any],
    output: dict[str, Any],
    output_dir: Path,
    to_format: str,
    to_kind: str,
    name: str | None,
    backend_name: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    uri = output.get("uri") or output.get("path") or output_dir
    return {
        "kind": to_kind,
        "name": name or f"converted-{to_format}",
        "uri": str(Path(uri) if isinstance(uri, str) else uri),
        "artifact_format": to_format,
        "schema_version": 1,
        "producer": {"backend": backend_name},
        "metadata": {
            "source_artifact_id": current.get("artifact_id"),
            "conversion": plan,
        },
    }


def _target_kind_for_step(
    *,
    current_kind: str,
    target_format: str,
    final_target_kind: str,
    is_final_step: bool,
) -> str:
    if is_final_step:
        return final_target_kind
    return artifact_vocab.kind_for_default_format(target_format) or current_kind


@task_handler("convert_artifact")
def run(task: Task) -> dict[str, Any]:
    inputs, spec = read_state(task)
    artifact = dict(inputs["artifact"])
    target_format = str(spec["target_format"])
    target_kind = str(spec["target_kind"])

    workspace = (
        Paths(get_settings()).job_root(task.tenant_id, str(inputs["project_id"]), task.job_id)
        / "artifact_conversions"
        / task.task_id
    )
    workspace.mkdir(parents=True, exist_ok=True)

    backend = get_backend()
    convert = require_backend_method(
        backend,
        "convert_artifact",
        capability="artifacts.convert",
    )
    backend_name = str(getattr(backend, "name", "unknown"))
    raw_plan = inputs.get("plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_steps = plan.get("steps") if isinstance(plan, dict) else None
    steps = (
        [step for step in raw_steps if isinstance(step, dict)]
        if isinstance(raw_steps, list)
        else []
    )
    if not steps:
        steps = [{"to_format": target_format}]

    progress = get_progress_reporter()
    if progress is not None:
        progress.phase_started("artifact_conversion")
        progress.phase_progress("artifact_conversion", current=0, total=len(steps))

    current_artifact = artifact
    output: dict[str, Any] = {}
    workspaces: list[str] = []
    for index, step in enumerate(steps):
        step_target_format = str(step.get("to_format") or target_format)
        is_final = index == len(steps) - 1
        step_target_kind = _target_kind_for_step(
            current_kind=str(current_artifact.get("kind") or target_kind),
            target_format=step_target_format,
            final_target_kind=target_kind,
            is_final_step=is_final,
        )
        step_workspace = workspace / f"step-{index + 1:02d}"
        step_workspace.mkdir(parents=True, exist_ok=True)
        workspaces.append(str(step_workspace))
        result = call_with_optional_progress(
            convert,
            progress=progress,
            input_artifact=current_artifact,
            output_dir=step_workspace,
            to_format=step_target_format,
            to_kind=step_target_kind,
            options=dict(spec.get("options") or {}),
        )
        output = dict(result or {})
        current_artifact = _first_artifact(output) or _synthesized_artifact(
            current=current_artifact,
            output=output,
            output_dir=step_workspace,
            to_format=step_target_format,
            to_kind=step_target_kind,
            name=str(spec.get("name")) if spec.get("name") is not None else None,
            backend_name=backend_name,
            plan=plan,
        )
        current_metadata = current_artifact.get("metadata")
        if not isinstance(current_metadata, dict):
            current_metadata = {}
            current_artifact["metadata"] = current_metadata
        current_metadata.setdefault("source_artifact_id", artifact.get("artifact_id"))
        current_metadata.setdefault("conversion", plan)
        current_artifact.setdefault("artifact_format", step_target_format)
        current_artifact.setdefault("kind", step_target_kind)
        if progress is not None:
            progress.phase_progress(
                "artifact_conversion",
                current=index + 1,
                total=len(steps),
            )

    if progress is not None:
        progress.phase_completed("artifact_conversion")

    artifacts = output.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        artifacts = [current_artifact]
    for item in artifacts:
        if isinstance(item, dict):
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                item["metadata"] = metadata
            metadata.setdefault("source_artifact_id", artifact.get("artifact_id"))
            metadata.setdefault("conversion", plan)
            item.setdefault("artifact_format", target_format)
            item.setdefault("kind", target_kind)
            item.setdefault("producer", {"backend": backend_name})
            item.setdefault("schema_version", 1)

    return {
        "source_artifact_id": artifact.get("artifact_id"),
        "target_format": target_format,
        "target_kind": target_kind,
        "workspace": str(workspace),
        "step_workspaces": workspaces,
        "conversion_steps": [
            {
                "from_format": step.get("from_format"),
                "to_format": step.get("to_format"),
                "lossless": step.get("lossless"),
                "contract_id": step.get("contract_id"),
            }
            for step in steps
        ],
        **output,
        "artifacts": artifacts,
    }
