"""Drive a running sfmapi server through one benchmark dataset.

Usage from `bench.cli`; not normally invoked directly.

The harness creates an ephemeral project per run so historical
projects don't accumulate. It does NOT delete the project on success
(operators want to inspect the reconstruction); add `--cleanup` in
the CLI if you need that.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sfmapi_client import SfmApiClient
from sfmapi_client.models import (
    FeaturesSpec,
    GlobalSpec,
    HierarchicalSpec,
    IncrementalSpec,
    MatcherSpec,
    PairsSpec,
    SphericalSpec,
    VerifySpec,
)

from bench import metrics as bench_metrics
from bench import store

_SPEC_BY_KIND = {
    "incremental": IncrementalSpec,
    "global": GlobalSpec,
    "hierarchical": HierarchicalSpec,
    "spherical": SphericalSpec,
}


@dataclass
class DatasetSpec:
    name: str
    description: str
    recipe: str
    spec: dict[str, Any]
    features: dict[str, Any]
    pairs: dict[str, Any]
    matcher: dict[str, Any]
    verify: dict[str, Any]
    source: dict[str, Any]
    camera_model: str
    expected: dict[str, Any]


def load_dataset(path: Path) -> DatasetSpec:
    raw = path.read_text(encoding="utf-8")
    body = yaml.safe_load(_expand_env(raw))
    # ``matches`` was the legacy combined shape; if a YAML still uses
    # it, split it across ``pairs`` and ``matcher`` for the new wire.
    legacy = body.get("matches") or {}
    pairs = body.get("pairs") or {}
    matcher = body.get("matcher") or {}
    if legacy:
        pairs.setdefault("strategy", legacy.get("mode", "exhaustive"))
        if "overlap" in legacy:
            pairs.setdefault("overlap", legacy["overlap"])
        if "vocab_tree_path" in legacy:
            pairs.setdefault("vocab_tree_path", legacy["vocab_tree_path"])
        for key in ("use_gpu", "max_ratio", "max_distance", "cross_check"):
            if key in legacy:
                matcher.setdefault(key, legacy[key])
    return DatasetSpec(
        name=body["name"],
        description=body.get("description", ""),
        recipe=body["recipe"],
        spec=body["spec"],
        features=body.get("features", {}),
        pairs=pairs,
        matcher=matcher,
        verify=body.get("verify", {}),
        source=body["source"],
        camera_model=body.get("camera_model", "SIMPLE_RADIAL"),
        expected=body.get("expected", {}),
    )


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand_env(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2) or ""
        return os.environ.get(var, default)

    return _ENV_PATTERN.sub(repl, s)


def _collect_image_list(image_root: Path, glob: str) -> list[str]:
    return sorted(p.name for p in image_root.glob(glob) if p.is_file())


def _resolve_git_sha() -> str:
    import subprocess

    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "local"


def run_one(
    *,
    client: SfmApiClient,
    dataset: DatasetSpec,
    project_prefix: str = "bench",
    poll_interval: float = 2.0,
    timeout_seconds: float = 60 * 60,
) -> store.BenchResult:
    started_at = store.now_iso()
    t0 = time.time()
    sha = _resolve_git_sha()

    rv = client.version()
    runtime_version_id = f"{rv.colmap_sha}:{rv.baxx_sha}:{rv.cudss_ver}:{rv.cuda_arch}"

    proj = client.create_project(f"{project_prefix}-{dataset.name}-{int(time.time())}")

    if dataset.source["kind"] != "local":
        raise NotImplementedError(
            f"bench harness currently only supports source.kind=local "
            f"(got {dataset.source['kind']!r})"
        )
    image_root = Path(dataset.source["image_root"])
    if not image_root.is_dir():
        raise FileNotFoundError(f"image_root not found: {image_root}. Set $BENCH_DATA_ROOT.")
    image_list = _collect_image_list(image_root, dataset.source.get("image_glob", "*"))
    if not image_list:
        raise RuntimeError(f"no images matched in {image_root}")

    ds = client.create_dataset(
        proj.project_id,
        name=dataset.name,
        source={"kind": "local", "root": str(image_root)},
        camera_model=dataset.camera_model,
    )

    spec_cls = _SPEC_BY_KIND[dataset.recipe]
    spec_payload = {**dataset.spec, "kind": dataset.recipe}
    spec = spec_cls.model_validate(spec_payload)

    job = client.run_pipeline(
        proj.project_id,
        dataset_id=ds.dataset_id,
        image_root=str(image_root),
        image_list=image_list,
        spec=spec,
        features=FeaturesSpec.model_validate(dataset.features) if dataset.features else None,
        pairs=PairsSpec.model_validate(dataset.pairs) if dataset.pairs else None,
        matcher=MatcherSpec.model_validate(dataset.matcher) if dataset.matcher else None,
        verify=VerifySpec.model_validate(dataset.verify) if dataset.verify else None,
    )

    deadline = time.time() + timeout_seconds
    detail = client.get_job(job.job_id)
    while detail.status not in ("succeeded", "failed", "cancelled", "cancelled_dirty"):
        if time.time() > deadline:
            raise TimeoutError(f"bench {dataset.name} timed out after {timeout_seconds:.0f}s")
        time.sleep(poll_interval)
        detail = client.get_job(job.job_id)

    wall = time.time() - t0
    metrics: dict[str, float] = {"wall_seconds": wall}

    recon_id = job.recon_id or ""
    if recon_id:
        try:
            m = bench_metrics.collect_metrics(client, recon_id=recon_id)
            metrics.update(
                {
                    "num_reg_images": float(m.num_reg_images),
                    "num_points3D": float(m.num_points3D),
                    "num_submodels": float(m.num_submodels),
                }
            )
            if m.mean_reproj_err is not None:
                metrics["mean_reproj_err"] = float(m.mean_reproj_err)
        except Exception:  # noqa: BLE001 — fall back to job outputs
            pass
    metrics.update(bench_metrics.metrics_from_job_outputs(detail))

    notes = {
        "project_id": proj.project_id,
        "dataset_id": ds.dataset_id,
        "recon_id": recon_id,
        "image_count": len(image_list),
        "expected": dataset.expected,
    }
    if detail.error_class:
        notes["error_class"] = detail.error_class
        notes["error_message"] = detail.error_message

    return store.BenchResult(
        dataset=dataset.name,
        recipe=dataset.recipe,
        git_sha=sha,
        runtime_version_id=runtime_version_id,
        started_at=started_at,
        finished_at=store.now_iso(),
        wall_seconds=wall,
        status=detail.status,
        metrics=metrics,
        notes=notes,
    )
