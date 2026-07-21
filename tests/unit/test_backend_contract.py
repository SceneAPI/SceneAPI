from __future__ import annotations

from typing import Any, cast

import pytest

from sceneapi.server.adapters.backend_contract import (
    assert_backend_contract,
    backend_capability_contract_violations,
    backend_contract_violations,
)
from sceneapi.server.adapters.registry import register_backend
from sceneapi.server.adapters.stub_backend import StubBackend
from sceneapi.server.cli import main as cli_main
from sceneapi.server.core.capabilities import reset_capabilities_cache

pytestmark = pytest.mark.unit


class GoodBackend(StubBackend):
    name = "good_backend"
    version = "1"

    def capabilities(self) -> set[str]:
        return {"features.extract.sift"}

    def list_backend_config_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "config_id": "good.features.sift",
                "stage": "features",
                "capability": "features.extract.sift",
                "provider": "good_backend",
                "display_name": "Good SIFT options",
                "option_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "SiftExtraction.peak_threshold": {"type": "number"},
                    },
                },
            }
        ]


class UnknownCapabilityBackend(GoodBackend):
    def capabilities(self) -> set[str]:
        return {"features.extract.sft"}


class LeakyConfigBackend(GoodBackend):
    def capabilities(self) -> set[str]:
        return {"features.extract.sift", "good.features.sift"}


class MissingCapabilityConfigBackend(GoodBackend):
    def capabilities(self) -> set[str]:
        return set()


class MalformedConfigBackend(GoodBackend):
    def list_backend_config_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "config_id": "badconfig",
                "stage": "bad_stage",
                "capability": "features.extract.sift",
                "provider": "bad provider",
                "display_name": "Bad",
                "option_schema": {
                    "type": "object",
                    "properties": {
                        "database_path": {"type": "string"},
                        "freeform": "bad",
                    },
                    "required": ["database_path", "missing"],
                },
            }
        ]


def test_backend_contract_accepts_good_backend() -> None:
    assert_backend_contract(GoodBackend())


def test_backend_contract_reports_unknown_capability_with_hint() -> None:
    violations = backend_capability_contract_violations(UnknownCapabilityBackend())

    assert any("unknown capability" in violation for violation in violations)
    assert any("features.extract.sift" in violation for violation in violations)


def test_backend_contract_rejects_config_schema_ids_in_capabilities() -> None:
    violations = backend_contract_violations(LeakyConfigBackend())

    assert any("config schema ids must not be advertised" in violation for violation in violations)
    with pytest.raises(AssertionError, match="Backend contract violations"):
        assert_backend_contract(LeakyConfigBackend())


def test_backend_contract_rejects_schema_for_unadvertised_capability() -> None:
    violations = backend_contract_violations(MissingCapabilityConfigBackend())

    assert any("does not advertise" in violation for violation in violations)


def test_backend_contract_rejects_malformed_config_schema() -> None:
    violations = backend_contract_violations(MalformedConfigBackend())

    assert any("config_id should be namespaced" in violation for violation in violations)
    assert any("stage must be one of" in violation for violation in violations)
    assert any("provider must match" in violation for violation in violations)
    assert any("additionalProperties must be false" in violation for violation in violations)
    # Runtime-managed-option filtering is COLMAP vendor data, evicted to the
    # COLMAP plugin family: core's generic contract checker no longer flags
    # runtime-managed options (the providers filter them from their served
    # schemas, covered in the scenemap suite). ``database_path`` above is
    # therefore now a benign property here.
    assert any("properties.freeform must be an object" in violation for violation in violations)
    assert any(
        "required contains unknown property 'missing'" in violation for violation in violations
    )


def test_check_backend_cli_reports_ok(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register_backend("good_backend", cast(Any, lambda: GoodBackend()))
    monkeypatch.setenv("SCENEAPI_BACKEND", "good_backend")
    reset_capabilities_cache()

    cli_main(["check-backend"])

    assert "OK backend contract: good_backend 1" in capsys.readouterr().out


def test_check_backend_cli_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register_backend("bad_backend", cast(Any, lambda: UnknownCapabilityBackend()))
    monkeypatch.setenv("SCENEAPI_BACKEND", "bad_backend")
    reset_capabilities_cache()

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["check-backend"])

    assert exc_info.value.code == 1
    assert "Backend contract violations" in capsys.readouterr().out
