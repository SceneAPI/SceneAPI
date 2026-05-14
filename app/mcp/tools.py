"""Curated MCP tools over sfmapi services.

The MCP surface intentionally stays smaller than the REST API. Agents
get stable, intent-oriented tools instead of hundreds of low-level
endpoint-shaped operations.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.core import artifacts as artifact_vocab
from app.core.capabilities import BackendInfo, empty_capabilities
from app.core.config import get_settings
from app.core.errors import TenantViolationError
from app.db.models import Task
from app.db.session import session_scope
from app.schemas.api.artifacts import (
    ArtifactConversionPlanRequest,
    ArtifactFormatOut,
    StageArtifactOut,
)
from app.schemas.api.backend_actions import BackendActionOut
from app.schemas.api.common import Page, to_out
from app.schemas.api.jobs import JobDetail, JobOut, JobStatus, TaskOut
from app.schemas.api.projects import ProjectOut
from app.schemas.api.reconstructions import (
    ReconstructionOut,
    SnapshotListResponse,
    SubModelOut,
)
from app.services import (
    artifact_conversion_service,
    artifact_service,
    backend_action_service,
    job_progress_service,
    job_service,
    project_service,
    reconstruction_service,
)


def _dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


def resolve_tenant(tenant_id: str | None) -> str:
    """Resolve MCP's tenant scope without allowing tenant escalation."""
    settings = get_settings()
    allowed = settings.mcp_tenant_id
    if allowed is None and settings.auth_mode == "none":
        allowed = settings.default_tenant
    if allowed is None:
        raise TenantViolationError(
            "SFMAPI_MCP_TENANT_ID is required when SFMAPI_AUTH_MODE is not 'none'"
        )
    if tenant_id is not None and tenant_id != allowed:
        raise TenantViolationError(f"MCP tenant scope is {allowed!r}, not {tenant_id!r}")
    return allowed


def validate_configuration() -> None:
    """Fail fast on unsafe MCP tenant configuration."""
    resolve_tenant(None)


def _page_size(value: int, *, maximum: int = 500) -> int:
    if value < 1 or value > maximum:
        raise ValueError(f"page_size must be between 1 and {maximum}")
    return value


async def sfmapi_version() -> dict[str, Any]:
    """Return sfmapi and registered backend version information."""
    from app.api.v1.health import version

    return _dump(await version())


async def sfmapi_capabilities() -> dict[str, Any]:
    """Return feature flags for this sfmapi deployment."""
    try:
        from app.core.capabilities import detect_capabilities

        caps = detect_capabilities()
    except KeyError:
        caps = empty_capabilities(BackendInfo(name="unregistered", version="0"))
    return caps.as_dict()


async def list_backend_actions(
    page_size: int = 100,
    page_token: str | None = None,
    include_schemas: bool = False,
    provider: str | None = None,
) -> dict[str, Any]:
    """List backend-native action descriptors."""
    rows, next_page_token = backend_action_service.list_actions(
        page_size=_page_size(page_size),
        page_token=page_token,
        include_schemas=include_schemas,
        provider=provider,
    )
    page = Page[BackendActionOut](
        items=[BackendActionOut.model_validate(row) for row in rows],
        next_page_token=next_page_token,
    )
    return _dump(page)


async def get_backend_action(action_id: str, provider: str | None = None) -> dict[str, Any]:
    """Read one backend-native action descriptor."""
    return _dump(
        BackendActionOut.model_validate(
            backend_action_service.get_action(action_id, provider=provider)
        )
    )


async def list_projects(
    tenant_id: str | None = None,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List projects for a tenant using sfmapi's keyset pagination."""
    async with session_scope() as session:
        rows, next_page_token = await project_service.list_projects(
            session,
            tenant_id=resolve_tenant(tenant_id),
            page_size=_page_size(page_size),
            page_token=page_token,
        )
    page = Page[ProjectOut](
        items=[to_out(ProjectOut, row) for row in rows],
        next_page_token=next_page_token,
    )
    return _dump(page)


async def list_jobs(
    tenant_id: str | None = None,
    status: JobStatus | None = None,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List jobs, optionally filtered to one lifecycle status."""
    async with session_scope() as session:
        rows, next_page_token = await job_service.list_jobs(
            session,
            tenant_id=resolve_tenant(tenant_id),
            status=status,
            page_size=_page_size(page_size),
            page_token=page_token,
        )
    page = Page[JobOut](
        items=[to_out(JobOut, row) for row in rows],
        next_page_token=next_page_token,
    )
    return _dump(page)


async def get_job(job_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Read a job and its task rows."""
    resolved_tenant = resolve_tenant(tenant_id)
    async with session_scope() as session:
        job = await job_service.get_job(session, tenant_id=resolved_tenant, job_id=job_id)
        tasks = (
            (
                await session.execute(
                    select(Task).where(Task.job_id == job_id).order_by(Task.created_at)
                )
            )
            .scalars()
            .all()
        )
    detail = JobDetail.model_validate(job).model_copy(
        update={"tasks": [to_out(TaskOut, task) for task in tasks]}
    )
    return _dump(detail)


async def get_job_progress(job_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Read a compact progress snapshot for one job."""
    async with session_scope() as session:
        progress = await job_progress_service.get_job_progress(
            session,
            tenant_id=resolve_tenant(tenant_id),
            job_id=job_id,
        )
    return _dump(progress)


async def list_artifacts(
    tenant_id: str | None = None,
    job_id: str | None = None,
    recon_id: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    name: str | None = None,
    page_size: int = 100,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List typed stage artifacts for one job or reconstruction."""
    if (job_id is None) == (recon_id is None):
        raise ValueError("pass exactly one of job_id or recon_id")
    resolved_tenant = resolve_tenant(tenant_id)
    async with session_scope() as session:
        if job_id is not None:
            await job_service.get_job(session, tenant_id=resolved_tenant, job_id=job_id)
            rows, next_page_token = await artifact_service.list_job_artifacts(
                session,
                tenant_id=resolved_tenant,
                job_id=job_id,
                page_size=_page_size(page_size),
                page_token=page_token,
                kind=kind,
                task_id=task_id,
                name=name,
            )
        else:
            await reconstruction_service.get_reconstruction(
                session,
                tenant_id=resolved_tenant,
                recon_id=str(recon_id),
            )
            rows, next_page_token = await artifact_service.list_reconstruction_artifacts(
                session,
                tenant_id=resolved_tenant,
                recon_id=str(recon_id),
                page_size=_page_size(page_size),
                page_token=page_token,
                kind=kind,
                task_id=task_id,
                name=name,
            )
    page = Page[StageArtifactOut](
        items=[to_out(StageArtifactOut, row) for row in rows],
        next_page_token=next_page_token,
    )
    return _dump(page)


async def get_artifact(artifact_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Read one typed stage artifact by id."""
    async with session_scope() as session:
        artifact = await artifact_service.get_artifact(
            session,
            tenant_id=resolve_tenant(tenant_id),
            artifact_id=artifact_id,
        )
    return _dump(to_out(StageArtifactOut, artifact))


async def list_artifact_formats() -> dict[str, Any]:
    """List sfmapi core artifact interchange formats."""
    rows = sorted(artifact_vocab.CORE_ARTIFACT_FORMATS.values(), key=lambda item: item.format_id)
    page = Page[ArtifactFormatOut](
        items=[ArtifactFormatOut.model_validate(row) for row in rows],
        next_page_token=None,
    )
    return _dump(page)


async def validate_artifact(artifact_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Validate an artifact descriptor and local managed bytes."""
    async with session_scope() as session:
        report = await artifact_conversion_service.validate_artifact(
            session,
            tenant_id=resolve_tenant(tenant_id),
            artifact_id=artifact_id,
        )
    return _dump(report)


async def plan_artifact_conversion(
    artifact_id: str,
    to_format: str | None = None,
    accepted_formats: list[str] | None = None,
    require_lossless: bool = False,
    provider: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Plan a conversion path for one artifact without submitting work."""
    async with session_scope() as session:
        plan = await artifact_conversion_service.get_conversion_plan(
            session,
            tenant_id=resolve_tenant(tenant_id),
            artifact_id=artifact_id,
            request=ArtifactConversionPlanRequest(
                to_format=to_format,
                accepted_formats=accepted_formats or [],
                require_lossless=require_lossless,
                provider=provider,
            ),
        )
    return _dump(plan)


async def get_reconstruction(recon_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Read one reconstruction metadata row."""
    async with session_scope() as session:
        recon = await reconstruction_service.get_reconstruction(
            session,
            tenant_id=resolve_tenant(tenant_id),
            recon_id=recon_id,
        )
    return _dump(to_out(ReconstructionOut, recon))


async def list_submodels(
    recon_id: str,
    tenant_id: str | None = None,
    page_size: int = 100,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List submodels for one reconstruction."""
    async with session_scope() as session:
        rows, next_page_token = await reconstruction_service.list_submodels(
            session,
            tenant_id=resolve_tenant(tenant_id),
            recon_id=recon_id,
            page_size=_page_size(page_size),
            page_token=page_token,
        )
    page = Page[SubModelOut](
        items=[to_out(SubModelOut, row) for row in rows],
        next_page_token=next_page_token,
    )
    return _dump(page)


# Curated catalog of the portable decomposed-pipeline stage routes.
# Each entry maps a stage to its HTTP route template, the resource it
# scopes to, and the capability flag(s) that gate it. Surfaced through
# MCP so an agent can answer "what stages can I run, and how?" without
# reverse-engineering the REST surface. Keep in sync with
# ``app/api/v1/{recon_stages,dataset_stages,sfm_stages,localize}.py``.
_PORTABLE_STAGES: tuple[dict[str, Any], ...] = (
    {
        "stage": "features",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}/features",
        "capability": "features.extract.{type}",
    },
    {
        "stage": "matches",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}/matches",
        "capability": "pairs.{strategy} + matchers.{type}",
    },
    {
        "stage": "verify",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}/verify",
        "capability": "matches.verify",
    },
    {
        "stage": "bundleAdjust",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:bundleAdjust",
        "capability": "ba.{mode}",
    },
    {
        "stage": "triangulate",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:triangulate",
        "capability": "triangulate.retri",
    },
    {
        "stage": "poseGraphOptimize",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:poseGraphOptimize",
        "capability": "pgo.optimize",
    },
    {
        "stage": "export",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:export",
        "capability": "export.{format}",
    },
    {
        "stage": "relocalize",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:relocalize",
        "capability": "relocalize.images",
    },
    {
        "stage": "undistort",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}:undistort",
        "capability": "image.undistort",
    },
    {
        "stage": "georegister",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}/georegister",
        "capability": "georegister.sim3 | georegister.gps",
    },
    {
        "stage": "localize",
        "scope": "reconstruction",
        "method": "POST",
        "route": "/v1/reconstructions/{recon_id}/localize",
        "capability": "localize.from_memory",
    },
    {
        "stage": "buildVocabTree",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}:buildVocabTree",
        "capability": "index.vocab_tree",
    },
    {
        "stage": "configureRig",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}:configureRig",
        "capability": "rigs.configure",
    },
    {
        "stage": "estimateTwoView",
        "scope": "dataset",
        "method": "POST",
        "route": "/v1/datasets/{dataset_id}:estimateTwoView",
        "capability": "geometry.two_view",
    },
)


async def list_portable_stages() -> dict[str, Any]:
    """List the portable decomposed-pipeline stage routes.

    Each entry carries the stage name, the resource it scopes to
    (dataset / reconstruction), the HTTP route template, and the
    capability flag(s) that gate it. Cross-reference with
    :func:`sfmapi_capabilities` to see which stages this deployment's
    backend can actually run.
    """
    return {"items": [dict(stage) for stage in _PORTABLE_STAGES]}


async def list_snapshots(recon_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """List sealed snapshot sequence numbers for one reconstruction."""
    resolved_tenant = resolve_tenant(tenant_id)
    async with session_scope() as session:
        recon = await reconstruction_service.get_reconstruction(
            session,
            tenant_id=resolved_tenant,
            recon_id=recon_id,
        )
        from app.core.paths import Paths

        seqs = reconstruction_service.list_snapshot_seqs(
            Paths(),
            resolved_tenant,
            recon.project_id,
            recon.recon_id,
        )
    base = f"/v1/reconstructions/{recon_id}/snapshots"
    response = SnapshotListResponse(
        seqs=seqs,
        links={
            **{str(seq): {"self": {"href": f"{base}/{seq}"}} for seq in seqs},
            "latest": {"self": {"href": f"{base}/{seqs[-1]}"}} if seqs else None,
        },
    )
    return _dump(response)


TOOLS = (
    sfmapi_version,
    sfmapi_capabilities,
    list_portable_stages,
    list_backend_actions,
    get_backend_action,
    list_projects,
    list_jobs,
    get_job,
    get_job_progress,
    list_artifacts,
    get_artifact,
    list_artifact_formats,
    validate_artifact,
    plan_artifact_conversion,
    get_reconstruction,
    list_submodels,
    list_snapshots,
)
