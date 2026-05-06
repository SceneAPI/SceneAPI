"""Quality metric extraction from a finished sfmapi reconstruction.

Reads the latest sealed snapshot, parses `summary.json` (written by
the snapshot writer at seal time) and folds in counts from the worker
task's `outputs_ref`. We deliberately avoid re-parsing `points.bin`
here; counts and headline error are already in the JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sfmapi_client import SfmApiClient
from sfmapi_client.models import JobDetail


@dataclass(frozen=True)
class ReconstructionMetrics:
    num_reg_images: int
    num_points3D: int
    mean_reproj_err: float | None
    num_submodels: int
    extras: dict[str, Any]


def metrics_from_snapshot_summary(summary: dict) -> ReconstructionMetrics:
    """Parse the `summary.json` shape produced by `app.workers.tasks.map`."""
    models = summary.get("models") or []
    n_imgs = sum(int(m.get("num_reg_images", 0) or 0) for m in models)
    n_pts = sum(int(m.get("num_points3D", 0) or 0) for m in models)
    err = summary.get("mean_reproj_err")
    return ReconstructionMetrics(
        num_reg_images=n_imgs,
        num_points3D=n_pts,
        mean_reproj_err=float(err) if isinstance(err, (int, float)) else None,
        num_submodels=len(models),
        extras={k: v for k, v in summary.items() if k not in ("models", "mean_reproj_err")},
    )


def collect_metrics(client: SfmApiClient, *, recon_id: str) -> ReconstructionMetrics:
    """Fetch the latest sealed snapshot's summary and convert to metrics."""
    seqs = client.list_snapshots(recon_id)
    if not seqs:
        return ReconstructionMetrics(0, 0, None, 0, {"error": "no sealed snapshots"})
    seq = seqs[-1]
    raw = client.read_snapshot_file(recon_id, seq, "summary.json")
    summary = json.loads(raw.decode("utf-8"))
    return metrics_from_snapshot_summary(summary)


def metrics_from_job_outputs(detail: JobDetail) -> dict[str, float]:
    """Best-effort fallback: read counts from the map task's
    `outputs_ref` when no snapshot is available."""
    out: dict[str, float] = {}
    for t in detail.tasks:
        if t.kind != "map" or not t.outputs_ref:
            continue
        models = t.outputs_ref.get("models") or []
        out["num_reg_images"] = float(sum(int(m.get("num_reg_images", 0) or 0) for m in models))
        out["num_points3D"] = float(sum(int(m.get("num_points3D", 0) or 0) for m in models))
        out["num_submodels"] = float(len(models))
    return out
