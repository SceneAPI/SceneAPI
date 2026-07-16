"""Stage artifact persistence and output-contract helpers."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import artifacts as artifact_vocab
from app.core.errors import NotFoundError, ValidationError
from app.core.public_outputs import (
    sanitize_public_artifact_file_refs,
    sanitize_public_artifact_metadata_dict,
    sanitize_public_artifact_name,
)
from app.db.models import StageArtifact, Task

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_RESERVED_ARTIFACT_METADATA_KEYS = (
    "artifact_format",
    "datatype",
    "schema_version",
    "files",
    "sha256",
    "byte_size",
    "coordinate_frame",
    "producer",
)


def _task_inputs(task: Task) -> dict[str, Any]:
    state = task.task_state_json or {}
    inputs = state.get("inputs") or {}
    return inputs if isinstance(inputs, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _public_metadata_dict(value: Any) -> dict[str, Any] | None:
    return sanitize_public_artifact_metadata_dict(value)


def _summary(value: Any) -> dict[str, Any] | None:
    return _public_metadata_dict(value)


def _metadata_value(artifact: StageArtifact, key: str) -> Any:
    metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    return metadata.get(key)


def _public_artifact_file_refs(files: Any) -> list[dict[str, Any]]:
    """Return only public artifact-file fields; local paths stay internal."""
    return sanitize_public_artifact_file_refs(files)


def _public_artifact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    metadata_without_files = {key: value for key, value in metadata.items() if key != "files"}
    public = _public_metadata_dict(metadata_without_files) or {}
    if isinstance(metadata.get("files"), list):
        public["files"] = metadata["files"]
    return public


def _artifact_context(context: str, index: int) -> str:
    return f"{context}[{index}]"


def _validate_artifact_file_refs(
    files: Any,
    *,
    index: int,
    field: str,
    context: str = "outputs.artifacts",
) -> None:
    base = _artifact_context(context, index)
    if files is None:
        return
    if not isinstance(files, list):
        raise ValidationError(f"{base}.{field} must be a list")
    for file_index, file_ref in enumerate(files):
        where = f"{base}.{field}[{file_index}]"
        if not isinstance(file_ref, dict):
            raise ValidationError(f"{where} must be an object")
        if not isinstance(file_ref.get("name"), str) or not file_ref.get("name"):
            raise ValidationError(f"{where}.name is required")
        uri = file_ref.get("uri", file_ref.get("path"))
        if not isinstance(uri, str) or not uri:
            raise ValidationError(f"{where}.uri is required")
        for key, max_len in (
            ("name", 255),
            ("uri", 2048),
            ("path", 2048),
            ("media_type", 127),
        ):
            value = file_ref.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > max_len):
                raise ValidationError(f"{where}.{key} must be a string up to {max_len} chars")
        file_sha = file_ref.get("sha256")
        if file_sha is not None and (
            not isinstance(file_sha, str) or not _SHA256_RE.fullmatch(file_sha)
        ):
            raise ValidationError(f"{where}.sha256 must be a lowercase hex SHA-256 digest")
        file_size = file_ref.get("byte_size")
        if file_size is not None and (
            not isinstance(file_size, int) or isinstance(file_size, bool) or file_size < 0
        ):
            raise ValidationError(f"{where}.byte_size must be a non-negative int")


def _validate_reserved_metadata(
    metadata: dict[str, Any],
    *,
    index: int,
    context: str,
) -> None:
    base = _artifact_context(context, index)
    for key in ("artifact_format", "datatype"):
        value = metadata.get(key)
        if value is not None and (
            not isinstance(value, str) or not artifact_vocab.is_valid_artifact_key(value)
        ):
            raise ValidationError(
                f"{base}.metadata.{key} must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
            )
    schema_version = metadata.get("schema_version")
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        raise ValidationError(f"{base}.metadata.schema_version must be a positive int")
    _validate_artifact_file_refs(
        metadata.get("files"),
        index=index,
        field="metadata.files",
        context=context,
    )
    sha = metadata.get("sha256")
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha)):
        raise ValidationError(f"{base}.metadata.sha256 must be a lowercase hex SHA-256 digest")
    byte_size = metadata.get("byte_size")
    if byte_size is not None and (
        not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0
    ):
        raise ValidationError(f"{base}.metadata.byte_size must be a non-negative int")
    coordinate_frame = metadata.get("coordinate_frame")
    if coordinate_frame is not None and (
        not isinstance(coordinate_frame, str) or len(coordinate_frame) > 255
    ):
        raise ValidationError(f"{base}.metadata.coordinate_frame must be a string up to 255 chars")
    producer = metadata.get("producer")
    if producer is not None and not isinstance(producer, dict):
        raise ValidationError(f"{base}.metadata.producer must be an object")


def validate_artifact_descriptor(
    descriptor: Any,
    *,
    index: int,
    context: str = "outputs.artifacts",
) -> dict[str, Any]:
    base = _artifact_context(context, index)
    if not isinstance(descriptor, dict):
        raise ValidationError(f"{base} must be an object")
    original_descriptor = descriptor
    descriptor = dict(descriptor)
    raw_metadata = descriptor.get("metadata")
    if raw_metadata is not None and not isinstance(raw_metadata, dict):
        raise ValidationError(f"{base}.metadata must be an object")
    metadata = dict(raw_metadata or {})
    _validate_reserved_metadata(metadata, index=index, context=context)
    for key in _RESERVED_ARTIFACT_METADATA_KEYS:
        if descriptor.get(key) is None and metadata.get(key) is not None:
            descriptor[key] = metadata[key]
    kind = descriptor.get("kind")
    if not isinstance(kind, str) or not artifact_vocab.is_valid_artifact_key(kind):
        raise ValidationError(f"{base}.kind must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}")
    core_kind = artifact_vocab.CORE_ARTIFACT_KINDS.get(kind)
    name = descriptor.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 255):
        raise ValidationError(f"{base}.name must be a string up to 255 chars")
    if name is not None:
        descriptor["name"] = sanitize_public_artifact_name(name)
    uri = descriptor.get("uri")
    if uri is not None and (not isinstance(uri, str) or len(uri) > 2048):
        raise ValidationError(f"{base}.uri must be a string up to 2048 chars")
    media_type = descriptor.get("media_type")
    if media_type is not None and (not isinstance(media_type, str) or len(media_type) > 127):
        raise ValidationError(f"{base}.media_type must be a string up to 127 chars")
    artifact_format = descriptor.get("artifact_format")
    if artifact_format is None and core_kind is not None:
        # The Format axis is open: a plugin backend may override the I/O format
        # for this kind's DataType. Prefer the backend-declared format, else the
        # core default (always present -- the override never removes I/O).
        from app.adapters import backend_artifacts

        artifact_format = (
            backend_artifacts.backend_default_format_for_kind(kind) or core_kind.artifact_format
        )
        descriptor["artifact_format"] = artifact_format
    if artifact_format is not None and (
        not isinstance(artifact_format, str)
        or not artifact_vocab.is_valid_artifact_key(artifact_format)
    ):
        raise ValidationError(
            f"{base}.artifact_format must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
        )
    if artifact_format is not None and not artifact_vocab.is_format_compatible_with_kind(
        kind,
        str(artifact_format),
    ):
        raise ValidationError(
            f"{base}.artifact_format {artifact_format!r} is not compatible with kind {kind!r}"
        )
    schema_version = descriptor.get("schema_version")
    if schema_version is None and core_kind is not None:
        schema_version = core_kind.schema_version
        descriptor["schema_version"] = schema_version
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        raise ValidationError(f"{base}.schema_version must be a positive int")
    if isinstance(artifact_format, str):
        format_def = artifact_vocab.CORE_ARTIFACT_FORMATS.get(artifact_format)
        if (
            format_def is not None
            and schema_version is not None
            and schema_version != format_def.schema_version
        ):
            raise ValidationError(
                f"{base}.schema_version {schema_version!r} does not match "
                f"artifact_format {artifact_format!r}"
            )
    datatype = descriptor.get("datatype")
    if datatype is None and artifact_format is not None:
        datatype = artifact_vocab.datatype_for_format(str(artifact_format))
        if datatype is not None:
            descriptor["datatype"] = datatype
    if datatype is None:
        datatype = artifact_vocab.datatype_for_kind(kind)
        if datatype is not None:
            descriptor["datatype"] = datatype
    if datatype is None and core_kind is not None:
        datatype = core_kind.datatype
        descriptor["datatype"] = datatype
    if datatype is not None and (
        not isinstance(datatype, str) or not artifact_vocab.is_valid_artifact_key(datatype)
    ):
        raise ValidationError(
            f"{base}.datatype must match {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
        )
    if isinstance(datatype, str):
        if artifact_format is not None:
            format_datatype = artifact_vocab.datatype_for_format(str(artifact_format))
            if format_datatype is not None and datatype != format_datatype:
                raise ValidationError(
                    f"{base}.datatype {datatype!r} is not compatible with "
                    f"artifact_format {artifact_format!r}"
                )
        kind_datatype = artifact_vocab.datatype_for_kind(kind)
        if kind_datatype is not None and datatype != kind_datatype:
            raise ValidationError(
                f"{base}.datatype {datatype!r} is not compatible with kind {kind!r}"
            )
    _validate_artifact_file_refs(
        descriptor.get("files"),
        index=index,
        field="files",
        context=context,
    )
    sha = descriptor.get("sha256")
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha)):
        raise ValidationError(f"{base}.sha256 must be a lowercase hex SHA-256 digest")
    byte_size = descriptor.get("byte_size")
    if byte_size is not None and (
        not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0
    ):
        raise ValidationError(f"{base}.byte_size must be a non-negative int")
    coordinate_frame = descriptor.get("coordinate_frame")
    if coordinate_frame is not None and (
        not isinstance(coordinate_frame, str) or len(coordinate_frame) > 255
    ):
        raise ValidationError(f"{base}.coordinate_frame must be a string up to 255 chars")
    producer = descriptor.get("producer")
    if producer is not None and not isinstance(producer, dict):
        raise ValidationError(f"{base}.producer must be an object")
    summary = descriptor.get("summary")
    if summary is not None and not isinstance(summary, dict):
        raise ValidationError(f"{base}.summary must be an object")
    if producer is not None:
        descriptor["producer"] = _public_metadata_dict(producer) or {}
    if summary is not None:
        descriptor["summary"] = _summary(summary) or {}
    metadata = _public_artifact_metadata(metadata)
    for key in _RESERVED_ARTIFACT_METADATA_KEYS:
        metadata.pop(key, None)
    for key in _RESERVED_ARTIFACT_METADATA_KEYS:
        if key in descriptor:
            if key == "files":
                metadata["files"] = descriptor["files"]
            else:
                metadata[key] = descriptor[key]
    descriptor["metadata"] = metadata
    original_descriptor.clear()
    original_descriptor.update(descriptor)
    return original_descriptor


def _validate_artifact_descriptor(descriptor: Any, *, index: int) -> dict[str, Any]:
    return validate_artifact_descriptor(descriptor, index=index)


def _append_unique(out: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = (item.get("kind"), item.get("name"), item.get("uri"))
    if key in {(a.get("kind"), a.get("name"), a.get("uri")) for a in out}:
        return
    out.append(item)


def normalize_task_outputs(task: Task, outputs: Any) -> dict[str, Any]:
    """Validate and enrich a worker return payload.

    Worker handlers must return a dict. Portable data products must be
    declared explicitly through ``{"artifacts": [...]}``; sfmapi no
    longer infers interchange semantics from backend-local paths.
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
        _append_unique(artifacts, validate_artifact_descriptor(descriptor, index=index))
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
                metadata_json=dict(item.get("metadata") or {})
                if isinstance(item.get("metadata"), dict)
                else None,
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
        not isinstance(expected_kind, str)
        or not artifact_vocab.is_valid_artifact_key(expected_kind)
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
        if not artifact_vocab.is_artifact_allowed_for_role(role, artifact.kind):
            allowed = ", ".join(sorted(artifact_vocab.ARTIFACT_INPUT_ROLE_KINDS.get(role, ())))
            raise ValidationError(
                f"input_artifacts.{role} expects one of [{allowed}], got {artifact.kind!r}"
            )
        if (
            dataset_id is not None
            and artifact.dataset_id is not None
            and artifact.dataset_id != dataset_id
        ):
            raise ValidationError(
                f"input_artifacts.{role} belongs to dataset {artifact.dataset_id}, not {dataset_id}"
            )
        resolved[role] = {
            "artifact_id": artifact.artifact_id,
            "kind": artifact.kind,
            "name": artifact.name,
            "uri": artifact.uri,
            "media_type": artifact.media_type,
            "artifact_format": _metadata_value(artifact, "artifact_format"),
            "datatype": _metadata_value(artifact, "datatype")
            or artifact_vocab.datatype_for_kind(artifact.kind),
            "schema_version": _metadata_value(artifact, "schema_version"),
            "summary": artifact.summary_json,
            "metadata": artifact.metadata_json,
            "job_id": artifact.job_id,
            "task_id": artifact.task_id,
            "recon_id": artifact.recon_id,
            "dataset_id": artifact.dataset_id,
        }
    return resolved


def artifact_uri_from_input_artifacts(
    input_artifacts: dict[str, dict[str, Any]],
    *,
    roles: tuple[str, ...],
    accepted_prefixes: tuple[str, ...] = (),
) -> str | None:
    """Return the URI for the first selected artifact matching a role.

    ``accepted_prefixes`` lets a worker ask for a same-family
    backend-native artifact without hardcoding one engine's database
    format into the sfmapi contract.
    """
    for role in roles:
        artifact = input_artifacts.get(role)
        if not artifact:
            continue
        kind = artifact.get("kind")
        uri = artifact.get("uri")
        if uri and (
            not accepted_prefixes or any(str(kind).startswith(p) for p in accepted_prefixes)
        ):
            return str(uri)
    return None


def database_path_from_input_artifacts(
    input_artifacts: dict[str, dict[str, Any]],
    *,
    roles: tuple[str, ...],
) -> str | None:
    """Return a backend-native database URI selected for a DB-backed worker.

    Portable artifacts still travel through ``options["input_artifacts"]``.
    Only engine-native database kinds are eligible to replace the worker's
    default ``database_path``.
    """
    return artifact_uri_from_input_artifacts(
        input_artifacts,
        roles=roles,
        accepted_prefixes=("features.database.", "matches.database."),
    )
