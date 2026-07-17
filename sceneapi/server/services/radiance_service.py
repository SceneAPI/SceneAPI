"""Radiance-field resource persistence and job submission."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.errors import CapabilityUnavailableError, NotFoundError, ValidationError
from sceneapi.server.core.ids import new_id
from sceneapi.server.core.paths import Paths
from sceneapi.server.core.public_outputs import sanitize_public_error, sanitize_public_outputs
from sceneapi.server.db.models import (
    Dataset,
    Project,
    RadianceEvaluation,
    RadianceField,
    RadianceSnapshot,
    RadianceVariant,
    Reconstruction,
)
from sceneapi.server.db.pagination import paginate_keyset
from sceneapi.server.orchestrator.dag import TaskNode, hash_inputs, hash_params
from sceneapi.server.orchestrator.scheduler import submit_job_dag
from sceneapi.server.schemas.api.radiance import RadianceEvaluateRequest, RadianceTrainRequest
from sceneapi.server.services.provider_routing_service import apply_provider_resolution
from sfm_hub.routing import provider_records


async def get_radiance_field(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
) -> RadianceField:
    result = await session.execute(
        select(RadianceField).where(
            RadianceField.tenant_id == tenant_id,
            RadianceField.radiance_field_id == radiance_field_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"RadianceField {radiance_field_id} not found")
    return row


async def list_radiance_fields(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    page_size: int,
    page_token: str | None,
) -> tuple[list[RadianceField], str | None]:
    stmt = select(RadianceField).where(
        RadianceField.tenant_id == tenant_id, RadianceField.project_id == project_id
    )
    return await paginate_keyset(
        session,
        stmt,
        pk=RadianceField.radiance_field_id,
        page_size=page_size,
        page_token=page_token,
    )


async def list_radiance_snapshots(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
) -> list[RadianceSnapshot]:
    await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    rows = (
        (
            await session.execute(
                select(RadianceSnapshot)
                .where(
                    RadianceSnapshot.tenant_id == tenant_id,
                    RadianceSnapshot.radiance_field_id == radiance_field_id,
                )
                .order_by(RadianceSnapshot.seq)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_radiance_evaluation(
    session: AsyncSession,
    *,
    tenant_id: str,
    evaluation_id: str,
) -> RadianceEvaluation:
    result = await session.execute(
        select(RadianceEvaluation).where(
            RadianceEvaluation.tenant_id == tenant_id,
            RadianceEvaluation.evaluation_id == evaluation_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"RadianceEvaluation {evaluation_id} not found")
    return row


async def list_radiance_evaluations(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
    page_size: int,
    page_token: str | None,
) -> tuple[list[RadianceEvaluation], str | None]:
    await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    stmt = select(RadianceEvaluation).where(
        RadianceEvaluation.tenant_id == tenant_id,
        RadianceEvaluation.radiance_field_id == radiance_field_id,
    )
    return await paginate_keyset(
        session,
        stmt,
        pk=RadianceEvaluation.evaluation_id,
        page_size=page_size,
        page_token=page_token,
    )


async def _require_project(session: AsyncSession, *, tenant_id: str, project_id: str) -> Project:
    row = await session.get(Project, project_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFoundError(f"Project {project_id} not found")
    return row


async def _validate_input(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    body: RadianceTrainRequest,
) -> None:
    await _require_project(session, tenant_id=tenant_id, project_id=project_id)
    if body.dataset_id is not None:
        dataset = await session.get(Dataset, body.dataset_id)
        if dataset is None or dataset.tenant_id != tenant_id or dataset.project_id != project_id:
            raise NotFoundError(f"Dataset {body.dataset_id} not found")
    if body.recon_id is not None:
        recon = await session.get(Reconstruction, body.recon_id)
        if recon is None or recon.tenant_id != tenant_id or recon.project_id != project_id:
            raise NotFoundError(f"Reconstruction {body.recon_id} not found")


def _require_radiance_provider_capabilities(
    provider: str,
    capability: str,
    metrics: list[str] | None = None,
) -> None:
    if provider == "stub":
        return
    bare_provider, sep, plugin_id = provider.partition("@")
    rows = [
        row
        for row in provider_records(installed_only=True, enabled_only=True)
        if row.provider.provider_id == bare_provider and (not sep or row.plugin_id == plugin_id)
    ]
    if not rows:
        raise CapabilityUnavailableError(
            capability=capability,
            reason=f"provider {provider!r} is not installed and enabled as a radiance plugin",
        )
    if len(rows) > 1:
        plugin_ids = ", ".join(sorted({row.plugin_id for row in rows}))
        raise ValidationError(
            f"provider {provider!r} is ambiguous across installed plugins: {plugin_ids}"
        )
    if "container_service" not in rows[0].runtime_modes:
        raise CapabilityUnavailableError(
            capability=capability,
            reason=(
                f"provider {provider!r} advertises {capability} but does not "
                "declare a container_service runtime"
            ),
        )
    capabilities = set(rows[0].provider.capabilities)
    required = [capability, *(f"radiance.metrics.{metric}" for metric in metrics or [])]
    for required_capability in required:
        if required_capability not in capabilities:
            raise CapabilityUnavailableError(
                capability=required_capability,
                reason=f"provider {provider!r} does not advertise {required_capability}",
            )


def _check_radiance_canonical_typos(backend_options: dict[str, Any]) -> None:
    """Reject likely typos on the canonical ``radiance.train`` knobs.

    radiance backend_options legitimately carries engine-specific extras
    (``model.primitive``, ``image_scale_factor``, ``dataset_path``, ...), so we
    cannot strict-validate against the canonical schema -- doing so would
    reject every splatting plugin's real keys. But a misspelling like
    ``num_gaussain`` silently slips through and wastes a GPU run. This guard
    flags close-match-but-not-equal keys against the canonical set only;
    unrecognised keys far from any canonical name pass through untouched.
    """
    if not backend_options:
        return
    import difflib

    from sceneapi.server.adapters.backend_config import radiance_train_option_schema

    canonical = list((radiance_train_option_schema().get("properties") or {}).keys())
    if not canonical:
        return
    suspicious: list[tuple[str, str]] = []
    for key in backend_options:
        if key in canonical:
            continue
        matches = difflib.get_close_matches(key, canonical, n=1, cutoff=0.85)
        if matches:
            suspicious.append((str(key), matches[0]))
    if suspicious:
        detail = "; ".join(f"{k!r} -> did you mean {v!r}?" for k, v in suspicious)
        raise ValidationError(
            f"radiance backend_options has possibly-misspelled canonical knob(s): {detail}"
        )


def _radiance_field_root(*, tenant_id: str, field: RadianceField) -> Path:
    return Paths().radiance_field_root(
        tenant_id,
        field.project_id,
        field.radiance_field_id,
    )


def _radiance_snapshot_root(*, tenant_id: str, field: RadianceField, seq: int) -> Path:
    return _radiance_field_root(tenant_id=tenant_id, field=field) / "snapshots" / f"{seq:08d}"


def _file_uri_path(value: str) -> str | None:
    if not value.startswith("file://"):
        return None
    parsed = urlparse(value)
    path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc != "localhost":
        if len(parsed.netloc) == 2 and parsed.netloc[1] == ":":
            path = parsed.netloc + path
        else:
            path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
        path = path[1:]
    return path


def _is_nonlocal_uri(value: str) -> bool:
    scheme_end = value.find("://")
    if scheme_end <= 0:
        return False
    if len(value) >= 3 and value[1] == ":" and value[2] in {"\\", "/"}:
        return False
    scheme = value[:scheme_end]
    if not all(ch.isalnum() or ch in "+-." for ch in scheme):
        return False
    return scheme != "file"


def _valid_snapshot_seq(value: object) -> bool:
    return type(value) is int and value >= 1


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_symlink_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _reject_snapshot_links(root: Path) -> None:
    stack = [root]
    while stack:
        current = stack.pop()
        if _is_symlink_or_junction(current):
            raise ValidationError(
                "radiance_train snapshot_path must not contain symlinks or junctions"
            )
        try:
            children = list(current.iterdir())
        except OSError as exc:
            raise ValidationError(
                "radiance_train snapshot_path could not be inspected for symlinks"
            ) from exc
        for child in children:
            if _is_symlink_or_junction(child):
                try:
                    rel = child.relative_to(root)
                except ValueError:
                    rel = child
                raise ValidationError(
                    f"radiance_train snapshot_path must not contain symlinks or junctions: {rel}"
                )
            if child.is_dir():
                stack.append(child)


def _write_json_if_absent(path: Path, payload: dict[str, Any]) -> None:
    if _is_symlink_or_junction(path):
        raise ValidationError(f"radiance_train metadata path is a symlink or junction: {path.name}")
    if path.exists():
        return
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _public_dict(value: Any) -> dict[str, Any]:
    public = sanitize_public_outputs(value if isinstance(value, dict) else {})
    return public if isinstance(public, dict) else {}


def _public_artifact_list(value: Any) -> list[Any]:
    """Sanitize a provider's artifact-descriptor rows.

    Wrapping the rows under an ``"artifacts"`` key routes them through the
    sanitizer's artifact-descriptor branch: a credential-free non-local
    ``uri`` (``mem://``, ``s3://``, public https) is preserved and the row's
    ``metadata`` is cleaned. Sanitizing the bare list instead would take the
    generic-text branch, which redacts every ``uri`` outright and leaves the
    stored artifact rows pointing at nothing.
    """
    rows = value if isinstance(value, list) else []
    public = sanitize_public_outputs({"artifacts": rows})
    artifacts = public.get("artifacts") if isinstance(public, dict) else None
    return artifacts if isinstance(artifacts, list) else []


def _seal_radiance_snapshot_path(
    *,
    tenant_id: str,
    field: RadianceField,
    seq: int,
    provider_path: str,
    summary: dict[str, Any],
    outputs: dict[str, Any],
) -> str:
    """Return a managed, API-safe snapshot root for a provider result.

    Providers may return temporary local directories or opaque URIs. The API
    only serves files from the tenant/project-managed radiance tree, so local
    provider directories are copied there and non-local URIs are reduced to a
    managed metadata-only snapshot directory.
    """
    managed_root = _radiance_snapshot_root(tenant_id=tenant_id, field=field, seq=seq)
    managed_root.mkdir(parents=True, exist_ok=True)
    local_provider_path = _file_uri_path(provider_path) or provider_path
    provider = Path(local_provider_path)
    if provider.is_absolute():
        if not provider.exists() and not provider.is_symlink():
            raise ValidationError(
                "radiance_train snapshot_path must reference an existing directory"
            )
        if _is_symlink_or_junction(provider):
            raise ValidationError("radiance_train snapshot_path must not be a symlink or junction")
        source = provider.resolve()
        if not source.is_dir():
            raise ValidationError("radiance_train snapshot_path must reference a directory")
        field_root = _radiance_field_root(tenant_id=tenant_id, field=field).resolve()
        managed_resolved = managed_root.resolve()
        live_root = (field_root / "_live").resolve()
        if (
            source != managed_resolved
            and not _path_is_under(source, managed_resolved)
            and not _path_is_under(source, live_root)
        ):
            raise ValidationError(
                "radiance_train snapshot_path must stay under the current radiance field root"
            )
        _reject_snapshot_links(source)
        if source != managed_resolved:
            shutil.copytree(source, managed_root, symlinks=True, dirs_exist_ok=True)
    elif not _is_nonlocal_uri(provider_path):
        raise ValidationError(
            "radiance_train snapshot_path must be an absolute local path "
            "or an explicit non-local URI"
        )
    _write_json_if_absent(managed_root / "summary.json", summary)
    metadata = outputs.get("metadata")
    if isinstance(metadata, dict):
        _write_json_if_absent(managed_root / "metadata.json", _public_dict(metadata))
    return str(managed_root.resolve())


def resolve_radiance_snapshot_file(
    *,
    tenant_id: str,
    field: RadianceField,
    snapshot: RadianceSnapshot,
    name: str,
) -> Path:
    root = Path(snapshot.sealed_path).resolve()
    managed_root = _radiance_field_root(tenant_id=tenant_id, field=field).resolve()
    if not _path_is_under(root, managed_root):
        raise NotFoundError(f"Snapshot file {name!r} not found")
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise NotFoundError(f"Snapshot file {name!r} not found") from exc
    if not target.is_file():
        raise NotFoundError(f"Snapshot file {name!r} not found")
    return target


async def submit_radiance_train(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    body: RadianceTrainRequest,
    inline: bool = False,
) -> tuple[str, list[str], str, str | None]:
    await _validate_input(session, tenant_id=tenant_id, project_id=project_id, body=body)
    _check_radiance_canonical_typos(body.backend_options)
    spec = body.spec()
    if body.provider != "stub":
        apply_provider_resolution(
            spec,
            stage="radiance",
            capability="radiance.train",
            project_id=project_id,
            workspace=str(Paths().workspace_root),
        )
    provider = str(spec.get("provider") or "stub")
    spec["provider"] = provider
    body.provider = provider
    if provider != "stub":
        eval_metrics = (
            [str(metric) for metric in body.eval.metrics]
            if body.eval is not None and body.eval.enabled
            else None
        )
        _require_radiance_provider_capabilities(
            provider,
            "radiance.train",
            eval_metrics,
        )
    radiance_field_id = new_id()
    name = body.name or f"radiance-{radiance_field_id[:8]}"
    field = RadianceField(
        radiance_field_id=radiance_field_id,
        tenant_id=tenant_id,
        project_id=project_id,
        dataset_id=body.dataset_id,
        recon_id=body.recon_id,
        name=name,
        provider=provider,
        method=body.method,
        status="running",
        spec_json=spec,
    )
    session.add(field)
    await session.flush()

    evaluation: RadianceEvaluation | None = None
    if body.eval is not None and body.eval.enabled and body.eval.final:
        eval_config = body.eval.model_dump(mode="json")
        evaluation = RadianceEvaluation(
            tenant_id=tenant_id,
            radiance_field_id=radiance_field_id,
            snapshot_seq=1,
            dataset_id=body.dataset_id,
            provider=provider,
            method=body.method,
            split=str(eval_config["split"]),
            status="running",
            config_json=eval_config,
        )
        session.add(evaluation)
        await session.flush()

    inputs = {
        "project_id": project_id,
        "radiance_field_id": radiance_field_id,
        "dataset_id": body.dataset_id,
        "recon_id": body.recon_id,
    }
    if evaluation is not None:
        inputs["evaluation_id"] = evaluation.evaluation_id
    node = TaskNode(
        task_id=new_id(),
        kind="radiance_train",
        inputs_hash=hash_inputs(inputs),
        params_hash=hash_params(spec),
        depends_on=[],
        gpu_required=provider != "stub",
        metadata={"inputs": inputs, "spec": spec},
    )
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="radiance.train",
        spec={
            "radiance_field_id": radiance_field_id,
            **spec,
        },
        nodes=[node],
        inline=inline,
    )
    if evaluation is not None:
        evaluation.job_id = job_id
    return (
        job_id,
        [task.task_id for task in tasks],
        radiance_field_id,
        evaluation.evaluation_id if evaluation is not None else None,
    )


async def submit_radiance_evaluate(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
    body: RadianceEvaluateRequest,
    inline: bool = False,
) -> tuple[str, list[str], str]:
    field = await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    snapshots = await list_radiance_snapshots(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    if not snapshots:
        raise ValidationError("radiance_field has no snapshots to evaluate")
    snapshot_seq = body.snapshot_seq or snapshots[-1].seq
    if all(row.seq != snapshot_seq for row in snapshots):
        raise NotFoundError(f"RadianceSnapshot {radiance_field_id}/{snapshot_seq} not found")
    dataset_id = body.dataset_id or field.dataset_id
    if body.dataset_id is not None:
        dataset = await session.get(Dataset, body.dataset_id)
        if (
            dataset is None
            or dataset.tenant_id != tenant_id
            or dataset.project_id != field.project_id
        ):
            raise NotFoundError(f"Dataset {body.dataset_id} not found")
    provider = body.provider or field.provider
    if provider != "stub":
        resolved_spec = {"provider": provider}
        apply_provider_resolution(
            resolved_spec,
            stage="radiance",
            capability="radiance.evaluate",
            project_id=field.project_id,
            workspace=str(Paths().workspace_root),
        )
        provider = str(resolved_spec.get("provider") or provider)
    method = body.method or field.method
    config = body.eval.model_dump(mode="json")
    config["enabled"] = True
    _require_radiance_provider_capabilities(
        provider,
        "radiance.evaluate",
        [str(metric) for metric in config.get("metrics") or []],
    )
    evaluation = RadianceEvaluation(
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
        snapshot_seq=snapshot_seq,
        dataset_id=dataset_id,
        provider=provider,
        method=method,
        split=str(config["split"]),
        status="running",
        config_json=config,
    )
    session.add(evaluation)
    await session.flush()

    spec = {
        "provider": provider,
        "method": method,
        "snapshot_seq": snapshot_seq,
        "eval": config,
        "backend_options": dict(body.backend_options),
        "request_id": body.request_id,
    }
    inputs = {
        "project_id": field.project_id,
        "radiance_field_id": radiance_field_id,
        "evaluation_id": evaluation.evaluation_id,
        "snapshot_seq": snapshot_seq,
        "dataset_id": dataset_id,
    }
    node = TaskNode(
        task_id=new_id(),
        kind="radiance_eval",
        inputs_hash=hash_inputs(inputs),
        params_hash=hash_params(spec),
        depends_on=[],
        gpu_required=provider != "stub",
        metadata={"inputs": inputs, "spec": spec},
    )
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=field.project_id,
        recipe="radiance.evaluate",
        spec={
            "radiance_field_id": radiance_field_id,
            "evaluation_id": evaluation.evaluation_id,
            **spec,
        },
        nodes=[node],
        inline=inline,
    )
    evaluation.job_id = job_id
    return job_id, [task.task_id for task in tasks], evaluation.evaluation_id


async def mark_radiance_field_status(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
    status: str,
    error: str | None = None,
) -> None:
    field = await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    field.status = status
    if error:
        field.summary_json = {**(field.summary_json or {}), "error": error}


async def mark_radiance_evaluation_status(
    session: AsyncSession,
    *,
    tenant_id: str,
    evaluation_id: str,
    status: str,
    error: dict[str, Any] | None = None,
) -> None:
    evaluation = await get_radiance_evaluation(
        session,
        tenant_id=tenant_id,
        evaluation_id=evaluation_id,
    )
    evaluation.status = status
    if error is not None:
        evaluation.error_json = sanitize_public_error(error)


async def record_radiance_evaluation_result(
    session: AsyncSession,
    *,
    tenant_id: str,
    evaluation_id: str,
    outputs: dict[str, Any],
) -> None:
    evaluation = await get_radiance_evaluation(
        session,
        tenant_id=tenant_id,
        evaluation_id=evaluation_id,
    )
    metrics = outputs.get("metrics")
    if not isinstance(metrics, dict):
        raise ValidationError("radiance_eval output must include metrics")
    artifacts = outputs.get("artifacts")
    evaluation.status = "succeeded"
    evaluation.metrics_json = _public_dict(metrics)
    evaluation.artifacts_json = _public_artifact_list(artifacts)
    evaluation.error_json = None


async def _record_embedded_evaluations(
    session: AsyncSession,
    *,
    tenant_id: str,
    expected_evaluation_id: str | None,
    expected_radiance_field_id: str,
    outputs: dict[str, Any],
) -> None:
    if not expected_evaluation_id:
        return
    evaluations = outputs.get("evaluations")
    if not isinstance(evaluations, list):
        return
    for item in evaluations:
        if not isinstance(item, dict):
            continue
        evaluation_id = item.get("evaluation_id")
        metrics = item.get("metrics")
        if not isinstance(evaluation_id, str) or not isinstance(metrics, dict):
            continue
        if evaluation_id != expected_evaluation_id:
            raise ValidationError(
                "radiance_train output includes metrics for an unexpected evaluation_id"
            )
        item_field_id = item.get("radiance_field_id")
        if isinstance(item_field_id, str) and item_field_id != expected_radiance_field_id:
            raise ValidationError(
                "radiance_train output includes metrics for a different radiance_field_id"
            )
        await record_radiance_evaluation_result(
            session,
            tenant_id=tenant_id,
            evaluation_id=evaluation_id,
            outputs=item,
        )


async def record_radiance_train_result(
    session: AsyncSession,
    *,
    tenant_id: str,
    radiance_field_id: str,
    outputs: dict[str, Any],
    expected_evaluation_id: str | None = None,
) -> None:
    field = await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    seq = outputs.get("snapshot_seq")
    sealed_path = outputs.get("snapshot_path")
    if not _valid_snapshot_seq(seq) or not isinstance(sealed_path, str) or not sealed_path:
        raise ValidationError("radiance_train output must include snapshot_seq and snapshot_path")
    summary = _public_dict(outputs.get("summary"))
    sealed_path = _seal_radiance_snapshot_path(
        tenant_id=tenant_id,
        field=field,
        seq=seq,
        provider_path=sealed_path,
        summary=summary,
        outputs=outputs,
    )
    field.status = "succeeded"
    field.summary_json = summary
    output_evaluation_id = outputs.get("evaluation_id")
    if (
        expected_evaluation_id is not None
        and isinstance(output_evaluation_id, str)
        and output_evaluation_id != expected_evaluation_id
    ):
        raise ValidationError("radiance_train output includes an unexpected evaluation_id")
    await _record_embedded_evaluations(
        session,
        tenant_id=tenant_id,
        expected_evaluation_id=expected_evaluation_id,
        expected_radiance_field_id=radiance_field_id,
        outputs=outputs,
    )
    existing = (
        await session.execute(
            select(RadianceSnapshot).where(
                RadianceSnapshot.tenant_id == tenant_id,
                RadianceSnapshot.radiance_field_id == radiance_field_id,
                RadianceSnapshot.seq == seq,
            )
        )
    ).scalar_one_or_none()
    snapshot = existing
    if snapshot is None:
        snapshot = RadianceSnapshot(
            tenant_id=tenant_id,
            radiance_field_id=radiance_field_id,
            seq=seq,
            sealed_path=sealed_path,
            summary_json=summary,
        )
        session.add(snapshot)
        await session.flush()
    else:
        snapshot.sealed_path = sealed_path
        snapshot.summary_json = summary
    variants = outputs.get("variants")
    if isinstance(variants, list):
        for item in variants:
            if not isinstance(item, dict):
                continue
            fmt = item.get("format")
            if not isinstance(fmt, str) or not fmt:
                continue
            session.add(
                RadianceVariant(
                    tenant_id=tenant_id,
                    snapshot_id=snapshot.snapshot_id,
                    format=fmt,
                    uri=item.get("uri") if isinstance(item.get("uri"), str) else None,
                    media_type=item.get("media_type")
                    if isinstance(item.get("media_type"), str)
                    else None,
                    summary_json=_public_dict(item.get("summary"))
                    if isinstance(item.get("summary"), dict)
                    else None,
                )
            )
