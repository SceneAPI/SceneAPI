"""Unit tests for the one-shot localize service. Covers the layers
that DON'T require pycolmap (input validation, missing sparse dir,
spec translation). The pycolmap-bound localize call itself is covered
by the existing localize integration test path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sfmapi.server.adapters.registry import register_backend
from sfmapi.server.adapters.stub_backend import StubBackend
from sfmapi.server.core.errors import NotFoundError, ValidationError
from sfmapi.server.schemas.pipeline_spec import FeaturesSpec
from sfmapi.server.services import oneshot_service

pytestmark = pytest.mark.unit


class OneShotLocalizeProviderBackend(StubBackend):
    name = "oneshot_localize_provider"
    last_spec: dict[str, Any] | None = None

    def localize_from_memory(
        self,
        *,
        sparse_dir: Path,
        query_image: Path,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        OneShotLocalizeProviderBackend.last_spec = spec
        return {"ok": True, "query": query_image.name}


def test_localize_oneshot_rejects_empty_body(tmp_path: Path) -> None:
    sparse_dir = tmp_path / "sparse"
    sparse_dir.mkdir()
    with pytest.raises(ValidationError, match="empty request body"):
        oneshot_service.localize_oneshot(
            b"", recon_id="r1", spec=FeaturesSpec(), sparse_dir=sparse_dir
        )


def test_localize_oneshot_rejects_bad_content_type(tmp_path: Path) -> None:
    sparse_dir = tmp_path / "sparse"
    sparse_dir.mkdir()
    with pytest.raises(ValidationError, match="unsupported content type"):
        oneshot_service.localize_oneshot(
            b"\xff\xd8\xff\xe0fake-jpeg",
            recon_id="r1",
            spec=FeaturesSpec(),
            sparse_dir=sparse_dir,
            content_type="text/csv",
        )


def test_localize_oneshot_rejects_missing_sparse_dir(tmp_path: Path) -> None:
    """No mapping has run yet — the sparse_dir doesn't exist on disk."""
    missing = tmp_path / "no-such-dir"
    with pytest.raises(NotFoundError, match="sparse dir for recon"):
        oneshot_service.localize_oneshot(
            b"\xff\xd8\xff\xe0image-bytes",
            recon_id="r1",
            spec=FeaturesSpec(),
            sparse_dir=missing,
            content_type="image/jpeg",
        )


def test_localize_oneshot_resolves_provider_alias(tmp_path: Path) -> None:
    register_backend(
        "oneshot_localize_provider",
        OneShotLocalizeProviderBackend,
        providers=["oneshot.localize"],
    )
    sparse_dir = tmp_path / "sparse"
    sparse_dir.mkdir()

    out = oneshot_service.localize_oneshot(
        b"\xff\xd8\xff\xe0image-bytes",
        recon_id="r1",
        spec=FeaturesSpec(provider="oneshot.localize"),
        sparse_dir=sparse_dir,
        content_type="image/jpeg",
    )

    assert out.runtime.backend == "oneshot_localize_provider"
    assert out.result["ok"] is True
    assert out.spec["provider"] == "oneshot.localize"


def test_localize_oneshot_passes_selected_feature_envelope(tmp_path: Path) -> None:
    register_backend(
        "oneshot_localize_provider",
        OneShotLocalizeProviderBackend,
        providers=["oneshot.localize"],
    )
    sparse_dir = tmp_path / "sparse"
    sparse_dir.mkdir()

    out = oneshot_service.localize_oneshot(
        b"\xff\xd8\xff\xe0image-bytes",
        recon_id="r1",
        spec=FeaturesSpec(
            type="sosnet",
            provider="oneshot.localize",
            max_num_features=512,
            backend_options={"descriptor": "sosnet"},
        ),
        sparse_dir=sparse_dir,
        content_type="image/jpeg",
    )

    assert out.spec["type"] == "sosnet"
    assert OneShotLocalizeProviderBackend.last_spec is not None
    assert OneShotLocalizeProviderBackend.last_spec["type"] == "sosnet"
    assert OneShotLocalizeProviderBackend.last_spec["portable"]["type"] == "sosnet"
    assert OneShotLocalizeProviderBackend.last_spec["backend_options"] == {"descriptor": "sosnet"}
