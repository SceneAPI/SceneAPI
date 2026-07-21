"""Drive a running sfmapi server through one benchmark dataset.

Usage from `bench.cli`; not normally invoked directly.

The harness creates an ephemeral project per run so historical
projects don't accumulate. It does NOT delete the project on success
(operators want to inspect the reconstruction); add `--cleanup` in
the CLI if you need that.

Speaks the supported generated SDK (``scenesdk``); the
hand-rolled ``sfmapi_client`` package was removed at 0.1.0 as
scheduled.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from scenesdk._ergonomics import TERMINAL_JOB_STATES
from scenesdk.api.datasets import (
    create_v1_projects_project_id_datasets_post as _create_dataset,
)
from scenesdk.api.health import version_version_get as _get_version
from scenesdk.api.images import (
    batch_create_v_1_datasets_dataset_id_images_batch_create_post as _batch_create_images,
)
from scenesdk.api.jobs import get_v1_jobs_job_id_get as _get_job
from scenesdk.api.pipelines import (
    run_recipe_v1_projects_project_id_pipelines_recipe_post as _run_recipe,
)
from scenesdk.api.projects import create_v1_projects_post as _create_project
from scenesdk.models import (
    BatchCreateImagesRequest,
    DatasetCreate,
    FeaturesSpec,
    GlobalSpec,
    HierarchicalSpec,
    ImageCreate,
    IncrementalSpec,
    MatcherSpec,
    PairsSpec,
    PipelineRequest,
    ProjectCreate,
    SphericalSpec,
    VerifySpec,
    VersionResponse,
)
from scenesdk.models.run_recipe_v1_projects_project_id_pipelines_recipe_post_recipe import (
    RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe as _RecipeSlug,
)
from scenesdk.types import UNSET, Unset

from bench import metrics as bench_metrics
from bench import store
from bench._sdk import ApiClient, call

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


_BATCH_CREATE_LIMIT = 1000  # server-side cap per images:batchCreate call


def _register_local_images(client: ApiClient, dataset_id: str, image_list: list[str]) -> None:
    """Bulk-register the collected local images against the dataset.

    The wire requires images to be registered explicitly before a
    pipeline can run (the legacy submit-time ``image_root`` /
    ``image_list`` parameters are gone); ``rel_path`` marks each row
    as a ``local``-kind image resolved under the dataset source root.
    """
    for start in range(0, len(image_list), _BATCH_CREATE_LIMIT):
        chunk = image_list[start : start + _BATCH_CREATE_LIMIT]
        call(
            _batch_create_images.sync,
            dataset_id,
            client=client,
            body=BatchCreateImagesRequest(
                requests=[ImageCreate(name=name, rel_path=name) for name in chunk]
            ),
        )


def _runtime_version_id(rv: VersionResponse) -> str:
    """Opaque engine-runtime identifier stamped on each bench row.

    The wire's ``VersionResponse`` carries the backend identity plus a
    backend-defined ``runtime_versions`` map (engine shas, CUDA arch,
    ... — whatever the backend salts into its cache keys); flatten it
    deterministically. Headless servers (no backend registered) fall
    back to the sfmapi version itself.
    """
    backend = rv.backend
    if backend is None or isinstance(backend, Unset):
        return f"sfmapi:{rv.sfmapi}"
    parts: list[str] = [str(backend.name), str(backend.version)]
    versions = backend.runtime_versions
    if not isinstance(versions, Unset):
        parts.extend(f"{k}={v}" for k, v in sorted(versions.additional_properties.items()))
    return ":".join(parts)


def run_one(
    *,
    client: ApiClient,
    dataset: DatasetSpec,
    project_prefix: str = "bench",
    poll_interval: float = 2.0,
    timeout_seconds: float = 60 * 60,
) -> store.BenchResult:
    started_at = store.now_iso()
    t0 = time.time()
    sha = _resolve_git_sha()

    rv = call(_get_version.sync, client=client)
    runtime_version_id = _runtime_version_id(rv)

    proj = call(
        _create_project.sync,
        client=client,
        body=ProjectCreate(name=f"{project_prefix}-{dataset.name}-{int(time.time())}"),
    )

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

    ds = call(
        _create_dataset.sync,
        proj.project_id,
        client=client,
        body=DatasetCreate.from_dict(
            {
                "name": dataset.name,
                "source": {"kind": "local", "root": str(image_root)},
                "camera_model": dataset.camera_model,
            }
        ),
    )

    _register_local_images(client, ds.dataset_id, image_list)

    spec_cls = _SPEC_BY_KIND[dataset.recipe]
    spec_payload = {**dataset.spec, "kind": dataset.recipe}
    spec = spec_cls.from_dict(spec_payload)

    request = PipelineRequest(
        dataset_id=ds.dataset_id,
        spec=spec,
        features=FeaturesSpec.from_dict(dataset.features) if dataset.features else UNSET,
        pairs=PairsSpec.from_dict(dataset.pairs) if dataset.pairs else UNSET,
        matcher=MatcherSpec.from_dict(dataset.matcher) if dataset.matcher else UNSET,
        verify=VerifySpec.from_dict(dataset.verify) if dataset.verify else UNSET,
    )
    job = call(
        _run_recipe.sync,
        proj.project_id,
        _RecipeSlug(dataset.recipe),
        client=client,
        body=request,
    )

    deadline = time.time() + timeout_seconds
    detail = call(_get_job.sync, job.job_id, client=client)
    while str(detail.status) not in TERMINAL_JOB_STATES:
        if time.time() > deadline:
            raise TimeoutError(f"bench {dataset.name} timed out after {timeout_seconds:.0f}s")
        time.sleep(poll_interval)
        detail = call(_get_job.sync, job.job_id, client=client)

    wall = time.time() - t0
    metrics: dict[str, float] = {"wall_seconds": wall}

    recon_id = job.recon_id if isinstance(job.recon_id, str) else ""
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
        except Exception:
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
        notes["error_class"] = str(detail.error_class)
        notes["error_message"] = (
            detail.error_message if isinstance(detail.error_message, str) else None
        )

    return store.BenchResult(
        dataset=dataset.name,
        recipe=dataset.recipe,
        git_sha=sha,
        runtime_version_id=runtime_version_id,
        started_at=started_at,
        finished_at=store.now_iso(),
        wall_seconds=wall,
        status=str(detail.status),
        metrics=metrics,
        notes=notes,
    )
