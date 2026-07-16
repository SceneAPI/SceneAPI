"""Artifact format planning, conversion submission, and validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import backend_artifacts
from app.adapters.backend import has_backend_method
from app.adapters.registry import get_backend
from app.core import artifacts as artifact_vocab
from app.core.config import get_settings
from app.core.errors import CapabilityUnavailableError, NotFoundError, ValidationError
from app.core.hashing import canonical_json, content_address, stream_sha256
from app.core.ids import new_id
from app.db.models import Dataset, Job, Project, Reconstruction, StageArtifact, Task, utcnow
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.schemas.api.artifacts import (
    ArtifactConversionPlanOut,
    ArtifactConversionPlanRequest,
    ArtifactConversionStepOut,
    ArtifactConvertRequest,
    ArtifactImportRequest,
    ArtifactValidationIssueOut,
    ArtifactValidationOut,
)
from app.services import artifact_service, provider_routing_service
from sfm_hub.routing import ensure_provider_enabled


def _path_from_file_uri_or_local(uri: str) -> Path | None:
    if len(uri) >= 3 and uri[0].isalpha() and uri[1] == ":" and uri[2] in ("\\", "/"):
        return Path(uri)
    if uri.startswith("\\\\"):
        return Path(uri)
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        if (
            len(raw_path) >= 3
            and raw_path[0] == "/"
            and raw_path[1].isalpha()
            and raw_path[2] == ":"
        ):
            raw_path = raw_path[1:]
        return Path(raw_path)
    if parsed.scheme:
        return None
    return Path(uri)


def _metadata(artifact: StageArtifact) -> dict[str, Any]:
    return artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}


def _artifact_format(artifact: StageArtifact) -> str | None:
    value = _metadata(artifact).get("artifact_format")
    return str(value) if isinstance(value, str) and value else None


def _datatype(artifact: StageArtifact) -> str | None:
    value = _metadata(artifact).get("datatype")
    if isinstance(value, str) and value:
        return value
    fmt = _artifact_format(artifact)
    if fmt is not None:
        inferred = artifact_vocab.datatype_for_format(fmt)
        if inferred is not None:
            return inferred
    return artifact_vocab.datatype_for_kind(artifact.kind)


def _datatype_conflict_message(
    *,
    kind: str,
    artifact_format: str | None,
    datatype: str | None,
) -> str | None:
    if datatype is None:
        return None
    if not artifact_vocab.is_valid_artifact_key(datatype):
        return "datatype is malformed"
    if artifact_format is not None:
        format_datatype = artifact_vocab.datatype_for_format(artifact_format)
        if format_datatype is not None and datatype != format_datatype:
            return (
                f"datatype {datatype!r} is not compatible with artifact_format {artifact_format!r}"
            )
    kind_datatype = artifact_vocab.datatype_for_kind(kind)
    if kind_datatype is not None and datatype != kind_datatype:
        return f"datatype {datatype!r} is not compatible with kind {kind!r}"
    return None


def _artifact_ref(artifact: StageArtifact) -> dict[str, Any]:
    metadata = _metadata(artifact)
    return {
        "artifact_id": artifact.artifact_id,
        "kind": artifact.kind,
        "name": artifact.name,
        "uri": artifact.uri,
        "media_type": artifact.media_type,
        "artifact_format": _artifact_format(artifact),
        "datatype": _datatype(artifact),
        "schema_version": metadata.get("schema_version"),
        "files": metadata.get("files") if isinstance(metadata.get("files"), list) else [],
        "summary": artifact.summary_json if isinstance(artifact.summary_json, dict) else {},
        "metadata": metadata,
        "job_id": artifact.job_id,
        "task_id": artifact.task_id,
        "recon_id": artifact.recon_id,
        "dataset_id": artifact.dataset_id,
    }


def _resolve_backend(provider: str | None) -> Any:
    try:
        if provider is not None:
            ensure_provider_enabled(provider)
        return get_backend(provider=provider)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc


def _target_formats(request: ArtifactConversionPlanRequest) -> list[str]:
    out: list[str] = []
    if request.to_format:
        out.append(request.to_format)
    for value in request.accepted_formats:
        if not isinstance(value, str) or not artifact_vocab.is_valid_artifact_key(value):
            raise ValidationError(
                f"accepted_formats contains invalid format id {value!r}; "
                f"expected {artifact_vocab.ARTIFACT_KEY_RE.pattern!r}"
            )
        if value not in out:
            out.append(value)
    if not out:
        raise ValidationError("to_format or accepted_formats is required")
    return out


def _conversion_step(
    *,
    contract: dict[str, Any],
    conversion: dict[str, Any],
) -> ArtifactConversionStepOut:
    return ArtifactConversionStepOut(
        contract_id=str(contract.get("contract_id")) if contract.get("contract_id") else None,
        backend=str(contract.get("backend")) if contract.get("backend") else None,
        provider=str(contract.get("provider")) if contract.get("provider") else None,
        from_format=str(conversion["from_format"]),
        to_format=str(conversion["to_format"]),
        lossless=bool(conversion.get("lossless", False)),
        description=str(conversion.get("description"))
        if conversion.get("description") is not None
        else None,
    )


def _iter_conversion_steps(*, provider: str | None = None) -> list[ArtifactConversionStepOut]:
    steps: list[ArtifactConversionStepOut] = []
    backend = _resolve_backend(provider)
    for contract in backend_artifacts.list_backend_artifact_contracts(backend):
        for conversion in contract.get("conversions") or []:
            if not isinstance(conversion, dict):
                continue
            steps.append(_conversion_step(contract=contract, conversion=conversion))
    return steps


def _find_conversion_path(
    *,
    from_format: str,
    target_formats: list[str],
    require_lossless: bool,
    provider: str | None,
) -> tuple[str, list[ArtifactConversionStepOut]] | None:
    targets = set(target_formats)
    if from_format in targets:
        return from_format, []

    edges_by_source: dict[str, list[ArtifactConversionStepOut]] = {}
    for step in _iter_conversion_steps(provider=provider):
        if require_lossless and not step.lossless:
            continue
        edges_by_source.setdefault(step.from_format, []).append(step)

    queue: list[tuple[str, list[ArtifactConversionStepOut]]] = [(from_format, [])]
    visited = {from_format}
    while queue:
        current, path = queue.pop(0)
        for step in edges_by_source.get(current, []):
            if step.to_format in visited:
                continue
            next_path = [*path, step]
            if step.to_format in targets:
                return step.to_format, next_path
            visited.add(step.to_format)
            queue.append((step.to_format, next_path))
    return None


def plan_conversion_for_artifact(
    artifact: StageArtifact,
    request: ArtifactConversionPlanRequest,
) -> ArtifactConversionPlanOut:
    source_format = _artifact_format(artifact)
    target_formats = _target_formats(request)
    if source_format is None:
        return ArtifactConversionPlanOut(
            artifact_id=artifact.artifact_id,
            source_format=None,
            target_format=target_formats[0],
            conversion_required=True,
            executable=False,
            reason="artifact has no artifact_format metadata",
        )

    for target_format in target_formats:
        if target_format == source_format:
            return ArtifactConversionPlanOut(
                artifact_id=artifact.artifact_id,
                source_format=source_format,
                target_format=target_format,
                conversion_required=False,
                executable=True,
                reason="artifact already uses the selected format",
            )

    path = _find_conversion_path(
        from_format=source_format,
        target_formats=target_formats,
        require_lossless=request.require_lossless,
        provider=request.provider,
    )
    if path is not None:
        target_format, steps = path
        return ArtifactConversionPlanOut(
            artifact_id=artifact.artifact_id,
            source_format=source_format,
            target_format=target_format,
            conversion_required=True,
            executable=True,
            steps=steps,
        )

    return ArtifactConversionPlanOut(
        artifact_id=artifact.artifact_id,
        source_format=source_format,
        target_format=target_formats[0],
        conversion_required=True,
        executable=False,
        reason=(
            "no backend artifact conversion path matches the requested formats"
            + (" with lossless=true" if request.require_lossless else "")
        ),
    )


async def get_conversion_plan(
    session: AsyncSession,
    *,
    tenant_id: str,
    artifact_id: str,
    request: ArtifactConversionPlanRequest,
) -> ArtifactConversionPlanOut:
    artifact = await artifact_service.get_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )
    return plan_conversion_for_artifact(artifact, request)


def _target_kind(source_kind: str, target_format: str, explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    core_kind = artifact_vocab.kind_for_default_format(target_format)
    if core_kind is not None:
        return core_kind
    return source_kind


async def submit_conversion(
    session: AsyncSession,
    *,
    tenant_id: str,
    artifact_id: str,
    request: ArtifactConvertRequest,
    inline: bool = False,
) -> tuple[str, list[Any], str, str | None]:
    artifact = await artifact_service.get_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )
    plan = plan_conversion_for_artifact(artifact, request)
    if not plan.executable:
        raise ValidationError(plan.reason or "artifact conversion is not executable")
    if not plan.conversion_required:
        raise ValidationError(
            "artifact already uses the selected format; no conversion job created"
        )

    source_job = await session.get(Job, artifact.job_id)
    if source_job is None:
        raise NotFoundError(f"source job not found for artifact {artifact_id}")
    target_kind = _target_kind(artifact.kind, plan.target_format, request.to_kind)
    if not artifact_vocab.is_format_compatible_with_kind(target_kind, plan.target_format):
        raise ValidationError(
            f"target format {plan.target_format!r} is not compatible with kind {target_kind!r}"
        )

    task_inputs = {
        "artifact": _artifact_ref(artifact),
        "project_id": source_job.project_id,
        "plan": plan.model_dump(mode="json"),
    }
    task_spec: dict[str, Any] = {
        "target_format": plan.target_format,
        "target_kind": target_kind,
        "name": request.name,
        "options": request.options,
    }
    if request.provider is not None:
        task_spec["provider"] = request.provider
    # Resolve a provider through routing profiles when the request did
    # not pin one — then the submit-time backend probe and the worker
    # both act on the SAME resolved provider.
    provider_routing_service.apply_provider_resolution(
        task_spec,
        stage="convert_artifact",
        capability="artifacts.convert",
        project_id=source_job.project_id,
        workspace=str(get_settings().workspace_root),
    )
    backend = _resolve_backend(task_spec.get("provider"))
    if not has_backend_method(backend, "convert_artifact"):
        raise CapabilityUnavailableError(
            capability="artifacts.convert",
            reason=(
                f"Backend {getattr(backend, 'name', 'unknown')!r} advertises a conversion "
                "contract but does not implement convert_artifact()."
            ),
        )

    node = TaskNode(
        task_id=new_id(),
        kind="convert_artifact",
        inputs_hash=hash_inputs(task_inputs),
        params_hash=hash_params(task_spec),
        gpu_required=False,
        metadata={"inputs": task_inputs, "spec": task_spec},
    )
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=source_job.project_id,
        recipe="artifact_conversion",
        spec={
            "artifact_id": artifact_id,
            "source_format": plan.source_format,
            "target_format": plan.target_format,
            "target_kind": target_kind,
            "options_hash": content_address(canonical_json(request.options)),
        },
        nodes=[node],
        inline=inline,
    )
    return job_id, tasks, plan.target_format, task_spec.get("provider")


async def import_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    request: ArtifactImportRequest,
) -> StageArtifact:
    project = await session.get(Project, request.project_id)
    if project is None or project.tenant_id != tenant_id:
        raise NotFoundError(f"Project {request.project_id} not found")

    if request.dataset_id is not None:
        dataset = await session.get(Dataset, request.dataset_id)
        if dataset is None or dataset.tenant_id != tenant_id:
            raise NotFoundError(f"Dataset {request.dataset_id} not found")
        if dataset.project_id != project.project_id:
            raise ValidationError("dataset_id does not belong to project_id")

    if request.recon_id is not None:
        recon = await session.get(Reconstruction, request.recon_id)
        if recon is None or recon.tenant_id != tenant_id:
            raise NotFoundError(f"Reconstruction {request.recon_id} not found")
        if recon.project_id != project.project_id:
            raise ValidationError("recon_id does not belong to project_id")
        if request.dataset_id is not None and recon.dataset_id != request.dataset_id:
            raise ValidationError("recon_id does not belong to dataset_id")

    job = Job(
        tenant_id=tenant_id,
        project_id=project.project_id,
        recipe="artifact_import",
        spec_json={
            "kind": request.kind,
            "artifact_format": request.artifact_format,
            "dataset_id": request.dataset_id,
            "recon_id": request.recon_id,
        },
        status="succeeded",
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    session.add(job)
    await session.flush()

    task = Task(
        task_id=new_id(),
        tenant_id=tenant_id,
        job_id=job.job_id,
        kind="import_artifact",
        inputs_hash=content_address(canonical_json({"project_id": project.project_id})),
        params_hash=content_address(
            canonical_json(
                {
                    "kind": request.kind,
                    "uri": request.uri,
                    "artifact_format": request.artifact_format,
                    "metadata": request.metadata,
                }
            )
        ),
        runtime_version_id="import",
        cache_key=content_address(canonical_json({"job_id": job.job_id, "task": "import"})),
        task_state_json={
            "inputs": {
                "project_id": project.project_id,
                "dataset_id": request.dataset_id,
                "recon_id": request.recon_id,
            },
            "spec": {},
        },
        outputs_ref_json={},
        status="succeeded",
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    session.add(task)
    await session.flush()

    descriptor = request.model_dump(
        mode="json",
        exclude={"project_id"},
        exclude_none=True,
    )
    outputs = artifact_service.normalize_task_outputs(task, {"artifacts": [descriptor]})
    task.outputs_ref_json = outputs
    await artifact_service.record_task_artifacts(session, task=task, outputs=outputs)

    row = (
        await session.execute(
            select(StageArtifact).where(
                StageArtifact.tenant_id == tenant_id,
                StageArtifact.task_id == task.task_id,
            )
        )
    ).scalar_one()
    # Flush, don't commit: services uniformly leave transaction
    # ownership to the caller — the request-scoped session
    # (app.db.session.get_db) commits on success.
    await session.flush()
    return row


def _managed_path(uri: str | None) -> tuple[Path | None, str | None]:
    if not uri:
        return None, "artifact has no content URI"
    candidate = _path_from_file_uri_or_local(uri)
    if candidate is None:
        return None, "remote artifact URI was not dereferenced"
    target = candidate.resolve(strict=False)
    settings = get_settings()
    allowed_roots = [
        settings.workspace_root.resolve(strict=False),
        settings.blob_root.resolve(strict=False),
        settings.s3_cache_root.resolve(strict=False),
    ]
    if not any(target == root or root in target.parents for root in allowed_roots):
        return None, "artifact content is outside sfmapi-managed storage"
    return target, None


def _issue(level: str, message: str, field: str | None = None) -> ArtifactValidationIssueOut:
    return ArtifactValidationIssueOut(level=level, field=field, message=message)


def _schema_type_matches(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int | float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_json_schema_subset(
    *,
    schema: dict[str, Any],
    value: Any,
    field: str,
) -> list[ArtifactValidationIssueOut]:
    issues: list[ArtifactValidationIssueOut] = []
    const = schema.get("const")
    if "const" in schema and value != const:
        issues.append(_issue("error", f"value must be {const!r}", field))

    raw_type = schema.get("type")
    expected_types = [raw_type] if isinstance(raw_type, str) else raw_type
    if (
        isinstance(expected_types, list)
        and expected_types
        and not any(
            isinstance(item, str) and _schema_type_matches(item, value) for item in expected_types
        )
    ):
        expected = " or ".join(str(item) for item in expected_types)
        issues.append(_issue("error", f"value must be {expected}", field))
        return issues

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for field_name in required:
                if isinstance(field_name, str) and field_name not in value:
                    child = f"{field}.{field_name}" if field else field_name
                    issues.append(
                        _issue("error", f"required field {field_name!r} is missing", child)
                    )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, child_schema in properties.items():
                if name in value and isinstance(child_schema, dict):
                    child = f"{field}.{name}" if field else str(name)
                    issues.extend(
                        _validate_json_schema_subset(
                            schema=child_schema,
                            value=value[name],
                            field=child,
                        )
                    )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(
                    _validate_json_schema_subset(
                        schema=item_schema,
                        value=item,
                        field=f"{field}[{index}]",
                    )
                )

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            issues.append(_issue("error", f"value must be at least {min_length} characters", field))
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.fullmatch(pattern, value):
            issues.append(_issue("error", f"value must match pattern {pattern!r}", field))

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            issues.append(_issue("error", f"value must be at least {minimum}", field))

    return issues


def _validate_manifest_dict(
    *,
    artifact_id: str,
    artifact_format: str,
    body: Any,
) -> list[ArtifactValidationIssueOut]:
    issues: list[ArtifactValidationIssueOut] = []
    if not isinstance(body, dict):
        return [_issue("error", "JSON artifact content must be an object")]
    format_def = artifact_vocab.CORE_ARTIFACT_FORMATS.get(artifact_format)
    if format_def is None:
        return issues
    content_format = body.get("format_id")
    if content_format is not None and content_format != artifact_format:
        issues.append(
            _issue(
                "error",
                f"content format_id {content_format!r} does not match artifact_format {artifact_format!r}",
                "format_id",
            )
        )
    content_schema_version = body.get("schema_version")
    if content_schema_version is not None and content_schema_version != format_def.schema_version:
        issues.append(
            _issue(
                "error",
                f"content schema_version {content_schema_version!r} does not match "
                f"{format_def.schema_version!r}",
                "schema_version",
            )
        )
    schema = format_def.json_schema or {}
    issues.extend(_validate_json_schema_subset(schema=schema, value=body, field="content"))
    if body.get("artifact_id") not in (None, artifact_id):
        issues.append(
            _issue("warning", "content artifact_id does not match this artifact", "artifact_id")
        )
    return issues


def _compare_file_integrity(
    path: Path,
    *,
    expected_sha256: Any,
    expected_byte_size: Any,
    field: str,
) -> list[ArtifactValidationIssueOut]:
    issues: list[ArtifactValidationIssueOut] = []
    if expected_byte_size is not None:
        if not isinstance(expected_byte_size, int) or isinstance(expected_byte_size, bool):
            issues.append(_issue("error", "byte_size must be an integer", field))
        else:
            actual_size = path.stat().st_size
            if actual_size != expected_byte_size:
                issues.append(
                    _issue(
                        "error",
                        f"byte_size {expected_byte_size} does not match actual size {actual_size}",
                        field,
                    )
                )
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256
        ):
            issues.append(_issue("error", "sha256 must be a lowercase hex SHA-256 digest", field))
        else:
            with path.open("rb") as fp:
                actual_sha, _ = stream_sha256(fp)
            if actual_sha != expected_sha256:
                issues.append(_issue("error", "sha256 does not match file content", field))
    return issues


def _file_uri_to_path(uri: str) -> Path | None:
    return _path_from_file_uri_or_local(uri)


def _resolve_manifest_file(base_dir: Path, item: dict[str, Any]) -> tuple[Path | None, str | None]:
    rel = item.get("uri") or item.get("name")
    if not isinstance(rel, str) or not rel:
        return None, "file entry requires uri or name"
    parsed = urlparse(rel)
    if parsed.scheme and parsed.scheme != "file":
        return None, "remote manifest file URI was not dereferenced"
    raw_path = _file_uri_to_path(rel)
    if raw_path is None:
        return None, "remote manifest file URI was not dereferenced"
    candidate = raw_path if raw_path.is_absolute() else base_dir / raw_path
    target = candidate.resolve(strict=False)
    try:
        target.relative_to(base_dir)
    except ValueError:
        return None, "file entry escapes artifact directory"
    return target, None


async def validate_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    artifact_id: str,
) -> ArtifactValidationOut:
    artifact = await artifact_service.get_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )
    artifact_format = _artifact_format(artifact)
    datatype = _datatype(artifact)
    issues: list[ArtifactValidationIssueOut] = []
    if not artifact_vocab.is_valid_artifact_key(artifact.kind):
        issues.append(_issue("error", "artifact kind is malformed", "kind"))
    if artifact_format is None:
        issues.append(
            _issue("warning", "artifact has no artifact_format metadata", "artifact_format")
        )
    elif not artifact_vocab.is_valid_artifact_key(artifact_format):
        issues.append(_issue("error", "artifact_format is malformed", "artifact_format"))
    elif not artifact_vocab.is_format_compatible_with_kind(artifact.kind, artifact_format):
        issues.append(
            _issue(
                "error",
                f"artifact_format {artifact_format!r} is not compatible with kind {artifact.kind!r}",
                "artifact_format",
            )
        )
    datatype_issue = _datatype_conflict_message(
        kind=artifact.kind,
        artifact_format=artifact_format,
        datatype=datatype,
    )
    if datatype_issue is not None:
        issues.append(_issue("error", datatype_issue, "datatype"))

    metadata = _metadata(artifact)
    if artifact_format in artifact_vocab.CORE_ARTIFACT_FORMATS:
        format_def = artifact_vocab.CORE_ARTIFACT_FORMATS[str(artifact_format)]
        if metadata.get("schema_version") not in (None, format_def.schema_version):
            issues.append(
                _issue(
                    "error",
                    f"metadata schema_version {metadata.get('schema_version')!r} does not match "
                    f"{format_def.schema_version!r}",
                    "schema_version",
                )
            )

    checked_content = False
    target, skip_reason = _managed_path(artifact.uri)
    if target is None:
        issues.append(_issue("warning", skip_reason or "artifact content was not checked", "uri"))
    elif target.is_dir():
        checked_content = True
        files = metadata.get("files")
        if isinstance(files, list):
            for index, item in enumerate(files):
                if not isinstance(item, dict):
                    issues.append(
                        _issue("error", "file entry must be an object", f"files[{index}]")
                    )
                    continue
                candidate, file_issue = _resolve_manifest_file(target, item)
                if candidate is None:
                    issues.append(
                        _issue(
                            "warning", file_issue or "file entry was not checked", f"files[{index}]"
                        )
                    )
                    continue
                if not candidate.exists():
                    issues.append(_issue("error", "file entry does not exist", f"files[{index}]"))
                    continue
                if not candidate.is_file():
                    issues.append(_issue("error", "file entry is not a file", f"files[{index}]"))
                    continue
                issues.extend(
                    _compare_file_integrity(
                        candidate,
                        expected_sha256=item.get("sha256"),
                        expected_byte_size=item.get("byte_size"),
                        field=f"files[{index}]",
                    )
                )
        else:
            issues.append(_issue("warning", "directory artifact has no files manifest", "files"))
    elif target.is_file():
        checked_content = True
        issues.extend(
            _compare_file_integrity(
                target,
                expected_sha256=metadata.get("sha256"),
                expected_byte_size=metadata.get("byte_size"),
                field="metadata",
            )
        )
        if artifact.media_type == "application/json" or target.suffix.lower() == ".json":
            try:
                body = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                issues.append(
                    _issue("error", f"artifact JSON content could not be parsed: {exc}", "uri")
                )
            else:
                if artifact_format is not None:
                    issues.extend(
                        _validate_manifest_dict(
                            artifact_id=artifact.artifact_id,
                            artifact_format=artifact_format,
                            body=body,
                        )
                    )
    else:
        issues.append(_issue("error", "artifact content URI does not exist", "uri"))

    return ArtifactValidationOut(
        artifact_id=artifact.artifact_id,
        valid=not any(issue.level == "error" for issue in issues),
        artifact_format=artifact_format,
        datatype=datatype,
        checked_content=checked_content,
        issues=issues,
    )
