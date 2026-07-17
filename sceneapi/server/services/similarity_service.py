"""Build + query similarity indexes for a dataset.

Lazy build for `dhash`: on first query, hash every registered image
and persist the index. The on-disk index is invalidated whenever the
dataset's `manifest_hash` changes (image add/remove); subsequent
queries detect the staleness and rebuild.

`vlad` queries load a pre-built `vlad.npz` (NumPy-only, no pycolmap
needed in the web process). Building the index is a worker job — see
``sceneapi/server/workers/tasks/vlad_index.py`` — that requires SIFT features in
the reconstruction database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.errors import NotFoundError, PycolmapUnavailableError, ValidationError
from sceneapi.server.core.paths import Paths
from sceneapi.server.db.models import Dataset, Image
from sceneapi.server.services import dataset_service, image_bytes_service
from sceneapi.server.storage import similarity as sim
from sceneapi.server.storage import vlad as vlad_store

Strategy = Literal["dhash", "vlad"]
SUPPORTED_STRATEGIES = ("dhash", "vlad")


def _dataset_dir(tenant_id: str, dataset: Dataset) -> Path:
    paths = Paths()
    return paths.dataset_root(tenant_id, dataset.project_id, dataset.dataset_id)


async def build_index(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    strategy: Strategy = "dhash",
    force: bool = False,
) -> sim.SimilarityIndex:
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValidationError(f"unknown strategy: {strategy!r}")
    if strategy == "vlad":
        # vlad indexes are built by a worker (`POST :build?strategy=vlad`)
        # because they need pycolmap + the feature DB. Synchronous
        # build here would force a worker handoff anyway.
        raise PycolmapUnavailableError(
            "vlad index must be built via a worker job; "
            "POST /v1/datasets/{dataset_id}/similarity:build?strategy=vlad"
        )
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    ds_dir = _dataset_dir(tenant_id, d)
    existing = sim.read_index(ds_dir, strategy)
    if existing is not None and not force and existing.manifest_hash == (d.manifest_hash or ""):
        return existing

    images = (
        (
            await session.execute(
                select(Image)
                .where(Image.tenant_id == tenant_id, Image.dataset_id == d.dataset_id)
                .order_by(Image.image_id)
            )
        )
        .scalars()
        .all()
    )
    if not images:
        raise ValidationError("dataset has no images registered")

    hashes: dict[str, str] = {}
    for img in images:
        path = await image_bytes_service.resolve_image_path(session, tenant_id=tenant_id, image=img)
        if not path.is_file():
            # Skip silently — clients can rebuild after the materialization
            # pipeline catches up.
            continue
        with path.open("rb") as fh:
            hashes[img.image_id] = sim.dhash_hex(fh)

    index = sim.SimilarityIndex(
        strategy=strategy,
        manifest_hash=d.manifest_hash or "",
        hashes=hashes,
    )
    sim.write_index(ds_dir, index)
    return index


async def query_neighbors(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    image_id: str,
    k: int = 5,
    strategy: Strategy = "dhash",
    include_self: bool = False,
) -> list[sim.SimilarityNeighbor] | list[vlad_store.VladNeighbor]:
    if k < 1 or k > 1000:
        raise ValidationError("k must be in [1, 1000]")
    if strategy == "vlad":
        d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
        ds_dir = _dataset_dir(tenant_id, d)
        index = vlad_store.read_index(ds_dir)
        if index is None:
            raise NotFoundError(
                "vlad index not built for this dataset; "
                f"POST /v1/datasets/{dataset_id}/similarity:build?strategy=vlad first"
            )
        try:
            return vlad_store.k_nearest(index, image_id=image_id, k=k, include_self=include_self)
        except KeyError as e:
            raise NotFoundError(f"image {image_id} not in vlad index") from e
    index = await build_index(
        session, tenant_id=tenant_id, dataset_id=dataset_id, strategy=strategy
    )
    try:
        return sim.k_nearest(index, image_id=image_id, k=k, include_self=include_self)
    except KeyError as e:
        raise NotFoundError(f"image {image_id} not in similarity index") from e
