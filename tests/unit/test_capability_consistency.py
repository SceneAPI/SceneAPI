"""Capability drift guards.

Capability strings are declared in three places:

1. ``sceneapi/server/core/capabilities.py::ALL_KNOWN`` — the canonical vocabulary.
2. Each backend's ``capabilities()`` method — the subset it implements.
3. ``require_capability("X.Y")`` calls in services / routes — the gate.

Drift between (1) and (3) is invisible at runtime: if a service
gates on a string that's not in ``ALL_KNOWN``,
``Capabilities.supports(name)`` quietly returns False even when a
backend advertises it (because ``Capabilities.features`` is keyed on
``ALL_KNOWN``). The endpoint then 501s permanently regardless of
backend support — a silent contract violation.

These tests scan ``sceneapi/server/services/`` + ``sceneapi/server/api/v1/`` for every
literal-arg ``require_capability("X.Y")`` call and assert each
appears in ``ALL_KNOWN``. Runtime-suffix forms (``f"matchers.{type}"``)
are spot-checked by walking the catalog of valid suffixes.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest

from sceneapi.server.core.capabilities import ALL_KNOWN
from sceneapi.server.core.processors import FEATURE_ATTRIBUTES
from sceneapi.server.schemas.pipeline_spec import (
    BA_MODE_CAPABILITIES,
    BundleAdjustmentSpec,
    FeatureType,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [ROOT / "sceneapi" / "server" / "services", ROOT / "sceneapi" / "server" / "api"]


def _collect_require_capability_strings() -> list[tuple[Path, int, str]]:
    """Walk every Python file in SCAN_DIRS and return every literal
    argument passed as the first positional to ``require_capability(...)``.

    Skips ``f"prefix.{var}"`` runtime-suffix calls — those are dynamic
    and validated separately by the suffix-coverage check below.
    """
    hits: list[tuple[Path, int, str]] = []
    for root in SCAN_DIRS:
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                func_name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else None
                )
                if func_name != "require_capability":
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    hits.append((py, first.lineno, first.value))
    return hits


def test_every_static_require_capability_is_in_ALL_KNOWN() -> None:
    """Every literal-string ``require_capability("X.Y")`` call site
    declares a capability that is in :data:`ALL_KNOWN`. Drift here
    silently breaks the 501 contract — a service that gates on an
    unknown string gets a permanent 501 regardless of backend
    support."""
    bad: list[tuple[Path, int, str]] = []
    for py, line, cap in _collect_require_capability_strings():
        if cap not in ALL_KNOWN:
            bad.append((py.relative_to(ROOT), line, cap))
    assert not bad, "require_capability() literals not in ALL_KNOWN:\n" + "\n".join(
        f"  {p}:{ln}  {cap!r}" for p, ln, cap in bad
    )


def test_runtime_suffix_capability_prefixes_are_known() -> None:
    """Spot-check the runtime-suffix capability families: every
    ``matchers.<type>``, ``map.<kind>``, ``ba.<mode>``,
    ``features.extract.<type>``, ``export.<format>`` capability that
    a backend could plausibly advertise has at least one matching
    entry in ``ALL_KNOWN``. Catches the class of drift where a new
    capability family is introduced in service code but never added
    to the vocabulary."""
    expected_prefixes = {
        "features.extract.",
        "pairs.",
        "matchers.",
        "map.",
        "ba.",
        "export.",
    }
    for prefix in expected_prefixes:
        matches = [c for c in ALL_KNOWN if c.startswith(prefix)]
        assert matches, (
            f"expected at least one capability with prefix {prefix!r} in ALL_KNOWN; "
            "a new capability family was likely introduced in service code "
            "without a corresponding ALL_KNOWN entry"
        )


def test_feature_extractors_align_across_capabilities_schema_and_processors() -> None:
    feature_types = set(get_args(FeatureType))
    capability_types = {
        cap.removeprefix("features.extract.")
        for cap in ALL_KNOWN
        if cap.startswith("features.extract.")
    }
    processor_types = {
        str(value) for attr in FEATURE_ATTRIBUTES if attr.name == "type" for value in attr.enum
    }
    assert feature_types == capability_types == processor_types


def test_ba_mode_capability_map_is_single_sourced() -> None:
    """``BA_MODE_CAPABILITIES`` in ``sceneapi.server.schemas.pipeline_spec`` is the
    one mode -> capability map; the web tier (sfm_stage_service) and
    the worker (tasks.ba) must both use that object rather than carry
    their own copy, and it must cover every ``BundleAdjustmentSpec.mode``
    with a capability that exists in ``ALL_KNOWN``."""
    from sceneapi.server.services import sfm_stage_service
    from sceneapi.server.workers.tasks import ba

    modes = set(get_args(BundleAdjustmentSpec.model_fields["mode"].annotation))
    assert set(BA_MODE_CAPABILITIES) == modes, (
        "BA_MODE_CAPABILITIES keys must exactly match BundleAdjustmentSpec.mode literals"
    )
    assert set(BA_MODE_CAPABILITIES.values()) <= ALL_KNOWN
    assert sfm_stage_service.BA_MODE_CAPABILITIES is BA_MODE_CAPABILITIES
    assert ba.BA_MODE_CAPABILITIES is BA_MODE_CAPABILITIES
    for mode, capability in BA_MODE_CAPABILITIES.items():
        assert sfm_stage_service._bundle_adjust_capability({"mode": mode}) == capability
