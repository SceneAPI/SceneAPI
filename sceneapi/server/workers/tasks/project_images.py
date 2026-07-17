"""Portable dataset image projection worker."""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from sceneapi.server.adapters.backend import require_backend_method
from sceneapi.server.core.config import get_settings
from sceneapi.server.core.paths import Paths
from sceneapi.server.core.projection_engine import project_image_directory
from sceneapi.server.core.projections import projection_capability
from sceneapi.server.db.models import Task
from sceneapi.server.schemas.api.projections import (
    ProjectionJobRequest,
    ProjectionManifest,
    manifest_geometry_for_operation,
)
from sceneapi.server.workers._materialize import materialize_image_set
from sceneapi.server.workers._task_io import read_state
from sceneapi.server.workers.backend_resolver import backend_for_stage
from sceneapi.server.workers.tasks._registry import task_handler


def _jsonable_dict(value: dict[str, object]) -> dict[str, object]:
    decoded = json.loads(json.dumps(value, default=str))
    if not isinstance(decoded, dict):
        return {}
    return {str(key): item for key, item in decoded.items()}


def _project_with_backend(
    *,
    backend: object,
    operation: str,
    image_path: Path,
    output_path: Path,
    spec: dict[str, object],
) -> dict[str, object]:
    capability = projection_capability(operation)
    backend_capabilities = set(getattr(backend, "capabilities", lambda: set())())
    legacy_cubemap = operation == "equirectangular_to_cubemap" and (
        "spherical.render_cubemap" in backend_capabilities
    )
    if capability not in backend_capabilities and not legacy_cubemap:
        return project_image_directory(
            operation=operation,
            input_image_path=image_path,
            output_path=output_path,
            spec=spec,
        )

    project_images = getattr(backend, "project_images", None)
    if callable(project_images):
        result = project_images(
            operation=operation,
            input_image_path=image_path,
            output_path=output_path,
            spec=spec,
        )
        return result if isinstance(result, dict) else {}

    if operation == "equirectangular_to_cubemap":
        render_cubemap = require_backend_method(
            backend,
            "render_spherical_cubemap_images",
            capability=projection_capability(operation),
            reason=(
                "Backend does not implement project_images() or render_spherical_cubemap_images()."
            ),
        )
        result = render_cubemap(
            input_image_path=image_path,
            output_path=output_path,
            face_size=spec.get("face_size"),
        )
        return result if isinstance(result, dict) else {}

    method_name = {
        "cubemap_to_equirectangular": "render_cubemap_equirectangular_images",
        "equirectangular_to_perspective": "render_spherical_perspective_images",
    }[operation]
    method = require_backend_method(
        backend,
        method_name,
        capability=projection_capability(operation),
        reason=f"Backend does not implement project_images() or {method_name}().",
    )
    result = method(input_image_path=image_path, output_path=output_path, spec=spec)
    return result if isinstance(result, dict) else {}


def _image_outputs_from_files(files: list[str], *, operation: str) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "camera_model": "SPHERICAL" if operation == "cubemap_to_equirectangular" else "PINHOLE",
            "projection_role": operation,
        }
        for name in files
        if name != "projection_manifest.json"
    ]


def _default_derived_dataset(
    *,
    operation: str,
    spec: dict[str, object],
    output_images: list[dict[str, object]],
    output_path: Path,
    source_dataset_id: object,
) -> dict[str, object] | None:
    output = spec.get("output")
    output_options = output if isinstance(output, dict) else {}
    if not bool(output_options.get("create_dataset", True)):
        return None
    dataset_name = output_options.get("dataset_name")
    if operation == "cubemap_to_equirectangular":
        camera_model = "SPHERICAL"
        intrinsics_mode = "single_camera"
        is_spherical = True
        rig_config: dict[str, object] = {
            "kind": "equirectangular",
            "convention": spec.get("convention", "sfmapi-opencv"),
        }
    elif operation == "equirectangular_to_perspective":
        camera_model = "PINHOLE"
        intrinsics_mode = "per_image"
        is_spherical = False
        rig_config = {
            "kind": "perspective_views",
            "convention": spec.get("convention", "sfmapi-opencv"),
        }
    else:
        camera_model = "PINHOLE"
        intrinsics_mode = "per_image"
        is_spherical = False
        rig_config = {
            "kind": "cubemap",
            "convention": spec.get("convention", "sfmapi-opencv"),
            "face_order": spec.get("face_order"),
        }
    return {
        "name": dataset_name if isinstance(dataset_name, str) else None,
        "camera_model": camera_model,
        "intrinsics_mode": intrinsics_mode,
        "is_spherical": is_spherical,
        "rig_config": rig_config,
        "source_dataset_id": source_dataset_id,
        "root": str(output_path),
        "images": output_images,
    }


@task_handler("project_images")
def run(task: Task) -> dict[str, Any]:
    inputs, raw_spec = read_state(task)
    request = ProjectionJobRequest.model_validate(raw_spec)
    operation = request.operation
    operation_spec = request.operation_spec()

    materialization = inputs["materialization"]
    dataset_dir = Path(inputs["dataset_dir"])

    paths = Paths(get_settings())
    stage = paths.workspace_root / "_projection_stage" / task.task_id
    image_path, _ = materialize_image_set(materialization, stage)

    output_path = dataset_dir / "_projections" / operation / task.task_id
    output_path.mkdir(parents=True, exist_ok=True)

    backend_result = _jsonable_dict(
        _project_with_backend(
            backend=backend_for_stage(operation_spec),
            operation=operation,
            image_path=image_path,
            output_path=output_path,
            spec=operation_spec,
        )
    )

    rendered_files = [
        p.relative_to(output_path).as_posix() for p in sorted(output_path.rglob("*")) if p.is_file()
    ]
    source_images = backend_result.get("source_images")
    output_images = backend_result.get("output_images")
    if not isinstance(source_images, list):
        source_images = []
    if not isinstance(output_images, list):
        output_images = _image_outputs_from_files(rendered_files, operation=operation)
    derived_dataset = backend_result.get("derived_dataset")
    if isinstance(derived_dataset, dict):
        derived_dataset = {
            **derived_dataset,
            "source_dataset_id": inputs.get("dataset_id"),
            "root": str(output_path),
            "images": output_images,
        }
    else:
        derived_dataset = _default_derived_dataset(
            operation=operation,
            spec=operation_spec,
            output_images=output_images,
            output_path=output_path,
            source_dataset_id=inputs.get("dataset_id"),
        )
    face_order = operation_spec.get("face_order")
    face_axes = backend_result.get("face_axes")
    manifest_kwargs: dict[str, Any] = {}
    if isinstance(face_order, list):
        manifest_kwargs["face_order"] = face_order
    if isinstance(face_axes, dict):
        manifest_kwargs["face_axes"] = face_axes
    if operation == "equirectangular_to_cubemap" and "face_axes" not in manifest_kwargs:
        manifest_kwargs.update(manifest_geometry_for_operation(operation))

    manifest = ProjectionManifest(
        operation=operation,
        spec=operation_spec,
        output_path=str(output_path),
        files=rendered_files,
        source_images=source_images,
        output_images=output_images,
        derived_dataset=derived_dataset,
        backend_result=backend_result,
        **manifest_kwargs,
    )

    manifest_path = output_path / "projection_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if "projection_manifest.json" not in rendered_files:
        rendered_files.append("projection_manifest.json")

    with contextlib.suppress(OSError):
        shutil.rmtree(stage)

    return {
        "operation": operation,
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "num_files": len(rendered_files),
        "source_images": source_images,
        "output_images": output_images,
        "derived_dataset": derived_dataset,
        "artifacts": [
            {
                "kind": "projection.images.v1",
                "name": operation,
                "uri": str(output_path),
                "artifact_format": "sfmapi.projection.images.v1",
                "schema_version": 1,
                "files": [{"name": name, "uri": name} for name in rendered_files],
                "metadata": {
                    "operation": operation,
                    "manifest": "projection_manifest.json",
                    "convention": operation_spec.get("convention"),
                },
            }
        ],
    }
