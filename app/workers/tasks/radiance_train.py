"""Deterministic alpha radiance training task.

Real 3DGS engines live in backend plugins. The core task provides a tiny
`stub` provider so the resource/job/snapshot contract can be tested without
CUDA or external checkouts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.errors import CapabilityUnavailableError, ValidationError
from app.core.paths import Paths
from app.db.models import Task
from app.services import artifact_service
from app.storage.snapshots import SnapshotStore
from app.workers._task_io import read_state
from app.workers.tasks._registry import task_handler
from sfm_hub.registry import get_manifest
from sfm_hub.models import _public_url_issue
from sfm_hub.routing import ProviderRecord, provider_records

_ARTIFACT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


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
    shutil.rmtree(live, ignore_errors=True)
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


def _valid_artifact_key(value: Any) -> bool:
    return isinstance(value, str) and bool(_ARTIFACT_KEY_RE.fullmatch(value))


def _validate_radiance_artifacts(
    artifacts: Any,
    *,
    provider: str,
    context: str,
) -> None:
    if artifacts is None:
        return
    if not isinstance(artifacts, list):
        raise ValidationError(
            f"radiance provider {provider!r} {context} artifacts must be a list"
        )
    for index, artifact in enumerate(artifacts):
        where = f"{context}.artifacts[{index}]"
        if not isinstance(artifact, dict):
            raise ValidationError(
                f"radiance provider {provider!r} {where} must be an object"
            )
        if not _valid_artifact_key(artifact.get("kind")):
            raise ValidationError(
                f"radiance provider {provider!r} {where}.kind is invalid"
            )
        try:
            artifact_service.validate_artifact_descriptor(
                artifact,
                index=index,
                context=f"{context}.artifacts",
            )
        except ValidationError as exc:
            raise ValidationError(
                f"radiance provider {provider!r} {exc.detail}"
            ) from exc
        for key in ("artifact_format", "datatype"):
            value = artifact.get(key)
            if value is not None and not _valid_artifact_key(value):
                raise ValidationError(
                    f"radiance provider {provider!r} {where}.{key} is invalid"
                )
        for key, max_len in (
            ("name", 255),
            ("uri", 2048),
            ("media_type", 127),
            ("sha256", 128),
        ):
            value = artifact.get(key)
            if value is not None and (
                not isinstance(value, str) or len(value) > max_len
            ):
                raise ValidationError(
                    f"radiance provider {provider!r} {where}.{key} must be "
                    f"a string of at most {max_len} characters"
                )
        schema_version = artifact.get("schema_version")
        if schema_version is not None and (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 1
        ):
            raise ValidationError(
                f"radiance provider {provider!r} {where}.schema_version "
                "must be a positive integer"
            )
        byte_size = artifact.get("byte_size")
        if byte_size is not None and (
            isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0
        ):
            raise ValidationError(
                f"radiance provider {provider!r} {where}.byte_size "
                "must be a non-negative integer"
            )
        for key in ("summary", "metadata", "producer"):
            value = artifact.get(key)
            if value is not None and not isinstance(value, dict):
                raise ValidationError(
                    f"radiance provider {provider!r} {where}.{key} "
                    "must be an object when present"
                )
        files = artifact.get("files")
        if files is None:
            continue
        if not isinstance(files, list):
            raise ValidationError(
                f"radiance provider {provider!r} {where}.files must be a list"
            )
        for file_index, file_item in enumerate(files):
            file_where = f"{where}.files[{file_index}]"
            if not isinstance(file_item, dict):
                raise ValidationError(
                    f"radiance provider {provider!r} {file_where} must be an object"
                )
            name = file_item.get("name")
            if not isinstance(name, str) or not name:
                raise ValidationError(
                    f"radiance provider {provider!r} {file_where}.name "
                    "must be a non-empty string"
                )
            uri = file_item.get("uri", file_item.get("path"))
            if not isinstance(uri, str) or not uri:
                raise ValidationError(
                    f"radiance provider {provider!r} {file_where}.uri "
                    "must be a non-empty string"
                )
            for key, max_len in (
                ("name", 255),
                ("uri", 2048),
                ("path", 2048),
                ("media_type", 127),
                ("sha256", 128),
            ):
                value = file_item.get(key)
                if value is not None and (
                    not isinstance(value, str) or len(value) > max_len
                ):
                    raise ValidationError(
                        f"radiance provider {provider!r} {file_where}.{key} "
                        f"must be a string of at most {max_len} characters"
                    )
            file_size = file_item.get("byte_size")
            if file_size is not None and (
                isinstance(file_size, bool)
                or not isinstance(file_size, int)
                or file_size < 0
            ):
                raise ValidationError(
                    f"radiance provider {provider!r} {file_where}.byte_size "
                    "must be a non-negative integer"
                )


def _validate_train_evaluation_artifacts(
    outputs: dict[str, Any],
    *,
    provider: str,
) -> None:
    evaluations = outputs.get("evaluations")
    if evaluations is None:
        return
    if not isinstance(evaluations, list):
        raise ValidationError(
            f"radiance provider {provider!r} outputs.evaluations must be a list"
        )
    for index, item in enumerate(evaluations):
        if not isinstance(item, dict) or "artifacts" not in item:
            continue
        _validate_radiance_artifacts(
            item.get("artifacts"),
            provider=provider,
            context=f"outputs.evaluations[{index}]",
        )


def _has_metric_evaluation(outputs: dict[str, Any]) -> bool:
    evaluations = outputs.get("evaluations")
    if not isinstance(evaluations, list):
        return False
    return any(
        isinstance(item, dict) and isinstance(item.get("metrics"), dict)
        for item in evaluations
    )


def _normalize_train_evaluation_outputs(
    outputs: dict[str, Any],
    *,
    radiance_field_id: Any,
    evaluation_id: Any,
) -> bool:
    if not isinstance(evaluation_id, str) or not evaluation_id:
        return False
    evaluations = outputs.get("evaluations")
    if not isinstance(evaluations, list):
        return False
    selected: dict[str, Any] | None = None
    selected_index: int | None = None
    for index, item in enumerate(evaluations):
        if (
            isinstance(item, dict)
            and item.get("evaluation_id") == evaluation_id
            and isinstance(item.get("metrics"), dict)
        ):
            selected = dict(item)
            selected_index = index
            break
    if selected is None:
        metric_candidates = [
            (index, dict(item))
            for index, item in enumerate(evaluations)
            if isinstance(item, dict) and isinstance(item.get("metrics"), dict)
        ]
        if len(metric_candidates) == 1:
            index, candidate = metric_candidates[0]
            candidate_evaluation_id = candidate.get("evaluation_id")
            if (
                not isinstance(candidate_evaluation_id, str)
                or not candidate_evaluation_id
                or candidate_evaluation_id == evaluation_id
            ):
                selected = candidate
                selected_index = index
    if selected is None:
        return False
    for index, item in enumerate(evaluations):
        if index == selected_index:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("metrics"), dict):
            if isinstance(item, dict) and item.get("evaluation_id") == evaluation_id:
                extra_keys = set(item) - {"evaluation_id", "radiance_field_id"}
                if extra_keys:
                    return False
            continue
        return False
    selected["evaluation_id"] = evaluation_id
    if isinstance(radiance_field_id, str) and radiance_field_id:
        selected["radiance_field_id"] = radiance_field_id
    outputs["evaluations"] = [
        selected,
        *[
            item
            for index, item in enumerate(evaluations)
            if not (
                index == selected_index
                or (isinstance(item, dict) and item.get("evaluation_id") == evaluation_id)
            )
        ],
    ]
    return True


def _radiance_provider_record(provider: str, capability: str = "radiance.train") -> ProviderRecord:
    all_rows = provider_records(installed_only=True, enabled_only=True)
    bare_provider, sep, plugin_id = provider.partition("@")
    same_provider = [
        row
        for row in all_rows
        if row.provider.provider_id == bare_provider and (not sep or row.plugin_id == plugin_id)
    ]
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
    if _public_url_issue(raw, allowed_schemes={"http"}) is not None:
        raise ValidationError(
            f"provider {row.provider.provider_id!r} container_service endpoint must be "
            "an http:// URL without credentials, query, fragment, or signed path parameters"
        )
    return raw.rstrip("/")


def _runtime_execution_contract(
    row: ProviderRecord,
    capability: str = "radiance.train",
) -> tuple[str, str, str, int]:
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
    return (
        runtime.protocol,
        runtime.protocol_version,
        runtime.execution.path,
        runtime.execution.timeout_seconds,
    )


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
    protocol, protocol_version, path, timeout_seconds = _runtime_execution_contract(
        row,
        capability,
    )
    url = f"{_service_base_url(row, capability)}{path}"
    payload = {
        "protocol": protocol,
        "protocol_version": protocol_version,
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
    status = str(body.get("status") or "succeeded")
    if status == "ok":
        status = "succeeded"
    if status != "succeeded":
        detail = body.get("error") or body.get("detail") or body
        raise RuntimeError(f"radiance provider {provider!r} failed: {detail}")

    outputs = body.get("outputs") if isinstance(body.get("outputs"), dict) else body
    if not isinstance(outputs, dict):
        raise ValidationError(
            f"radiance provider {provider!r} outputs must be a JSON object"
        )
    eval_config = spec.get("eval") if isinstance(spec.get("eval"), dict) else None
    radiance_field_id = inputs.get("radiance_field_id")
    evaluation_id = inputs.get("evaluation_id")
    if isinstance(radiance_field_id, str) and radiance_field_id:
        outputs["radiance_field_id"] = radiance_field_id
    if task_kind == "radiance_eval":
        if not isinstance(evaluation_id, str) or not evaluation_id:
            raise ValidationError("radiance_eval missing evaluation_id")
        if not isinstance(outputs.get("metrics"), dict):
            raise ValidationError(
                f"radiance provider {provider!r} eval output must include metrics"
            )
        outputs["evaluation_id"] = evaluation_id
        if _has_metric_evaluation(outputs):
            raise ValidationError(
                f"radiance provider {provider!r} eval output includes duplicate "
                "nested evaluation metrics"
            )
    elif isinstance(evaluation_id, str) and evaluation_id:
        outputs["evaluation_id"] = evaluation_id
    train_eval_expected = (
        task_kind == "radiance_train"
        and isinstance(eval_config, dict)
        and eval_config.get("enabled") is True
        and isinstance(evaluation_id, str)
        and bool(evaluation_id)
    )
    if task_kind == "radiance_train" and not train_eval_expected and (
        (isinstance(outputs.get("evaluation_id"), str) and outputs.get("evaluation_id"))
        or isinstance(outputs.get("metrics"), dict)
    ):
        raise ValidationError(
            f"radiance provider {provider!r} train output includes top-level "
            "evaluation metrics but this train task did not request an evaluation"
        )
    if (
        task_kind == "radiance_train"
        and train_eval_expected
        and isinstance(outputs.get("metrics"), dict)
    ):
        raise ValidationError(
            f"radiance provider {provider!r} train output must put evaluation "
            "metrics under outputs.evaluations"
        )
    if task_kind == "radiance_train" and _has_metric_evaluation(outputs) and not train_eval_expected:
        raise ValidationError(
            f"radiance provider {provider!r} train output includes evaluation "
            "metrics but this train task did not request an evaluation"
        )
    _validate_train_evaluation_artifacts(outputs, provider=provider)
    if (
        train_eval_expected
        and not _normalize_train_evaluation_outputs(
            outputs,
            radiance_field_id=radiance_field_id,
            evaluation_id=evaluation_id,
        )
    ):
        raise ValidationError(
            f"radiance provider {provider!r} train output must include "
            f"evaluation metrics for evaluation_id={evaluation_id}"
        )
    return outputs
