from __future__ import annotations

from typing import Any

import pytest

from app.adapters.backend_actions import (
    assert_backend_action_contract,
    backend_action_contract_violations,
)
from app.adapters.stub_backend import StubBackend

pytestmark = pytest.mark.unit


class GenericActionBackend(StubBackend):
    name = "generic"
    version = "1"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return {"features.extract.sift"}

    def list_backend_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": "generic.inspect",
                "display_name": "Inspect",
                "stability": "backend_extension",
                "side_effects": "read",
                "long_running": False,
                "gpu_required": False,
                "required_capabilities": ["features.extract.sift"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ]


class LeakyActionBackend(GenericActionBackend):
    def capabilities(self) -> set[str]:
        return {"features.extract.sift", "generic.inspect", "generic.inspect.schema"}


class MalformedActionBackend(GenericActionBackend):
    def list_backend_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": "inspect",
                "display_name": "",
                "stability": "draft",
                "side_effects": "maybe",
                "required_capabilities": ["generic.inspect"],
                "input_schema": "bad",
            },
            {
                "action_id": "inspect",
                "display_name": "Duplicate",
                "stability": "backend_extension",
                "side_effects": "read",
            },
        ]


def test_backend_action_contract_accepts_generic_non_colmap_backend() -> None:
    assert_backend_action_contract(GenericActionBackend())


def test_backend_action_contract_rejects_action_ids_in_capabilities() -> None:
    violations = backend_action_contract_violations(LeakyActionBackend())

    assert any("generic.inspect" in violation for violation in violations)
    assert any("capabilities()" in violation for violation in violations)
    with pytest.raises(AssertionError, match="Backend action contract"):
        assert_backend_action_contract(LeakyActionBackend())


def test_backend_action_contract_rejects_malformed_descriptors() -> None:
    violations = backend_action_contract_violations(MalformedActionBackend())

    assert any("should be namespaced" in violation for violation in violations)
    assert any("display_name is required" in violation for violation in violations)
    assert any("stability must be one of" in violation for violation in violations)
    assert any("side_effects must be one of" in violation for violation in violations)
    assert any("input_schema must be an object" in violation for violation in violations)
    assert any("non-portable capability" in violation for violation in violations)
    assert any("duplicate action_id" in violation for violation in violations)
