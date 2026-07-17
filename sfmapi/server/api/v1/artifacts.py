"""Stage artifact resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.api.v1._helpers import accepted_response
from sfmapi.server.core import artifacts as artifact_vocab
from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import NotFoundError, ValidationError
from sfmapi.server.core.http import file_etag, if_none_match_hit, not_modified
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.artifacts import (
    ArtifactConversionPlanOut,
    ArtifactConversionPlanRequest,
    ArtifactConvertRequest,
    ArtifactFormatOut,
    ArtifactImportRequest,
    ArtifactKindOut,
    ArtifactValidationOut,
    StageArtifactOut,
)
from sfmapi.server.schemas.api.common import Link, Page
from sfmapi.server.schemas.api.jobs import JobAcceptedResponse
from sfmapi.server.services import artifact_conversion_service, artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_BINARY_SCHEMA = {"schema": {"type": "string", "format": "binary"}}
_ARTIFACT_CONTENT_MEDIA_TYPES = sorted(
    {
        "application/octet-stream",
        *(
            media_type
            for fmt in artifact_vocab.CORE_ARTIFACT_FORMATS.values()
            for media_type in fmt.media_types
        ),
    }
)
_BINARY_RESPONSE = {
    200: {
        "content": {media_type: _BINARY_SCHEMA for media_type in _ARTIFACT_CONTENT_MEDIA_TYPES},
        "description": "Artifact content bytes.",
    }
}

_DIRECTORY_ARTIFACT_KINDS = {
    "radiance.snapshot",
    "reconstruction.snapshot",
    "reconstruction.submodel",
    "reconstruction.sparse.v1",
}


def _local_artifact_path(uri: str | None) -> Path | None:
    if not uri:
        return None
    if uri.startswith("/v1/"):
        return None
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
    elif "://" in uri:
        return None
    return Path(uri)


def _artifact_uri_is_local_directory(artifact: Any) -> bool:
    uri = getattr(artifact, "uri", None)
    candidate = _local_artifact_path(uri)
    if candidate is None:
        return False
    if candidate.is_file():
        return False
    return bool(candidate.is_dir())


def _artifact_uri_has_public_content(artifact: Any) -> bool:
    try:
        _resolve_managed_artifact_file(getattr(artifact, "uri", None))
    except (NotFoundError, OSError, ValidationError):
        return False
    return True


def artifact_links(artifact: Any) -> dict[str, Link]:
    artifact_id = artifact.artifact_id
    links = {
        "self": Link(href=f"/v1/artifacts/{artifact_id}"),
        "job": Link(href=f"/v1/jobs/{artifact.job_id}"),
    }
    if _artifact_uri_has_public_content(artifact):
        links["content"] = Link(href=f"/v1/artifacts/{artifact_id}/content")
    recon_id = getattr(artifact, "recon_id", None)
    if recon_id is not None:
        links["reconstruction"] = Link(href=f"/v1/reconstructions/{recon_id}")
    return links


def artifact_out(artifact: Any) -> StageArtifactOut:
    links = artifact_links(artifact)
    content_href = links["content"].href if "content" in links else ""
    content_path = _local_artifact_path(getattr(artifact, "uri", None))
    out = StageArtifactOut.model_validate(
        artifact,
        context={
            "public_content_href": content_href,
            "public_content_path": str(content_path) if content_path is not None else "",
        },
    ).model_copy(update={"links": links})
    uri = getattr(artifact, "uri", None)
    if "content" in links:
        out = out.model_copy(update={"uri": links["content"].href})
    elif _local_artifact_path(uri) is not None:
        out = out.model_copy(update={"uri": None})
    return out


@router.get("/kinds", response_model=Page[ArtifactKindOut])
async def list_artifact_kinds() -> Page[ArtifactKindOut]:
    """List sfmapi's reserved core artifact kinds.

    Backends may still emit namespaced extension kinds. The core list
    gives clients stable semantics for portable stage inputs.
    """
    rows = sorted(artifact_vocab.CORE_ARTIFACT_KINDS.values(), key=lambda item: item.kind)
    return Page(
        items=[ArtifactKindOut.model_validate(row) for row in rows],
        next_page_token=None,
    )


@router.get("/formats", response_model=Page[ArtifactFormatOut])
async def list_artifact_formats() -> Page[ArtifactFormatOut]:
    """List sfmapi's reserved core artifact interchange formats.

    Backend-native formats are exposed by backend artifact contracts,
    not reserved here. Core formats are the stable interchange surface
    clients can rely on across backend implementations.
    """
    rows = sorted(artifact_vocab.CORE_ARTIFACT_FORMATS.values(), key=lambda item: item.format_id)
    return Page(
        items=[ArtifactFormatOut.model_validate(row) for row in rows],
        next_page_token=None,
    )


@router.post("/{artifact_id}:conversionPlan", response_model=ArtifactConversionPlanOut)
async def plan_artifact_conversion(
    artifact_id: str,
    body: ArtifactConversionPlanRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ArtifactConversionPlanOut:
    """Plan conversion from this artifact's current format to a target format."""
    return await artifact_conversion_service.get_conversion_plan(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        request=body,
    )


@router.post(
    "/{artifact_id}:convert",
    response_model=JobAcceptedResponse,
    status_code=202,
)
async def convert_artifact(
    artifact_id: str,
    body: ArtifactConvertRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Submit an artifact format conversion as a normal sfmapi job."""
    (
        job_id,
        tasks,
        target_format,
        resolved_provider,
    ) = await artifact_conversion_service.submit_conversion(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        request=body,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=[task.task_id for task in tasks],
            artifact_id=artifact_id,
            target_format=target_format,
            provider=resolved_provider,
        )
    )


@router.post("/{artifact_id}:validate", response_model=ArtifactValidationOut)
async def validate_artifact(
    artifact_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ArtifactValidationOut:
    """Validate an artifact descriptor and any local server-managed bytes."""
    return await artifact_conversion_service.validate_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )


@router.post(":import", response_model=StageArtifactOut, status_code=201)
async def import_artifact(
    body: ArtifactImportRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> StageArtifactOut:
    """Register an existing artifact URI for validation and downstream reuse."""
    artifact = await artifact_conversion_service.import_artifact(
        session,
        tenant_id=tenant_id,
        request=body,
    )
    return artifact_out(artifact)


@router.get("/{artifact_id}", response_model=StageArtifactOut)
async def get_artifact(
    artifact_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> StageArtifactOut:
    """Read one typed stage artifact by id."""
    artifact = await artifact_service.get_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )
    return artifact_out(artifact)


@router.get(
    "/{artifact_id}/content",
    response_class=FileResponse,
    responses=_BINARY_RESPONSE,
)
async def read_artifact_content(
    artifact_id: str,
    request: Request,
    download: bool = Query(default=False, description="Force Content-Disposition: attachment"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve content for top-level local server-managed regular-file artifacts.

    Remote object-store URIs, absent top-level URIs, files[]-only local
    paths, local directories, and paths outside sfmapi's managed roots
    are intentionally not dereferenced through this route.
    """
    artifact = await artifact_service.get_artifact(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
    )
    target = _resolve_managed_artifact_file(artifact.uri)
    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    media_type = artifact.media_type or "application/octet-stream"
    headers = {"ETag": etag, "Cache-Control": "private, max-age=60"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name if download else None,
        headers=headers,
    )


def _resolve_managed_artifact_file(uri: str | None) -> Path:
    if not uri:
        raise NotFoundError("artifact has no local content URI")
    candidate = _local_artifact_path(uri)
    if candidate is None:
        raise ValidationError("artifact content URI is remote and cannot be served directly")
    if candidate.is_dir():
        raise ValidationError("artifact content URI points at a directory")
    target = candidate.resolve(strict=False)
    settings = get_settings()
    allowed_roots = [
        settings.workspace_root.resolve(strict=False),
        settings.blob_root.resolve(strict=False),
        settings.s3_cache_root.resolve(strict=False),
    ]
    if not any(target == root or root in target.parents for root in allowed_roots):
        raise ValidationError("artifact content is outside sfmapi-managed storage")
    if not target.is_file():
        raise NotFoundError("artifact content file not found")
    return target
