"""Stage artifact persistence and output-contract helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import artifacts as artifact_vocab
from app.core.errors import NotFoundError, ValidationError
from app.db.models import StageArtifact, Task


def _task_inputs(task: Task) -> dict[str, Any]:
    state = task.task_state_json or {}
    inputs = state.get("inputs") or {}
    return inputs if isinstance(inputs, dict) else {}


def _task_spec(task: Task) -> dict[str, Any]:
    state = task.task_state_json or {}
    spec = state.get("spec") or {}
    return spec if isinstance(spec, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _summary(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _output_summary(outputs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in outputs.items() if key != "artifacts"}


def _metadata(task: Task, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"stage": task.kind}
    spec = _task_spec(task)
    provider = spec.get("provider")
    if provider is not None:
        out["provider"] = provider
    if extra:
        out.update(extra)
    return out


def _validate_artifact_descriptor(descriptor: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ValidationError(f"outputs.artifacts[{index}] must be an object")
    kind = descriptor.get("kind")
    if not isinstance(kind, str) or not artifact_vocab.is_valid_artifact_key(kind):
        raise ValidationError(
            "outputs.artifacts"
            f"[{index}].kind must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
        )
    name = descriptor.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 255):
        raise ValidationError(f"outputs.artifacts[{index}].name must be a string up to 255 chars")
    uri = descriptor.get("uri")
    if uri is not None and (not isinstance(uri, str) or len(uri) > 2048):
        raise ValidationError(f"outputs.artifacts[{index}].uri must be a string up to 2048 chars")
    media_type = descriptor.get("media_type")
    if media_type is not None and (not isinstance(media_type, str) or len(media_type) > 127):
        raise ValidationError(
            f"outputs.artifacts[{index}].media_type must be a string up to 127 chars"
        )
    summary = descriptor.get("summary")
    if summary is not None and not isinstance(summary, dict):
        raise ValidationError(f"outputs.artifacts[{index}].summary must be an object")
    metadata = descriptor.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValidationError(f"outputs.artifacts[{index}].metadata must be an object")
    return descriptor


def _append_unique(out: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = (item.get("kind"), item.get("name"), item.get("uri"))
    if key in {(a.get("kind"), a.get("name"), a.get("uri")) for a in out}:
        return
    out.append(item)


def _infer_artifacts(task: Task, outputs: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = _task_inputs(task)
    artifacts: list[dict[str, Any]] = []
    recon_id = _optional_str(inputs.get("recon_id"))
    dataset_id = _optional_str(inputs.get("dataset_id"))

    database_path = _optional_str(outputs.get("database_path"))
    database_kind = artifact_vocab.DATABASE_ARTIFACT_KIND_BY_TASK.get(task.kind)
    if database_path and database_kind:
        _append_unique(
            artifacts,
            {
                "kind": database_kind,
                "name": "database",
                "uri": database_path,
                "summary": _output_summary(outputs),
                "metadata": _metadata(task, {"source": "inferred"}),
                "recon_id": recon_id,
                "dataset_id": dataset_id,
            },
        )

    correspondence_path = _optional_str(outputs.get("correspondence_graph_path"))
    if correspondence_path:
        _append_unique(
            artifacts,
            {
                "kind": "matches.correspondence_graph",
                "name": "correspondence_graph",
                "uri": correspondence_path,
                "media_type": "application/json",
                "summary": _output_summary(outputs),
                "metadata": _metadata(task, {"source": "inferred"}),
                "recon_id": recon_id,
                "dataset_id": dataset_id,
            },
        )

    two_view_path = _optional_str(outputs.get("two_view_geometries_path"))
    if two_view_path:
        _append_unique(
            artifacts,
            {
                "kind": "matches.two_view_geometries",
                "name": "two_view_geometries",
                "uri": two_view_path,
                "media_type": "application/json",
                "summary": _output_summary(outputs),
                "metadata": _metadata(task, {"source": "inferred"}),
                "recon_id": recon_id,
                "dataset_id": dataset_id,
            },
        )

    snapshot_path = _optional_str(outputs.get("snapshot_path"))
    if snapshot_path:
        _append_unique(
            artifacts,
            {
                "kind": "reconstruction.snapshot",
                "name": f"snapshot-{outputs.get('snapshot_seq')}",
                "uri": snapshot_path,
                "summary": {"snapshot_seq": outputs.get("snapshot_seq")},
                "metadata": _metadata(task, {"source": "inferred"}),
                "recon_id": recon_id,
                "dataset_id": dataset_id,
            },
        )

    models = outputs.get("models")
    if snapshot_path and isinstance(models, list):
        for position, summary in enumerate(models):
            if not isinstance(summary, dict):
                continue
            idx = summary.get("idx", position)
            _append_unique(
                artifacts,
                {
                    "kind": "reconstruction.submodel",
                    "name": f"submodel-{idx}",
                    "uri": str(Path(snapshot_path) / str(idx))
                    if len(models) > 1
                    else snapshot_path,
                    "summary": summary,
                    "metadata": _metadata(task, {"source": "inferred", "idx": idx}),
                    "recon_id": recon_id,
                    "dataset_id": dataset_id,
                },
            )
    return artifacts


def normalize_task_outputs(task: Task, outputs: Any) -> dict[str, Any]:
    """Validate and enrich a worker return payload.

    Worker handlers must return a dict. They may return
    ``{"artifacts": [...]}``; sfmapi also infers artifacts from legacy
    keys such as ``database_path`` and ``snapshot_path`` for backward
    compatibility.
    """
    if outputs is None:
        outputs = {}
    if not isinstance(outputs, dict):
        raise ValidationError(
            f"{task.kind} worker returned {type(outputs).__name__}; expected dict"
        )

    normalized = dict(outputs)
    explicit = normalized.get("artifacts") or []
    if explicit and not isinstance(explicit, list):
        raise ValidationError("outputs.artifacts must be a list of objects")

    artifacts: list[dict[str, Any]] = []
    for index, descriptor in enumerate(explicit):
        _append_unique(artifacts, _validate_artifact_descriptor(descriptor, index=index))
    for descriptor in _infer_artifacts(task, normalized):
        _append_unique(artifacts, descriptor)
    if artifacts:
        normalized["artifacts"] = artifacts
    return normalized


async def record_task_artifacts(
    session: AsyncSession,
    *,
    task: Task,
    outputs: dict[str, Any],
) -> None:
    await session.execute(
        delete(StageArtifact).where(
            StageArtifact.tenant_id == task.tenant_id,
            StageArtifact.task_id == task.task_id,
        )
    )
    artifacts = outputs.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise ValidationError("outputs.artifacts must be a list of objects")
    inputs = _task_inputs(task)
    default_recon_id = _optional_str(inputs.get("recon_id"))
    default_dataset_id = _optional_str(inputs.get("dataset_id"))
    for index, raw in enumerate(artifacts):
        item = _validate_artifact_descriptor(raw, index=index)
        session.add(
            StageArtifact(
                tenant_id=task.tenant_id,
                job_id=task.job_id,
                task_id=task.task_id,
                recon_id=_optional_str(item.get("recon_id")) or default_recon_id,
                dataset_id=_optional_str(item.get("dataset_id")) or default_dataset_id,
                kind=str(item["kind"]),
                name=_optional_str(item.get("name")),
                uri=_optional_str(item.get("uri")),
                media_type=_optional_str(item.get("media_type")),
                summary_json=_summary(item.get("summary")),
                metadata_json=_summary(item.get("metadata")),
            )
        )
    await session.flush()


async def list_job_artifacts(
    session: AsyncSession,
    *,
    tenant_id: str,
    job_id: str,
    page_size: int = 100,
    page_token: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    name: str | None = None,
) -> tuple[list[StageArtifact], str | None]:
    _validate_list_filters(kind=kind, name=name)
    stmt = (
        select(StageArtifact)
        .where(StageArtifact.tenant_id == tenant_id, StageArtifact.job_id == job_id)
        .order_by(StageArtifact.artifact_id)
    )
    if kind:
        stmt = stmt.where(StageArtifact.kind == kind)
    if task_id:
        stmt = stmt.where(StageArtifact.task_id == task_id)
    if name:
        stmt = stmt.where(StageArtifact.name == name)
    if page_token:
        stmt = stmt.where(StageArtifact.artifact_id > page_token)
    stmt = stmt.limit(page_size + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    next_page_token: str | None = None
    if len(rows) > page_size:
        next_page_token = rows[page_size - 1].artifact_id
        rows = rows[:page_size]
    return rows, next_page_token


async def list_reconstruction_artifacts(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    page_size: int = 100,
    page_token: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    name: str | None = None,
) -> tuple[list[StageArtifact], str | None]:
    _validate_list_filters(kind=kind, name=name)
    stmt = (
        select(StageArtifact)
        .where(StageArtifact.tenant_id == tenant_id, StageArtifact.recon_id == recon_id)
        .order_by(StageArtifact.artifact_id)
    )
    if kind:
        stmt = stmt.where(StageArtifact.kind == kind)
    if task_id:
        stmt = stmt.where(StageArtifact.task_id == task_id)
    if name:
        stmt = stmt.where(StageArtifact.name == name)
    if page_token:
        stmt = stmt.where(StageArtifact.artifact_id > page_token)
    stmt = stmt.limit(page_size + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    next_page_token: str | None = None
    if len(rows) > page_size:
        next_page_token = rows[page_size - 1].artifact_id
        rows = rows[:page_size]
    return rows, next_page_token


def _validate_list_filters(*, kind: str | None, name: str | None) -> None:
    if kind is not None and not artifact_vocab.is_valid_artifact_key(kind):
        raise ValidationError(f"kind must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}")
    if name is not None and len(name) > 255:
        raise ValidationError("name must be at most 255 characters")


async def get_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    artifact_id: str,
) -> StageArtifact:
    artifact = await session.get(StageArtifact, artifact_id)
    if artifact is None or artifact.tenant_id != tenant_id:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    return artifact


def _artifact_ref_dict(value: Any, *, role: str) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValidationError(f"input_artifacts.{role} must be an object")
    artifact_id = value.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValidationError(f"input_artifacts.{role}.artifact_id is required")
    expected_kind = value.get("kind")
    if expected_kind is not None and (
        not isinstance(expected_kind, str) or not artifact_vocab.is_valid_artifact_key(expected_kind)
    ):
        raise ValidationError(
            f"input_artifacts.{role}.kind must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
        )
    return {"artifact_id": artifact_id, "kind": expected_kind}


async def resolve_input_artifacts(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str | None,
    input_artifacts: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Validate role-keyed artifact references for a stage submission."""
    if not input_artifacts:
        return {}
    if not isinstance(input_artifacts, dict):
        raise ValidationError("input_artifacts must be an object")

    resolved: dict[str, dict[str, Any]] = {}
    for role, raw_ref in input_artifacts.items():
        if not isinstance(role, str) or not artifact_vocab.is_valid_artifact_key(role):
            raise ValidationError(
                f"input_artifacts role must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
            )
        ref = _artifact_ref_dict(raw_ref, role=role)
        artifact = await get_artifact(
            session,
            tenant_id=tenant_id,
            artifact_id=str(ref["artifact_id"]),
        )
        expected_kind = ref.get("kind")
        if expected_kind is not None and artifact.kind != expected_kind:
            raise ValidationError(
                f"input_artifacts.{role}.kind expected {expected_kind!r}, "
                f"but artifact {artifact.artifact_id} is {artifact.kind!r}"
            )
        allowed_kinds = artifact_vocab.ARTIFACT_INPUT_ROLE_KINDS.get(role)
        if allowed_kinds is not None and artifact.kind not in allowed_kinds:
            allowed = ", ".join(sorted(allowed_kinds))
            raise ValidationError(
                f"input_artifacts.{role} expects one of [{allowed}], "
                f"got {artifact.kind!r}"
            )
        if (
            dataset_id is not None
            and artifact.dataset_id is not None
            and artifact.dataset_id != dataset_id
        ):
            raise ValidationError(
                f"input_artifacts.{role} belongs to dataset {artifact.dataset_id}, "
                f"not {dataset_id}"
            )
        resolved[role] = {
            "artifact_id": artifact.artifact_id,
            "kind": artifact.kind,
            "name": artifact.name,
            "uri": artifact.uri,
            "media_type": artifact.media_type,
            "summary": artifact.summary_json,
            "metadata": artifact.metadata_json,
            "job_id": artifact.job_id,
            "task_id": artifact.task_id,
            "recon_id": artifact.recon_id,
            "dataset_id": artifact.dataset_id,
        }
    return resolved


def database_path_from_input_artifacts(
    input_artifacts: dict[str, dict[str, Any]],
    *,
    roles: tuple[str, ...],
) -> str | None:
    """Return a backend-readable database path from a selected artifact."""
    for role in roles:
        artifact = input_artifacts.get(role)
        if not artifact:
            continue
        kind = artifact.get("kind")
        uri = artifact.get("uri")
        if kind in {
            "features.database",
            "matches.database",
            "matches.verified_database",
        } and uri:
            return str(uri)
    return None
