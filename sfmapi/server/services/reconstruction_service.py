"""Reconstruction + SubModel CRUD + Snapshot reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.errors import NotFoundError
from sfmapi.server.core.paths import Paths
from sfmapi.server.db.models import Reconstruction, SubModel
from sfmapi.server.db.pagination import paginate_keyset
from sfmapi.server.storage.snapshots import SnapshotStore


async def get_reconstruction(
    session: AsyncSession, *, tenant_id: str, recon_id: str
) -> Reconstruction:
    result = await session.execute(
        select(Reconstruction).where(
            Reconstruction.tenant_id == tenant_id, Reconstruction.recon_id == recon_id
        )
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise NotFoundError(f"Reconstruction {recon_id} not found")
    return r


async def list_submodels(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    page_size: int = 100,
    page_token: str | None = None,
) -> tuple[list[SubModel], str | None]:
    """AIP-158 keyset pagination on ``submodel_id`` ascending; ``idx``
    determines display order but ``submodel_id`` is the cursor key (stable
    even when components are added / removed mid-iteration)."""
    stmt = select(SubModel).where(SubModel.tenant_id == tenant_id, SubModel.recon_id == recon_id)
    rows, next_page_token = await paginate_keyset(
        session,
        stmt,
        pk=SubModel.submodel_id,
        page_size=page_size,
        page_token=page_token,
    )
    # Surface in idx order (UI/SDK expectation) regardless of cursor key.
    rows.sort(key=lambda r: r.idx)
    return rows, next_page_token


async def get_submodel(session: AsyncSession, *, tenant_id: str, submodel_id: str) -> SubModel:
    result = await session.execute(
        select(SubModel).where(SubModel.tenant_id == tenant_id, SubModel.submodel_id == submodel_id)
    )
    sm = result.scalar_one_or_none()
    if sm is None:
        raise NotFoundError(f"SubModel {submodel_id} not found")
    return sm


def _coerce_model_idx(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _submodel_sealed_path(*, snapshot_path: str | None, idx: int, model_count: int) -> str | None:
    if not snapshot_path:
        return None
    base = Path(snapshot_path)
    return str(base / str(idx)) if model_count > 1 else str(base)


async def mark_reconstruction_status(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    status: str,
) -> None:
    """Update a reconstruction lifecycle state if the row still exists."""
    result = await session.execute(
        select(Reconstruction).where(
            Reconstruction.tenant_id == tenant_id, Reconstruction.recon_id == recon_id
        )
    )
    r = result.scalar_one_or_none()
    if r is not None:
        r.status = status


async def record_mapping_result(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    models: list[dict[str, Any]],
    snapshot_seq: int | None,
    snapshot_path: str | None,
) -> None:
    """Replace SubModel rows with the components emitted by a map task.

    Backends return one summary per disconnected mapping component. The
    snapshot writer seals those component files under the same sequence;
    this function makes the resource layer reflect that output.
    """
    r = await get_reconstruction(session, tenant_id=tenant_id, recon_id=recon_id)
    await session.execute(
        delete(SubModel).where(SubModel.tenant_id == tenant_id, SubModel.recon_id == recon_id)
    )
    model_count = len(models)
    for position, summary in enumerate(models):
        idx = _coerce_model_idx(summary.get("idx"), position)
        session.add(
            SubModel(
                tenant_id=tenant_id,
                recon_id=recon_id,
                idx=idx,
                summary_json=summary,
                snapshot_seq=snapshot_seq,
                sealed_path=_submodel_sealed_path(
                    snapshot_path=snapshot_path,
                    idx=idx,
                    model_count=model_count,
                ),
            )
        )
    r.status = "succeeded"
    await session.flush()


def list_snapshot_seqs(paths: Paths, tenant_id: str, project_id: str, recon_id: str) -> list[int]:
    root = paths.reconstruction_root(tenant_id, project_id, recon_id)
    if not root.exists():
        return []
    return SnapshotStore(root).list_sealed()


def snapshot_dir(paths: Paths, tenant_id: str, project_id: str, recon_id: str, seq: int) -> Path:
    return SnapshotStore(paths.reconstruction_root(tenant_id, project_id, recon_id)).path_for(seq)
