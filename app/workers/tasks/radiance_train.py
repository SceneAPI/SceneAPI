"""Deterministic alpha radiance training task.

Real 3DGS engines live in backend plugins. The core task provides a tiny
`stub` provider so the resource/job/snapshot contract can be tested without
CUDA or external checkouts.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.core.errors import CapabilityUnavailableError, ValidationError
from app.core.paths import Paths
from app.db.models import Task
from app.storage.snapshots import SnapshotStore
from app.workers._task_io import read_state
from app.workers.tasks._registry import task_handler
from sfm_hub.registry import get_manifest
from sfm_hub.routing import ProviderRecord, provider_records


@task_handler("radiance_train")
def run(task: Task) -> dict:
    inputs, spec = read_state(task)
    provider = str(spec.get("provider") or "stub")
    if provider != "stub":
        return _run_container_service_provider(task, provider=provider, inputs=inputs, spec=spec)
    radiance_field_id = inputs.get("radiance_field_id")
    project_id = inputs.get("project_id")
    if not isinstance(radiance_field_id, str) or not radiance_field_id:
        raise ValidationError("radiance_train missing radiance_field_id")
    if not isinstance(project_id, str) or not project_id:
        raise ValidationError("radiance_train missing project_id")

    root = Paths().radiance_field_root(task.tenant_id, project_id, radiance_field_id)
    live = root / "_live" / task.task_id
    live.mkdir(parents=True, exist_ok=True)
    ply = live / "point_cloud.ply"
    ply.write_text(
        "\n".join(
            [
                "ply",
                "format ascii 1.0",
                "element vertex 1",
                "property float x",
                "property float y",
                "property float z",
                "property uchar red",
                "property uchar green",
                "property uchar blue",
                "end_header",
                "0 0 0 255 255 255",
                "",
            ]
        ),
        encoding="utf-8",
    )
    summary = {
        "provider": "stub",
        "method": str(spec.get("method") or "stub"),
        "radiance_field_id": radiance_field_id,
        "vertex_count": 1,
        "format": "ply",
        "duration_s": 0.0,
    }
    (live / "metadata.json").write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    evaluations: list[dict[str, Any]] = []
    eval_config = spec.get("eval") if isinstance(spec.get("eval"), dict) else None
    evaluation_id = inputs.get("evaluation_id")
    if (
        isinstance(eval_config, dict)
        and eval_config.get("enabled") is True
        and isinstance(evaluation_id, str)
        and evaluation_id
    ):
        metrics = _stub_metrics()
        eval_artifacts = [
            {
                "kind": "radiance.evaluation.metrics",
                "name": "metrics.json",
                "media_type": "application/json",
                "artifact_format": "sfmapi.radiance.metrics.v1",
                "summary": metrics,
            }
        ]
        (live / "metrics.json").write_text(
            json.dumps(metrics, sort_keys=True),
            encoding="utf-8",
        )
        evaluations.append(
            {
                "evaluation_id": evaluation_id,
                "radiance_field_id": radiance_field_id,
                "snapshot_seq": 1,
                "metrics": metrics,
                "artifacts": eval_artifacts,
            }
        )
    sealed = SnapshotStore(root).seal(seq=1, source_dir=live, summary=summary)
    variant_uri = str(sealed / "point_cloud.ply")
    return {
        "radiance_field_id": radiance_field_id,
        "snapshot_seq": 1,
        "snapshot_path": str(sealed),
        "summary": summary,
        "evaluations": evaluations,
        "artifacts": [
            {
                "kind": "radiance.snapshot",
                "name": "snapshot-1",
                "uri": str(sealed),
                "artifact_format": "sfmapi.radiance.snapshot.v1",
                "metadata": {"radiance_field_id": radiance_field_id, "snapshot_seq": 1},
                "summary": summary,
            },
            {
                "kind": "radiance.variant.ply",
                "name": "point_cloud.ply",
                "uri": variant_uri,
                "media_type": "application/octet-stream",
                "artifact_format": "sfmapi.radiance.variant.ply.v1",
                "metadata": {"radiance_field_id": radiance_field_id, "snapshot_seq": 1},
                "summary": {"vertex_count": 1},
            },
        ],
        "variants": [
            {
                "format": "ply",
                "uri": variant_uri,
                "media_type": "application/octet-stream",
                "summary": {"vertex_count": 1},
            }
        ],
    }


def _stub_metrics() -> dict[str, Any]:
    return {
        "psnr_db": 30.0,
        "ssim": 1.0,
        "lpips": 0.0,
        "num_images": 1,
        "duration_s": 0.0,
        "render_time_s_total": 0.0,
        "render_time_s_mean": 0.0,
    }


def _radiance_provider_record(provider: str, capability: str = "radiance.train") -> ProviderRecord:
    all_rows = provider_records(installed_only=True, enabled_only=True)
    same_provider = [row for row in all_rows if row.provider.provider_id == provider]
    candidates = [
        row for row in same_provider if capability in row.provider.capabilities
    ]
    if not candidates:
        if same_provider:
            raise CapabilityUnavailableError(
                capability=capability,
                reason=f"provider {provider!r} does not advertise {capability}",
            )
        raise CapabilityUnavailableError(
            capability=capability,
            reason=f"provider {provider!r} is not installed and enabled as a radiance plugin",
        )
    if len(candidates) > 1:
        plugin_ids = ", ".join(sorted({row.plugin_id for row in candidates}))
        raise ValidationError(
            f"provider {provider!r} is ambiguous across installed plugins: {plugin_ids}"
        )
    return candidates[0]


def _service_base_url(row: ProviderRecord, capability: str = "radiance.train") -> str:
    manifest = get_manifest(row.plugin_id)
    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        raise CapabilityUnavailableError(
            capability=capability,
            reason=(
                f"provider {row.provider.provider_id!r} is installed but does not define "
                "a container_service runtime"
            ),
        )

    raw = os.environ.get(runtime.service.url_env) if runtime.service.url_env else None
    raw = raw or runtime.service.default_url
    if not raw:
        hint = f"; set {runtime.service.url_env}" if runtime.service.url_env else ""
        raise CapabilityUnavailableError(
            capability=capability,
            reason=(
                f"provider {row.provider.provider_id!r} container_service endpoint "
                f"is not configured{hint}"
            ),
        )
    parsed = urlsplit(raw)
    if parsed.scheme != "http" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValidationError(
            f"provider {row.provider.provider_id!r} container_service endpoint must be "
            "an http:// URL without query or fragment"
        )
    return raw.rstrip("/")


def _runtime_execution_path(row: ProviderRecord, capability: str = "radiance.train") -> tuple[str, int]:
    manifest = get_manifest(row.plugin_id)
    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        raise CapabilityUnavailableError(
            capability=capability,
            reason=(
                f"provider {row.provider.provider_id!r} is installed but does not define "
                "a container_service runtime"
            ),
        )
    return runtime.execution.path, runtime.execution.timeout_seconds


def _run_container_service_provider(
    task: Task,
    *,
    provider: str,
    inputs: dict[str, Any],
    spec: dict[str, Any],
    task_kind: str = "radiance_train",
    capability: str = "radiance.train",
) -> dict[str, Any]:
    radiance_field_id = inputs.get("radiance_field_id")
    project_id = inputs.get("project_id")
    if not isinstance(radiance_field_id, str) or not radiance_field_id:
        raise ValidationError(f"{task_kind} missing radiance_field_id")
    if not isinstance(project_id, str) or not project_id:
        raise ValidationError(f"{task_kind} missing project_id")

    row = _radiance_provider_record(provider, capability)
    path, timeout_seconds = _runtime_execution_path(row, capability)
    url = f"{_service_base_url(row, capability)}{path}"
    payload = {
        "protocol": "sfmapi-plugin-http-v1",
        "task_kind": task_kind,
        "capability": capability,
        "tenant_id": task.tenant_id,
        "job_id": task.job_id,
        "task_id": task.task_id,
        "provider": provider,
        "inputs": inputs,
        "spec": spec,
    }
    request = Request(
        url,
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(
            f"radiance provider {provider!r} returned HTTP {exc.code}: {body}"
        ) from exc
    except URLError as exc:
        raise CapabilityUnavailableError(
            capability=capability,
            reason=f"radiance provider {provider!r} endpoint {url!r} is unavailable: {exc}",
        ) from exc

    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"radiance provider {provider!r} returned invalid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise ValidationError(f"radiance provider {provider!r} must return a JSON object")
    if body.get("status") in {"failed", "error"}:
        detail = body.get("error") or body.get("detail") or body
        raise RuntimeError(f"radiance provider {provider!r} failed: {detail}")

    outputs = body.get("outputs") if isinstance(body.get("outputs"), dict) else body
    if not isinstance(outputs, dict):
        raise ValidationError(
            f"radiance provider {provider!r} outputs must be a JSON object"
        )
    if task_kind == "radiance_eval" and (
        not isinstance(outputs.get("evaluation_id"), str)
        or not isinstance(outputs.get("metrics"), dict)
    ):
        raise ValidationError(
            f"radiance provider {provider!r} eval output must include "
            "evaluation_id and metrics"
        )
    eval_config = spec.get("eval") if isinstance(spec.get("eval"), dict) else None
    evaluation_id = inputs.get("evaluation_id")
    if (
        task_kind == "radiance_train"
        and isinstance(eval_config, dict)
        and eval_config.get("enabled") is True
        and isinstance(evaluation_id, str)
    ):
        evaluations = outputs.get("evaluations")
        if not isinstance(evaluations, list) or not any(
            isinstance(item, dict)
            and item.get("evaluation_id") == evaluation_id
            and isinstance(item.get("metrics"), dict)
            for item in evaluations
        ):
            raise ValidationError(
                f"radiance provider {provider!r} train output must include "
                f"evaluation metrics for evaluation_id={evaluation_id}"
            )
    return outputs
