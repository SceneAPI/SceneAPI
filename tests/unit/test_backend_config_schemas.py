from __future__ import annotations

from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters import backend_config
from app.adapters.registry import register_backend
from app.adapters.stub_backend import StubBackend
from app.core.capabilities import detect_capabilities, reset_capabilities_cache
from app.core.config import reset_settings_for_tests
from app.core.errors import ValidationError
from app.workers.tasks.extract import _feature_options
from app.workers.tasks.match import _match_options

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
    monkeypatch.setenv("SFMAPI_BACKEND", "config_test")
    register_backend("config_test", ConfigBackend)
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.main import create_app

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


def test_worker_feature_options_keep_portable_and_backend_envelopes() -> None:
    options = _feature_options(
        {
            "version": 1,
            "type": "sift",
            "provider": "config_test",
            "max_num_features": 4096,
            "backend_options": {"SiftExtraction.peak_threshold": 0.01},
            "extractor_options": {"ImageReader.single_camera": True},
        }
    )

    assert options["portable"]["max_num_features"] == 4096
    assert options["backend_options"] == {"SiftExtraction.peak_threshold": 0.01}
    assert options["legacy_options"]["extractor_options"] == {"ImageReader.single_camera": True}
    assert options["SiftExtraction.peak_threshold"] == 0.01
    assert options["ImageReader.single_camera"] is True
    assert options["sift"]["max_num_features"] == 4096


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
            "matcher_options": {"SiftMatching.max_distance": 0.7},
        },
    )

    assert options["pairs_provider"] == "colmap"
    assert options["matcher_provider"] == "colmap"
    assert options["backend_options"]["pairs"] == {"ExhaustiveMatching.block_size": 50}
    assert options["backend_options"]["matcher"] == {"SiftMatching.max_ratio": 0.75}
    assert options["legacy_options"]["matcher_options"] == {"SiftMatching.max_distance": 0.7}
    assert options["ExhaustiveMatching.block_size"] == 50
    assert options["SiftMatching.max_ratio"] == 0.75
    assert options["SiftMatching.max_distance"] == 0.7
