"""Unit tests for the one-shot features service. The pycolmap
integration path is exercised by the ``needs_pycolmap`` e2e tests;
here we verify the surface that doesn't require pycolmap (input
validation, content-type sniffing, spec-to-options translation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.adapters.registry import register_backend
from app.adapters.stub_backend import StubBackend
from app.core.errors import ValidationError
from app.schemas.pipeline_spec import FeaturesSpec
from app.services import oneshot_service

pytestmark = pytest.mark.unit


class OneShotProviderBackend(StubBackend):
    name = "oneshot_provider"

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        return {"num_keypoints": 0}

    def read_keypoints(
        self,
        *,
        database_path: Path,
        image_id: int,
    ) -> tuple[list[list[float]], bytes, int]:
        return [], b"", 128


class OneShotSosnetBackend(StubBackend):
    name = "oneshot_sosnet_provider"
    last_options: dict[str, Any] | None = None

    def extract_features(
        self,
        *,
        database_path: Path,
        image_root: Path,
        image_list: list[str],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        OneShotSosnetBackend.last_options = options
        return {"num_keypoints": 0}

    def read_keypoints(
        self,
        *,
        database_path: Path,
        image_id: int,
    ) -> tuple[list[list[float]], bytes, int]:
        return [], b"", 256


def test_extract_features_oneshot_rejects_empty_body() -> None:
    with pytest.raises(ValidationError, match="empty request body"):
        oneshot_service.extract_features_oneshot(b"", FeaturesSpec())


def test_extract_features_oneshot_rejects_bad_content_type() -> None:
    with pytest.raises(ValidationError, match="unsupported content type"):
        oneshot_service.extract_features_oneshot(
            b"\xff\xd8\xff\xe0fake-jpeg",
            FeaturesSpec(),
            content_type="text/csv",
        )


def test_sniff_extension_recognizes_canonical_magics() -> None:
    assert oneshot_service._sniff_extension(b"\xff\xd8\xff\x00\x00\x00\x00\x00") == ".jpg"
    assert oneshot_service._sniff_extension(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4) == ".png"
    assert oneshot_service._sniff_extension(b"II*\x00" + b"\x00" * 4) == ".tif"
    assert oneshot_service._sniff_extension(b"MM\x00*" + b"\x00" * 4) == ".tif"
    assert oneshot_service._sniff_extension(b"BM" + b"\x00" * 6) == ".bmp"
    assert oneshot_service._sniff_extension(b"RIFFwxyzWEBP" + b"\x00" * 4) == ".webp"
    assert oneshot_service._sniff_extension(b"\x00\x00\x00\x00\x00\x00\x00\x00") is None
    assert oneshot_service._sniff_extension(b"") is None


def test_ext_for_content_type_maps_image_types() -> None:
    assert oneshot_service._ext_for_content_type("image/jpeg") == ".jpg"
    assert oneshot_service._ext_for_content_type("image/png") == ".png"
    assert oneshot_service._ext_for_content_type("image/tiff") == ".tif"
    assert oneshot_service._ext_for_content_type("image/bmp") == ".bmp"
    assert oneshot_service._ext_for_content_type("image/webp") == ".webp"
    assert oneshot_service._ext_for_content_type("application/octet-stream") is None
    assert oneshot_service._ext_for_content_type("text/plain") is None


def test_sift_options_from_spec_default() -> None:
    out = oneshot_service._sift_options_from_spec(FeaturesSpec())
    assert out == {"max_num_features": 8192, "use_gpu": True}


def test_sift_options_from_spec_uses_canonical_fields() -> None:
    out = oneshot_service._sift_options_from_spec(
        FeaturesSpec(
            max_num_features=2048,
            use_gpu=False,
        )
    )
    assert out["max_num_features"] == 2048
    assert out["use_gpu"] is False


def test_sift_options_from_spec_passes_backend_options_through() -> None:
    out = oneshot_service._sift_options_from_spec(
        FeaturesSpec(backend_options={"edge_threshold": 5.0, "peak_threshold": 0.01})
    )
    assert out["edge_threshold"] == 5.0
    assert out["peak_threshold"] == 0.01


def test_feature_options_from_spec_uses_worker_style_envelope() -> None:
    out = oneshot_service._feature_options_from_spec(
        FeaturesSpec(
            type="sosnet",
            max_num_features=2048,
            use_gpu=False,
            backend_options={"descriptor": "sosnet"},
        )
    )

    assert out["type"] == "sosnet"
    assert out["max_num_features"] == 2048
    assert out["use_gpu"] is False
    assert out["portable"]["type"] == "sosnet"
    assert out["portable"]["max_num_features"] == 2048
    assert out["backend_options"] == {"descriptor": "sosnet"}
    assert out["descriptor"] == "sosnet"
    assert out["sift"]["max_num_features"] == 2048
    assert out["sift"]["use_gpu"] is False


def test_read_back_keypoints_uses_selected_feature_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_require_backend_method(
        backend: object,
        method_name: str,
        *,
        capability: str,
    ):
        calls.append((method_name, capability))

        def read_keypoints(**_: Any) -> tuple[list[list[float]], bytes, int]:
            return [], b"", 256

        return read_keypoints

    monkeypatch.setattr(
        oneshot_service,
        "require_backend_method",
        fake_require_backend_method,
    )

    _, _, descriptor_dim = oneshot_service._read_back_keypoints(
        tmp_path / "oneshot.db",
        "oneshot.png",
        backend=object(),
        feature_capability="features.extract.sosnet",
    )

    assert descriptor_dim == 256
    assert calls == [("read_keypoints", "features.extract.sosnet")]


def test_extract_features_oneshot_resolves_provider_alias() -> None:
    register_backend(
        "oneshot_provider",
        OneShotProviderBackend,
        providers=["oneshot.provider"],
    )
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1).to_bytes(4, "big") * 2

    out = oneshot_service.extract_features_oneshot(
        png_header,
        FeaturesSpec(provider="oneshot.provider"),
        content_type="image/png",
    )

    assert out.runtime.backend == "oneshot_provider"
    assert out.spec["provider"] == "oneshot.provider"


def test_extract_features_oneshot_passes_non_sift_feature_envelope() -> None:
    register_backend(
        "oneshot_sosnet_provider",
        OneShotSosnetBackend,
        providers=["oneshot.sosnet"],
    )
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1).to_bytes(4, "big") * 2

    out = oneshot_service.extract_features_oneshot(
        png_header,
        FeaturesSpec(
            type="sosnet",
            provider="oneshot.sosnet",
            max_num_features=321,
            use_gpu=False,
            backend_options={"descriptor": "sosnet"},
        ),
        content_type="image/png",
    )

    assert out.features.type == "sosnet"
    assert out.features.descriptor_dim == 256
    assert OneShotSosnetBackend.last_options is not None
    assert OneShotSosnetBackend.last_options["type"] == "sosnet"
    assert OneShotSosnetBackend.last_options["portable"]["type"] == "sosnet"
    assert OneShotSosnetBackend.last_options["backend_options"] == {"descriptor": "sosnet"}
