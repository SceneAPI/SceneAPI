"""Workspace GC + storage accounting.

GC policy (Phase 5). Old completed jobs (status in
``succeeded``, ``failed``, ``cancelled``, ``cancelled_dirty``) past
``ttl_days`` are swept in this order:

1. Drop the job's ``dense/`` directory first.
2. Drop the job's ``snapshots/`` directory next (job-scoped output).
3. Drop the job's ``sparse/`` directory last.
4. Drop the job's ``log.jsonl`` / ``events.jsonl`` only if
   ``drop_db_rows`` is set.

Reconstruction-level artifacts are NOT touched by job GC because
reconstructions can outlive the job that produced them and may be
referenced by other (still active) jobs. A separate ``gc_orphan_*``
job sweeps reconstructions whose ``dataset_snapshot_hash`` no longer
matches any current dataset.

Pinned jobs (``Job.pinned=True``) are skipped at every step.

Caller passes a ``now`` for testability; otherwise ``datetime.now(UTC)``.
"""

from __future__ import annotations

import contextlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.config import get_settings
from sceneapi.server.core.paths import Paths
from sceneapi.server.db.models import Job


def _now() -> datetime:
    return datetime.now(UTC)


def workspace_total_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


async def gc_completed_jobs(
    session: AsyncSession,
    *,
    ttl_days: int,
    now: datetime | None = None,
    drop_db_rows: bool = False,
) -> dict:
    """Sweep completed-and-old jobs. Returns a summary dict for tests."""
    settings = get_settings()
    paths = Paths(settings)
    cutoff = (now or _now()) - timedelta(days=ttl_days)
    rows = (
        (
            await session.execute(
                select(Job).where(
                    Job.status.in_(("succeeded", "failed", "cancelled", "cancelled_dirty")),
                    Job.finished_at.is_not(None),
                    Job.finished_at < cutoff,
                    Job.pinned.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    summary = {
        "considered": len(rows),
        "dense_dropped": 0,
        "snapshots_dropped": 0,
        "sparse_dropped": 0,
        "rows_deleted": 0,
    }

    for j in rows:
        job_dir = paths.job_root(j.tenant_id, j.project_id, j.job_id)
        for sub, key in (
            ("dense", "dense_dropped"),
            ("snapshots", "snapshots_dropped"),
            ("sparse", "sparse_dropped"),
        ):
            d = job_dir / sub
            if d.exists():
                with contextlib.suppress(OSError):
                    shutil.rmtree(d)
                    summary[key] += 1
        if drop_db_rows:
            await session.delete(j)
            summary["rows_deleted"] += 1

    await session.flush()
    return summary
