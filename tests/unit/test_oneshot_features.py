"""Unit tests for the one-shot features service. The pycolmap
integration path is exercised by the ``needs_pycolmap`` e2e tests;
here we verify the surface that doesn't require pycolmap (input
validation, content-type sniffing, spec-to-options translation).
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.schemas.pipeline_spec import FeaturesSpec
from app.services import oneshot_service

pytestmark = pytest.mark.unit


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


def test_sift_options_from_spec_legacy_aliases_win() -> None:
    out = oneshot_service._sift_options_from_spec(
        FeaturesSpec(
            max_num_features=8192,
            sift_max_num_features=2048,
            sift_first_octave=-1,
            use_gpu=False,
        )
    )
    # Legacy alias overrides the canonical max_num_features when set.
    assert out["max_num_features"] == 2048
    assert out["first_octave"] == -1
    assert out["use_gpu"] is False


def test_sift_options_from_spec_passes_extractor_options_through() -> None:
    out = oneshot_service._sift_options_from_spec(
        FeaturesSpec(extractor_options={"edge_threshold": 5.0, "peak_threshold": 0.01})
    )
    assert out["edge_threshold"] == 5.0
    assert out["peak_threshold"] == 0.01
