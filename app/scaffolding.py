"""Plugin-scaffolding templates and writer for ``sfmapi scaffold-plugin``.

Produces the smallest directory tree that pip-installs cleanly, exposes
a ``[sfmapi.backends]`` entry point, and passes the framework's
``check_backend`` contract gate as a no-op stub. Plugin authors then
flesh out ``backend.py`` (and the manifest's capabilities + provider
list) to actually implement engine functionality.

The scaffolded plugin uses the canonical :class:`sfmapi.backends.Plugin`
dataclass rather than re-rolling the per-plugin SfmapiBackendPlugin
dataclass that every existing baseline plugin ships with.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from pathlib import Path

# ----- naming validation ----------------------------------------------------


# Same shape app.core.ids.PROVIDER_ID_RE enforces on provider_ids.
# Reused here for plugin_ids: a plugin_id is also the package suffix
# (sfmapi_<id>) and the entry-point name, so the same constraints apply.
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_plugin_id(plugin_id: str) -> None:
    """Reject ids that won't survive being used as both a Python module
    suffix and an sfmapi backend name.

    Allowed: lowercase letters, digits, underscores; must start with a
    letter. Dots and hyphens are rejected because the result is used
    inside a module path (``sfmapi_<id>.plugin``).
    """
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise ValueError(
            f"plugin_id must match {_PLUGIN_ID_RE.pattern!r} "
            f"(lowercase letters/digits/underscores, leading letter): "
            f"got {plugin_id!r}"
        )


# ----- templates ------------------------------------------------------------


_PYPROJECT_TEMPLATE = string.Template(
    """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sfmapi-${plugin_id_dash}"
version = "0.0.0"
description = "${description}"
license = { text = "Apache-2.0" }
requires-python = ">=3.12,<3.13"
dependencies = [
    "sfmapi>=0.0.1",
]

[project.entry-points."sfmapi.backends"]
${plugin_id} = "sfmapi_${plugin_id}.plugin:plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/sfmapi_${plugin_id}"]
"""
)


_INIT_TEMPLATE = string.Template(
    '''\
"""sfmapi_${plugin_id} — ${display_name} backend plugin for sfmapi."""

from .plugin import plugin

__all__ = ["plugin"]
'''
)


_PLUGIN_TEMPLATE = string.Template(
    '''\
"""Plugin entry point for ${display_name}.

The MANIFEST + Plugin instance below are consumed by sfmapi at
discovery and registration time. The framework's check_backend gate
runs against the backend instance returned by Plugin.backend_factory.
"""

from __future__ import annotations

from sfmapi.backends import Plugin

from .backend import ${class_name}Backend

MANIFEST: dict = {
    "schema_version": 1,
    "plugin_id": "${plugin_id}",
    "display_name": "${display_name}",
    "description": "${description}",
    "package_name": "sfmapi-${plugin_id_dash}",
    "github_url": "https://github.com/OWNER/sfmapi_${plugin_id}",
    "entry_points": ["sfmapi_${plugin_id}.plugin:plugin"],
    "providers": [
        {
            "provider_id": "${plugin_id}",
            "display_name": "${display_name}",
            "capabilities": [],
            "backend_actions": [],
            "priority_hint": 100,
        },
    ],
    "runtime_modes": {},
    "capabilities": [],
    "backend_actions": [],
    "config_schemas": [],
    "artifact_contracts": [],
    "licenses": [{"name": "Apache-2.0"}],
    "upstream_projects": [],
    "compatibility": {"sfmapi": ">=0.0.1"},
    "conformance": {"status": "not_run", "suite": "sfmapi-bench"},
    "trust_tier": "community",
}

plugin = Plugin(
    manifest=MANIFEST,
    backend_name="${plugin_id}",
    backend_factory=${class_name}Backend,
)
'''
)


_BACKEND_TEMPLATE = string.Template(
    '''\
"""Backend implementation for ${display_name}.

This is a stub: it satisfies the framework's minimal Backend contract
so the plugin loads, registers, and passes ``sfmapi check-backend``,
but does not implement any capability. Fill in capabilities (e.g.
``features.extract.sift``, ``map.global``) and the matching backend
methods (``extract_features``, ``run_mapping`` etc.) to make the
plugin actually do work; see ``sfmapi.backends.SfmBackend`` and the
``sfmapi`` repo's existing plugin examples for the method shapes.
"""

from __future__ import annotations


class ${class_name}Backend:
    """Stub backend. Replace with a real implementation."""

    name = "${plugin_id}"
    version = "0.0.0"
    vendor = "${vendor}"

    def capabilities(self) -> list[str]:
        # Add portable capabilities here, e.g. "features.extract.sift".
        # The canonical vocabulary lives in app.core.capabilities.ALL_KNOWN.
        return []

    def runtime_versions(self) -> dict:
        # Backend, engine, model, and CUDA versions that should appear
        # in /v1/backend.runtime_versions.
        return {"backend": self.version}
'''
)


_README_TEMPLATE = string.Template(
    """\
# sfmapi-${plugin_id_dash}

${description}

## Install (editable, for development)

```bash
uv pip install -e .
```

After install, the plugin shows up in:

```python
import importlib.metadata as m
list(m.entry_points(group="sfmapi.backends"))
# -> contains EntryPoint(name='${plugin_id}', value='sfmapi_${plugin_id}.plugin:plugin', ...)
```

## Wire into sfmapi

```bash
SFMAPI_BACKEND=${plugin_id} SFMAPI_AUTO_LOAD_BACKEND_PLUGINS=true \\
    uv run uvicorn sfmapi.runtime:create_app --factory
```

## Next steps

1. Edit `src/sfmapi_${plugin_id}/backend.py` to declare real
   capabilities and implement the matching backend methods.
2. Edit `src/sfmapi_${plugin_id}/plugin.py` to add provider entries,
   config schemas, backend_actions, and capability declarations to
   `MANIFEST`.
3. Run `sfmapi check-backend --import sfmapi_${plugin_id}.plugin
   --backend ${plugin_id}` to validate the contract.
"""
)


_TEST_TEMPLATE = string.Template(
    '''\
"""Smoke tests for the scaffolded sfmapi_${plugin_id} plugin."""

from __future__ import annotations

import importlib.metadata as m


def test_entry_point_registered() -> None:
    names = {ep.name for ep in m.entry_points(group="sfmapi.backends")}
    assert "${plugin_id}" in names


def test_plugin_imports_and_exposes_manifest() -> None:
    from sfmapi_${plugin_id}.plugin import plugin

    manifest = plugin.get_plugin_manifest()
    assert manifest["plugin_id"] == "${plugin_id}"
    assert manifest["entry_points"] == ["sfmapi_${plugin_id}.plugin:plugin"]


def test_backend_factory_produces_a_named_backend() -> None:
    from sfmapi_${plugin_id}.plugin import plugin

    backend = plugin.backend_factory()
    assert backend.name == "${plugin_id}"
    # Stub returns empty capabilities; flesh out in a later commit.
    assert backend.capabilities() == []
'''
)


# ----- the scaffolding entry point ------------------------------------------


@dataclass(frozen=True)
class ScaffoldedFile:
    path: Path
    bytes_written: int


def _to_class_name(plugin_id: str) -> str:
    """``my_engine`` -> ``MyEngine``. Used for the backend class name."""
    return "".join(part.capitalize() for part in plugin_id.split("_"))


def scaffold_plugin(
    plugin_id: str,
    *,
    output_dir: Path,
    display_name: str | None = None,
    description: str | None = None,
    vendor: str = "unknown",
    overwrite: bool = False,
) -> list[ScaffoldedFile]:
    """Create the scaffold for an sfmapi backend plugin at
    ``output_dir/sfmapi_<plugin_id>``.

    Raises ``ValueError`` for an invalid plugin_id, or ``FileExistsError``
    if any target file would be overwritten and ``overwrite`` is False.
    """
    validate_plugin_id(plugin_id)
    root = output_dir / f"sfmapi_{plugin_id}"
    pkg = root / "src" / f"sfmapi_{plugin_id}"
    tests = root / "tests"
    class_name = _to_class_name(plugin_id)
    subst = {
        "plugin_id": plugin_id,
        "plugin_id_dash": plugin_id.replace("_", "-"),
        "class_name": class_name,
        "display_name": display_name or class_name,
        "description": (
            description or f"sfmapi backend plugin for {display_name or class_name}."
        ),
        "vendor": vendor,
    }
    plan: list[tuple[Path, str]] = [
        (root / "pyproject.toml", _PYPROJECT_TEMPLATE.substitute(subst)),
        (root / "README.md", _README_TEMPLATE.substitute(subst)),
        (pkg / "__init__.py", _INIT_TEMPLATE.substitute(subst)),
        (pkg / "plugin.py", _PLUGIN_TEMPLATE.substitute(subst)),
        (pkg / "backend.py", _BACKEND_TEMPLATE.substitute(subst)),
        (tests / "__init__.py", ""),
        (tests / "test_plugin.py", _TEST_TEMPLATE.substitute(subst)),
    ]
    if not overwrite:
        clashes = [p for p, _ in plan if p.exists()]
        if clashes:
            joined = ", ".join(str(p.relative_to(output_dir)) for p in clashes)
            raise FileExistsError(
                f"refusing to overwrite existing files: {joined}; pass overwrite=True to force"
            )

    written: list[ScaffoldedFile] = []
    for path, body in plan:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = body.encode("utf-8")
        path.write_bytes(encoded)
        written.append(ScaffoldedFile(path=path, bytes_written=len(encoded)))
    return written


# ----- off-wire contract scaffolding ----------------------------------------
#
# Mirrors scaffold_plugin but for an app.core off-wire contract (a data
# standard with cross-tier parity, e.g. colmap_db). Generates the two
# repo-side files; the single cross-repo step (registering in
# sfmapi-cpp/tools/gen_contracts.py) is printed for the author, since that
# file lives in the C++ port repo.


# A contract name becomes a Python module name (app/core/<name>.py), a
# test filename, an artifact filename, and the C++ accessor stem — same
# constraints as a plugin id.
_CONTRACT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_contract_name(name: str) -> None:
    if not _CONTRACT_NAME_RE.match(name):
        raise ValueError(
            f"contract name must match {_CONTRACT_NAME_RE.pattern!r} "
            f"(lowercase letters/digits/underscores, leading letter): got {name!r}"
        )


_CONTRACT_MODULE_TEMPLATE = string.Template(
    '''\
"""${title} — an sfmapi off-wire core contract.

A repo-owned data standard with no HTTP endpoint. This module is the
single source of truth; ``tools/gen_contracts.py`` serializes
:func:`contract_dict` to ``parity/contracts/${name}.contract.json`` +
``src/contracts/${name}_contract.inc``, and check_sync's contract-parity
gate fails if the C++ embed drifts. Implementations conform to this
contract; the contract depends on no implementation.
"""

from __future__ import annotations

CONTRACT_NAME = "${name}"
# Version of THIS serialization shape (bump on a breaking shape change).
CONTRACT_SCHEMA_VERSION = 1


def contract_dict() -> dict:
    """The ${name} contract as a deterministic, JSON-serializable dict.

    Flesh out the payload with the actual standard. Keep ordering stable
    (declaration order / sorted) so the generated artifact is reproducible.
    """
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        # TODO: add the contract's fields here.
    }


__all__ = ["CONTRACT_NAME", "CONTRACT_SCHEMA_VERSION", "contract_dict"]
'''
)


_CONTRACT_TEST_TEMPLATE = string.Template(
    '''\
"""Contract test for app.core.${name} (off-wire core contract)."""

from __future__ import annotations

import json

from app.core import ${name} as contract


def test_contract_dict_is_json_serializable_and_self_describing() -> None:
    payload = contract.contract_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["contract"] == contract.CONTRACT_NAME == "${name}"
    assert payload["contract_schema_version"] == contract.CONTRACT_SCHEMA_VERSION


# TODO: pin the contract's real fields here (the structured standard),
# so drift from the intended schema is caught at the source.
'''
)


def scaffold_contract(
    name: str,
    *,
    core_dir: Path,
    tests_dir: Path,
    title: str | None = None,
    overwrite: bool = False,
) -> list[ScaffoldedFile]:
    """Create the two repo-side files for a new off-wire core contract:
    ``<core_dir>/<name>.py`` and ``<tests_dir>/test_<name>_contract.py``.

    Returns the written files. The caller is expected to surface the
    remaining (cross-repo) step: registering the contract in
    ``sfmapi-cpp/tools/gen_contracts.py``.

    Raises ``ValueError`` for an invalid name, ``FileExistsError`` on a
    clash when ``overwrite`` is False.
    """
    validate_contract_name(name)
    subst = {"name": name, "title": title or _to_class_name(name)}
    plan: list[tuple[Path, str]] = [
        (core_dir / f"{name}.py", _CONTRACT_MODULE_TEMPLATE.substitute(subst)),
        (tests_dir / f"test_{name}_contract.py",
         _CONTRACT_TEST_TEMPLATE.substitute(subst)),
    ]
    if not overwrite:
        clashes = [p for p, _ in plan if p.exists()]
        if clashes:
            joined = ", ".join(str(p) for p in clashes)
            raise FileExistsError(
                f"refusing to overwrite existing files: {joined}; "
                "pass overwrite=True to force"
            )
    written: list[ScaffoldedFile] = []
    for path, body in plan:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = body.encode("utf-8")
        path.write_bytes(encoded)
        written.append(ScaffoldedFile(path=path, bytes_written=len(encoded)))
    return written


__all__ = [
    "ScaffoldedFile",
    "scaffold_contract",
    "scaffold_plugin",
    "validate_contract_name",
    "validate_plugin_id",
]
