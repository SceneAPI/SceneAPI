"""Radiance-field resource persistence and job submission."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CapabilityUnavailableError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.db.models import (
    Dataset,
    Project,
    RadianceEvaluation,
    RadianceField,
    RadianceSnapshot,
    RadianceVariant,
    Reconstruction,
)
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.schemas.api.radiance import RadianceEvaluateRequest, RadianceTrainRequest
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
    stmt = (
        select(RadianceField)
        .where(RadianceField.tenant_id == tenant_id, RadianceField.project_id == project_id)
        .order_by(RadianceField.radiance_field_id)
    )
    if page_token:
        stmt = stmt.where(RadianceField.radiance_field_id > page_token)
    stmt = stmt.limit(page_size + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    next_page_token: str | None = None
    if len(rows) > page_size:
        next_page_token = rows[page_size - 1].radiance_field_id
        rows = rows[:page_size]
    return rows, next_page_token


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
) -> list[RadianceEvaluation]:
    await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    rows = (
        (
            await session.execute(
                select(RadianceEvaluation)
                .where(
                    RadianceEvaluation.tenant_id == tenant_id,
                    RadianceEvaluation.radiance_field_id == radiance_field_id,
                )
                .order_by(RadianceEvaluation.created_at, RadianceEvaluation.evaluation_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


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
    rows = [
        row
        for row in provider_records(installed_only=True, enabled_only=True)
        if row.provider.provider_id == provider
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

    from app.adapters.backend_config import _radiance_train_option_schema

    canonical = list((_radiance_train_option_schema().get("properties") or {}).keys())
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
    if body.provider != "stub":
        eval_metrics = (
            [str(metric) for metric in body.eval.metrics]
            if body.eval is not None and body.eval.enabled
            else None
        )
        _require_radiance_provider_capabilities(
            body.provider,
            "radiance.train",
            eval_metrics,
        )
    radiance_field_id = new_id()
    name = body.name or f"radiance-{radiance_field_id[:8]}"
    spec = body.spec()
    field = RadianceField(
        radiance_field_id=radiance_field_id,
        tenant_id=tenant_id,
        project_id=project_id,
        dataset_id=body.dataset_id,
        recon_id=body.recon_id,
        name=name,
        provider=body.provider,
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
            provider=body.provider,
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
        gpu_required=body.provider != "stub",
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
    provider = body.provider or field.provider
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
        evaluation.error_json = error


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
    evaluation.metrics_json = metrics
    evaluation.artifacts_json = artifacts if isinstance(artifacts, list) else []
    evaluation.error_json = None


async def _record_embedded_evaluations(
    session: AsyncSession,
    *,
    tenant_id: str,
    outputs: dict[str, Any],
) -> None:
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
) -> None:
    field = await get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    seq = outputs.get("snapshot_seq")
    sealed_path = outputs.get("snapshot_path")
    if not isinstance(seq, int) or not isinstance(sealed_path, str) or not sealed_path:
        raise ValidationError("radiance_train output must include snapshot_seq and snapshot_path")
    summary = outputs.get("summary") if isinstance(outputs.get("summary"), dict) else {}
    field.status = "succeeded"
    field.summary_json = summary
    await _record_embedded_evaluations(session, tenant_id=tenant_id, outputs=outputs)
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
                    summary_json=item.get("summary")
                    if isinstance(item.get("summary"), dict)
                    else None,
                )
            )
