from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sceneapi.server.core.errors import CapabilityUnavailableError
from sceneapi.server.core.errors import ValidationError as SfmValidationError
from sceneapi.server.core.projection_engine import project_image_directory

PILImage = pytest.importorskip("PIL.Image")

pytestmark = pytest.mark.unit


def _write_axis_panorama(path: Path, *, width: int = 128, height: int = 64) -> None:
    x = (np.arange(width, dtype=np.float32) + 0.5) / width
    y = (np.arange(height, dtype=np.float32) + 0.5) / height
    lon, lat_down = np.meshgrid((x - 0.5) * 2.0 * np.pi, (y - 0.5) * np.pi)
    rays = np.stack(
        [
            np.sin(lon) * np.cos(lat_down),
            np.sin(lat_down),
            np.cos(lon) * np.cos(lat_down),
        ],
        axis=-1,
    )
    image = np.clip((rays + 1.0) * 127.5, 0, 255).astype(np.uint8)
    PILImage.fromarray(image).save(path)


def _center_rgb(path: Path) -> np.ndarray:
    image = np.asarray(PILImage.open(path).convert("RGB"))
    return image[image.shape[0] // 2, image.shape[1] // 2]


def test_equirectangular_to_cubemap_projects_face_centers(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    _write_axis_panorama(input_root / "pano.png")

    result = project_image_directory(
        operation="equirectangular_to_cubemap",
        input_image_path=input_root,
        output_path=output_root,
        spec={
            "face_size": 64,
            "sampling": {"interpolation": "nearest"},
            "output": {"format": "png", "create_dataset": True, "dataset_name": "cube"},
        },
    )

    assert result["engine"] == "sceneapi.core.projection_engine"
    assert len(result["source_images"]) == 1
    assert len(result["output_images"]) == 6
    assert result["derived_dataset"]["name"] == "cube"
    expected = {
        "front": np.array([128, 128, 255]),
        "right": np.array([255, 128, 128]),
        "back": np.array([128, 128, 0]),
        "left": np.array([0, 128, 128]),
        "up": np.array([128, 0, 128]),
        "down": np.array([128, 255, 128]),
    }
    for face, rgb in expected.items():
        assert np.allclose(_center_rgb(output_root / f"pano__{face}.png"), rgb, atol=8)


@pytest.mark.parametrize(
    "operation",
    ["cubemap_to_equirectangular", "equirectangular_to_perspective"],
)
def test_reverse_and_perspective_are_contract_only(tmp_path: Path, operation: str) -> None:
    with pytest.raises(CapabilityUnavailableError, match="contract-only"):
        project_image_directory(
            operation=operation,
            input_image_path=tmp_path,
            output_path=tmp_path / "out",
            spec={},
        )


def test_builtin_engine_rejects_higher_order_sampling(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_axis_panorama(input_root / "pano.png")

    with pytest.raises(SfmValidationError, match="higher-order sampling"):
        project_image_directory(
            operation="equirectangular_to_cubemap",
            input_image_path=input_root,
            output_path=tmp_path / "out",
            spec={
                "face_size": 64,
                "sampling": {"interpolation": "cubic"},
                "output": {"format": "png"},
            },
        )
