from __future__ import annotations

from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from sceneapi.server.adapters import backend_config
from sceneapi.server.adapters.registry import register_backend
from sceneapi.server.adapters.stub_backend import StubBackend
from sceneapi.server.core.capabilities import detect_capabilities, reset_capabilities_cache
from sceneapi.server.core.config import reset_settings_for_tests
from sceneapi.server.core.errors import ValidationError
from sceneapi.server.workers.tasks.extract import _feature_options
from sceneapi.server.workers.tasks.match import _match_options

pytestmark = pytest.mark.unit


class ConfigBackend(StubBackend):
    name = "config_test"
    version = "1.0"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return {
            "features.extract.sift",
            "pairs.exhaustive",
            "matchers.nn-mutual",
            "matches.verify",
            "map.incremental",
        }

    def list_backend_config_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "config_id": "config_test.features.sift",
                "stage": "features",
                "capability": "features.extract.sift",
                "provider": "config_test",
                "display_name": "Test SIFT options",
                "option_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "SiftExtraction.peak_threshold": {"type": "number"},
                        "ImageReader.single_camera": {"type": "boolean"},
                    },
                },
            }
        ]


async def _client_for_backend(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv("SCENEAPI_BACKEND", "config_test")
    register_backend("config_test", ConfigBackend, providers=["config_test"])
    reset_settings_for_tests()
    reset_capabilities_cache()
    from sceneapi.server.main import create_app

    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    )


async def test_backend_config_schema_catalog_is_discoverable(
    db_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await _client_for_backend(monkeypatch) as client:
        backend = await client.get("/v1/backend")
        assert backend.status_code == 200
        assert backend.json()["config_schema_count"] == 1
        assert backend.json()["_links"]["config_schemas"]["href"] == "/v1/backend/config-schemas"

        caps = detect_capabilities()
        assert caps.supports("backend.config_schemas")

        page = await client.get("/v1/backend/config-schemas")
        assert page.status_code == 200, page.text
        item = page.json()["items"][0]
        assert item["config_id"] == "config_test.features.sift"
        assert item["option_schema"]["properties"]["SiftExtraction.peak_threshold"]

        compact = await client.get("/v1/backend/config-schemas?include_schemas=false")
        assert compact.status_code == 200
        assert compact.json()["items"][0]["option_schema"] is None

        detail = await client.get("/v1/backend/config-schemas/config_test.features.sift")
        assert detail.status_code == 200
        assert detail.json()["provider"] == "config_test"


def test_backend_options_validate_against_published_schema() -> None:
    backend = ConfigBackend()

    valid = backend_config.validate_backend_options(
        stage="features",
        capability="features.extract.sift",
        provider="config_test",
        options={
            "SiftExtraction.peak_threshold": 0.01,
            "ImageReader.single_camera": True,
        },
        backend=backend,
    )
    assert valid["valid"] is True

    with pytest.raises(ValidationError, match="not a valid option"):
        backend_config.validate_backend_options(
            stage="features",
            capability="features.extract.sift",
            provider="config_test",
            options={"bad_option": 1},
            backend=backend,
        )

    with pytest.raises(ValidationError, match="expects JSON type number"):
        backend_config.validate_backend_options(
            stage="features",
            capability="features.extract.sift",
            provider="config_test",
            options={"SiftExtraction.peak_threshold": "low"},
            backend=backend,
        )


def test_backend_options_resolve_provider_alias_when_no_backend_is_passed() -> None:
    register_backend("config_test", ConfigBackend, providers=["config_provider"])

    valid = backend_config.validate_backend_options(
        stage="features",
        capability="features.extract.sift",
        provider="config_provider",
        options={"SiftExtraction.peak_threshold": 0.01},
    )

    assert valid["valid"] is True
    with pytest.raises(ValidationError, match="not a valid option"):
        backend_config.validate_backend_options(
            stage="features",
            capability="features.extract.sift",
            provider="config_provider",
            options={"bad_option": 1},
        )


def test_worker_feature_options_keep_portable_and_backend_envelopes() -> None:
    options = _feature_options(
        {
            "version": 1,
            "type": "sift",
            "provider": "config_test",
            "max_num_features": 4096,
            "backend_options": {"SiftExtraction.peak_threshold": 0.01},
        }
    )

    assert options["portable"]["max_num_features"] == 4096
    assert options["backend_options"] == {"SiftExtraction.peak_threshold": 0.01}
    assert options["SiftExtraction.peak_threshold"] == 0.01
    assert options["sift"]["max_num_features"] == 4096


class ColmapLikeBackend(StubBackend):
    """Fake COLMAP-family backend: advertises colmap capabilities and a
    machine-readable command schema. COLMAP stage-config vendor data now
    lives in the COLMAP plugin family (evicted from core), so a bare
    COLMAP-like backend serves no config schemas here — used below to prove
    the radiance path stays capability-gated for a non-radiance backend."""

    name = "colmap_like"
    version = "1.0"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return {
            "features.extract.sift",
            "pairs.exhaustive",
            "pairs.spatial",
            "pairs.from_poses",
            "matchers.nn-mutual",
            "matches.verify",
            "ba.standard",
        }

    def colmap_command_schema(self, command: str) -> dict[str, Any]:
        return {
            "schema_source": "synthetic",
            "option_count": 2,
            "options": [
                {"name": "SiftMatching.max_ratio", "schema": {"type": "number"}},
                # runtime-managed -> must be filtered out of the option schema
                {"name": "database_path", "schema": {"type": "string"}},
            ],
        }


class RadianceLikeBackend(StubBackend):
    name = "radiance_like"
    version = "1.0"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return {"radiance.train", "radiance.evaluate"}


def test_bare_colmap_like_backend_serves_no_framework_config_schemas() -> None:
    # COLMAP stage-config vendor data was evicted to the plugin family; a
    # backend that only exposes the duck-typed colmap_command_schema (and does
    # not implement list_backend_config_schemas) no longer gets config rows
    # synthesized by core. The three real COLMAP providers self-serve via
    # discovery (covered in the sceneapi_map suite).
    assert backend_config.list_backend_config_schemas(ColmapLikeBackend()) == []
    assert backend_config.backend_config_contract_violations(ColmapLikeBackend()) == []


def test_framework_radiance_train_schema_is_capability_gated() -> None:
    rows = backend_config._radiance_config_descriptors(RadianceLikeBackend(), include_schema=True)
    by_id = {r["config_id"]: r for r in rows}
    assert "radiance.train" in by_id
    props = by_id["radiance.train"]["option_schema"]["properties"]
    assert {"num_gaussians", "max_resolution", "init", "test_every"} <= set(props)
    # a backend without radiance.train serves nothing from this path.
    assert (
        backend_config._radiance_config_descriptors(ColmapLikeBackend(), include_schema=True) == []
    )
    # regression guard: the radiance.train schema must satisfy the config-schema
    # contract checker (valid stage + additionalProperties:false). This catches
    # the original bug where stage="radiance" was not a valid stage and the
    # schema used additionalProperties:true.
    assert backend_config.backend_config_contract_violations(RadianceLikeBackend()) == []


def test_worker_match_options_split_pairs_and_matcher_backend_options() -> None:
    options = _match_options(
        cast(Any, object()),
        {
            "version": 1,
            "strategy": "exhaustive",
            "provider": "colmap",
            "backend_options": {"ExhaustiveMatching.block_size": 50},
        },
        {
            "version": 1,
            "type": "nn-mutual",
            "provider": "colmap",
            "backend_options": {"SiftMatching.max_ratio": 0.75},
        },
    )

    assert options["pairs_provider"] == "colmap"
    assert options["matcher_provider"] == "colmap"
    assert options["backend_options"]["pairs"] == {"ExhaustiveMatching.block_size": 50}
    assert options["backend_options"]["matcher"] == {"SiftMatching.max_ratio": 0.75}
    assert options["ExhaustiveMatching.block_size"] == 50
    assert options["SiftMatching.max_ratio"] == 0.75
