"""Radiance-field / 3DGS resource routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.api.v1._helpers import accepted_response
from sceneapi.server.core.errors import NotFoundError
from sceneapi.server.core.http import file_etag, if_none_match_hit, not_modified
from sceneapi.server.core.public_outputs import sanitize_public_outputs
from sceneapi.server.core.tenancy import current_tenant
from sceneapi.server.db.session import get_db
from sceneapi.server.schemas.api.common import Link, Page, to_out
from sceneapi.server.schemas.api.jobs import JobAcceptedResponse
from sceneapi.server.schemas.api.radiance import (
    RadianceEvaluateRequest,
    RadianceEvaluationOut,
    RadianceFieldOut,
    RadianceMetrics,
    RadianceSnapshotListResponse,
    RadianceSnapshotOut,
    RadianceTrainRequest,
)
from sceneapi.server.services import radiance_service

router = APIRouter(tags=["radiance"])

SNAPSHOT_FILE_MEDIA_TYPES = {
    "point_cloud.ply": "application/octet-stream",
    "summary.json": "application/json",
    "metadata.json": "application/json",
    "metrics.json": "application/json",
    "transforms.json": "application/json",
}

_SNAPSHOT_FILE_RESPONSE = {
    200: {
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "application/json": {"schema": {"type": "object", "additionalProperties": True}},
        },
        "description": "Radiance snapshot file bytes or JSON sidecar content.",
    }
}


def _field_links(radiance_field_id: str, project_id: str) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/radiance_fields/{radiance_field_id}"),
        "project": Link(href=f"/v1/projects/{project_id}"),
        "snapshots": Link(href=f"/v1/radiance_fields/{radiance_field_id}/snapshots"),
        "evaluations": Link(href=f"/v1/radiance_fields/{radiance_field_id}/evaluations"),
    }


def _snapshot_links(radiance_field_id: str, seq: int) -> dict[str, Link]:
    base = f"/v1/radiance_fields/{radiance_field_id}/snapshots/{seq}"
    return {
        "self": Link(href=base),
        "point_cloud": Link(href=f"{base}/point_cloud.ply"),
        "summary": Link(href=f"{base}/summary.json"),
        "metadata": Link(href=f"{base}/metadata.json"),
    }


def _evaluation_links(evaluation_id: str, radiance_field_id: str) -> dict[str, Link]:
    base = f"/v1/radiance_evaluations/{evaluation_id}"
    return {
        "self": Link(href=base),
        "radiance_field": Link(href=f"/v1/radiance_fields/{radiance_field_id}"),
        "metrics": Link(href=f"{base}/metrics"),
    }


@router.post(
    "/projects/{project_id}/radiance_fields:train",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def train_radiance_field(
    project_id: str,
    body: RadianceTrainRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    (
        job_id,
        task_ids,
        radiance_field_id,
        evaluation_id,
    ) = await radiance_service.submit_radiance_train(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        body=body,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=task_ids,
            project_id=project_id,
            dataset_id=body.dataset_id,
            provider=body.provider,
            radiance_field_id=radiance_field_id,
            radiance_evaluation_id=evaluation_id,
        )
    )


@router.post(
    "/radiance_fields/{radiance_field_id}:evaluate",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def evaluate_radiance_field(
    radiance_field_id: str,
    body: RadianceEvaluateRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    job_id, task_ids, evaluation_id = await radiance_service.submit_radiance_evaluate(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
        body=body,
    )
    field = await radiance_service.get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=task_ids,
            project_id=field.project_id,
            dataset_id=body.dataset_id or field.dataset_id,
            provider=body.provider or field.provider,
            radiance_field_id=radiance_field_id,
            radiance_evaluation_id=evaluation_id,
        )
    )


@router.get("/projects/{project_id}/radiance_fields", response_model=Page[RadianceFieldOut])
async def list_project_radiance_fields(
    project_id: str,
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[RadianceFieldOut]:
    rows, next_page_token = await radiance_service.list_radiance_fields(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        page_size=page_size,
        page_token=page_token,
    )
    return Page(
        items=[
            to_out(
                RadianceFieldOut,
                row,
                links=_field_links(row.radiance_field_id, row.project_id),
            )
            for row in rows
        ],
        next_page_token=next_page_token,
    )


@router.get("/radiance_fields/{radiance_field_id}", response_model=RadianceFieldOut)
async def get_radiance_field(
    radiance_field_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> RadianceFieldOut:
    row = await radiance_service.get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    return to_out(
        RadianceFieldOut,
        row,
        links=_field_links(row.radiance_field_id, row.project_id),
    )


@router.get(
    "/radiance_fields/{radiance_field_id}/evaluations",
    response_model=Page[RadianceEvaluationOut],
)
async def list_radiance_evaluations(
    radiance_field_id: str,
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[RadianceEvaluationOut]:
    rows, next_page_token = await radiance_service.list_radiance_evaluations(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
        page_size=page_size,
        page_token=page_token,
    )
    return Page(
        items=[
            to_out(
                RadianceEvaluationOut,
                row,
                links=_evaluation_links(row.evaluation_id, row.radiance_field_id),
            )
            for row in rows
        ],
        next_page_token=next_page_token,
    )


@router.get("/radiance_evaluations/{evaluation_id}", response_model=RadianceEvaluationOut)
async def get_radiance_evaluation(
    evaluation_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> RadianceEvaluationOut:
    row = await radiance_service.get_radiance_evaluation(
        session,
        tenant_id=tenant_id,
        evaluation_id=evaluation_id,
    )
    return to_out(
        RadianceEvaluationOut,
        row,
        links=_evaluation_links(row.evaluation_id, row.radiance_field_id),
    )


@router.get("/radiance_evaluations/{evaluation_id}/metrics", response_model=RadianceMetrics)
async def get_radiance_evaluation_metrics(
    evaluation_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> RadianceMetrics:
    row = await radiance_service.get_radiance_evaluation(
        session,
        tenant_id=tenant_id,
        evaluation_id=evaluation_id,
    )
    sanitized = sanitize_public_outputs(row.metrics_json or {})
    return RadianceMetrics.model_validate(sanitized if isinstance(sanitized, dict) else {})


async def _read_radiance_evaluation_metrics_artifact(
    evaluation_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    row = await radiance_service.get_radiance_evaluation(
        session,
        tenant_id=tenant_id,
        evaluation_id=evaluation_id,
    )
    sanitized = sanitize_public_outputs(row.metrics_json or {})
    return JSONResponse(sanitized if isinstance(sanitized, dict) else {})


@router.get("/radiance_evaluations/{evaluation_id}/artifacts/metrics.json")
async def read_radiance_evaluation_metrics_artifact(
    evaluation_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    return await _read_radiance_evaluation_metrics_artifact(
        evaluation_id=evaluation_id,
        tenant_id=tenant_id,
        session=session,
    )


@router.get("/radiance_evaluations/{evaluation_id}/artifacts/{name}")
async def read_radiance_evaluation_artifact(
    evaluation_id: str,
    name: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if name != "metrics.json":
        raise NotFoundError(f"Radiance evaluation artifact {name} not found")
    return await _read_radiance_evaluation_metrics_artifact(
        evaluation_id=evaluation_id,
        tenant_id=tenant_id,
        session=session,
    )


@router.get(
    "/radiance_fields/{radiance_field_id}/snapshots",
    response_model=RadianceSnapshotListResponse,
)
async def list_radiance_snapshots(
    radiance_field_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> RadianceSnapshotListResponse:
    rows = await radiance_service.list_radiance_snapshots(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    seqs = [row.seq for row in rows]
    return RadianceSnapshotListResponse(
        seqs=seqs,
        links={
            **{str(row.seq): _snapshot_links(radiance_field_id, row.seq) for row in rows},
            "latest": (_snapshot_links(radiance_field_id, seqs[-1]) if seqs else None),
        },
    )


@router.get(
    "/radiance_fields/{radiance_field_id}/snapshots/{seq}",
    response_model=RadianceSnapshotOut,
)
async def get_radiance_snapshot(
    radiance_field_id: str,
    seq: int,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> RadianceSnapshotOut:
    rows = await radiance_service.list_radiance_snapshots(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    for row in rows:
        if row.seq == seq:
            return to_out(
                RadianceSnapshotOut,
                row,
                links=_snapshot_links(radiance_field_id, seq),
            )
    raise NotFoundError(f"RadianceSnapshot {radiance_field_id}/{seq} not found")


@router.get(
    "/radiance_fields/{radiance_field_id}/snapshots/{seq}/{name}",
    response_class=FileResponse,
    responses=_SNAPSHOT_FILE_RESPONSE,
)
async def read_radiance_snapshot_file(
    radiance_field_id: str,
    seq: int,
    name: str,
    request: Request,
    download: bool = Query(default=False),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    if name not in SNAPSHOT_FILE_MEDIA_TYPES:
        raise NotFoundError(f"Snapshot file {name!r} not found")
    field = await radiance_service.get_radiance_field(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    rows = await radiance_service.list_radiance_snapshots(
        session,
        tenant_id=tenant_id,
        radiance_field_id=radiance_field_id,
    )
    snapshot = next((row for row in rows if row.seq == seq), None)
    if snapshot is None:
        raise NotFoundError(f"RadianceSnapshot {radiance_field_id}/{seq} not found")
    target = radiance_service.resolve_radiance_snapshot_file(
        tenant_id=tenant_id,
        field=field,
        snapshot=snapshot,
        name=name,
    )

    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(
        target,
        media_type=SNAPSHOT_FILE_MEDIA_TYPES[name],
        filename=name if download else None,
        headers=headers,
    )
