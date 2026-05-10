"""Stage artifact resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import artifacts as artifact_vocab
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.http import file_etag, if_none_match_hit, not_modified
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.artifacts import ArtifactKindOut, StageArtifactOut
from app.schemas.api.common import Link, Page, to_out
from app.services import artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def artifact_links(artifact: Any) -> dict[str, Link]:
    artifact_id = artifact.artifact_id
    links = {
        "self": Link(href=f"/v1/artifacts/{artifact_id}"),
        "content": Link(href=f"/v1/artifacts/{artifact_id}/content"),
        "job": Link(href=f"/v1/jobs/{artifact.job_id}"),
    }
    recon_id = getattr(artifact, "recon_id", None)
    if recon_id is not None:
        links["reconstruction"] = Link(href=f"/v1/reconstructions/{recon_id}")
    return links


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
    return to_out(StageArtifactOut, artifact, links=artifact_links(artifact))


@router.get("/{artifact_id}/content")
async def read_artifact_content(
    artifact_id: str,
    request: Request,
    download: bool = Query(default=False, description="Force Content-Disposition: attachment"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve content for local, server-managed file artifacts.

    Remote object-store URIs and paths outside sfmapi's managed roots
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
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        candidate = Path(raw_path)
    elif "://" in uri:
        raise ValidationError("artifact content URI is remote and cannot be served directly")
    else:
        candidate = Path(uri)
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
