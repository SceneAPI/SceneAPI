from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The framework core: the API, the public backend-authoring surface, and
# the plugin hub. None of these may import a backend *plugin* distribution
# (sfmapi_colmap, sfmapi_hloc, ...) or the client SDK (sfmapi_client*).
# The dependency must always flow plugin -> core, never the reverse.
_CORE_PACKAGES = ("app", "sfmapi", "sfm_hub")

# Underscore-suffixed distributions are separate packages (plugins + SDK).
# The core's own package is bare ``sfmapi`` (no underscore), which is fine.
_PLUGIN_DIST_RE = re.compile(r"^sfmapi_\w+")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _core_python_files() -> list[Path]:
    files: list[Path] = []
    for pkg in _CORE_PACKAGES:
        root = ROOT / pkg
        if not root.is_dir():
            continue
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _imported_modules(tree: ast.AST) -> set[str]:
    """Top-level module names from real import statements only.

    AST-based on purpose: string templates that *contain* import-looking
    text (e.g. the plugin scaffolder's generated-code templates) are
    string literals, not Import/ImportFrom nodes, so they're correctly
    ignored.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".", 1)[0])
        # Only absolute imports carry a plugin-distribution name;
        # relative imports (level > 0) are within the core package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".", 1)[0])
    return names


def test_workflows_do_not_reference_removed_in_repo_clients() -> None:
    stale = (
        "clients" + "/python",
        "clients" + "/typescript",
        "clients" + "/cpp",
        "clients" + "/",
    )
    failures: list[str] = []
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = _read_text(path)
        for marker in stale:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT)} contains {marker!r}")

    assert not failures, "\n".join(failures)


def test_public_docs_use_public_runtime_entrypoint() -> None:
    stale = "app.main" + ":app"
    checked = [ROOT / "README.md", *Path(ROOT / "docs").rglob("*.md")]
    failures = [
        str(path.relative_to(ROOT))
        for path in checked
        if path.is_file() and stale in _read_text(path)
    ]

    assert not failures, "\n".join(failures)


# Amended services->adapters layering rule (lean audit 2026-07, 3.5):
# services MAY import the adapters *contract layer* -- the backend
# Protocols, the registry, and the three descriptor surfaces -- because
# those modules are pure contract/registry code (no engine imports).
# Everything else under ``app.adapters`` (stub backend, image adapter,
# ...) and any private leading-underscore symbol stays off-limits.
_SERVICES_ALLOWED_ADAPTER_MODULES = frozenset(
    {
        "backend",
        "registry",
        "backend_config",
        "backend_actions",
        "backend_artifacts",
    }
)


def _adapter_import_violations(tree: ast.AST) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:2] != ["app", "adapters"]:
                    continue
                if len(parts) < 3 or parts[2] not in _SERVICES_ALLOWED_ADAPTER_MODULES:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if parts[:2] != ["app", "adapters"]:
                continue
            if len(parts) == 2:
                # ``from app.adapters import <name>`` -- <name> must be an
                # allowed contract-layer submodule, never a symbol.
                violations.extend(
                    f"from app.adapters import {alias.name}"
                    for alias in node.names
                    if alias.name not in _SERVICES_ALLOWED_ADAPTER_MODULES
                )
            elif parts[2] not in _SERVICES_ALLOWED_ADAPTER_MODULES:
                violations.append(f"from {node.module} import ... (module not allowed)")
            else:
                violations.extend(
                    f"from {node.module} import {alias.name}"
                    for alias in node.names
                    if alias.name == "*" or alias.name.startswith("_")
                )
    return violations


def test_services_import_only_public_adapter_contract_surface() -> None:
    """Services may use the adapters contract layer, publicly and only it.

    CLAUDE.md's original rule was "services never import adapters"; six
    services legitimately need backend discovery/dispatch, so the rule is
    amended to allow the contract-layer modules listed in
    ``_SERVICES_ALLOWED_ADAPTER_MODULES`` -- public names only. Importing
    any other ``app.adapters`` module (or any ``_private`` symbol) from
    ``app/services`` fails here; either use the public seam or extend the
    contract layer deliberately.
    """
    failures: list[str] = []
    for path in sorted((ROOT / "app" / "services").glob("*.py")):
        tree = ast.parse(_read_text(path), filename=str(path))
        failures.extend(
            f"{path.relative_to(ROOT)}: {violation}"
            for violation in _adapter_import_violations(tree)
        )

    assert not failures, "\n".join(failures)


def test_core_does_not_import_plugin_distributions() -> None:
    """The framework core must not depend on any backend plugin (or the
    client SDK). Dependency direction is plugin -> core, never reverse.

    A core-defined standard *derived from* a backend family (e.g. the
    COLMAP stage-config table or the COLMAP reconstruction data format)
    is fine -- that's a shared contract the core owns, not a dependency
    on a plugin package. What this guards against is the core actually
    ``import``-ing an ``sfmapi_<plugin>`` distribution, which would
    invert the layering and make the API unusable without that plugin
    installed.
    """
    failures: list[str] = []
    for path in _core_python_files():
        try:
            tree = ast.parse(_read_text(path), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - core must parse
            failures.append(f"{path.relative_to(ROOT)}: unparseable ({exc})")
            continue
        for module in sorted(_imported_modules(tree)):
            if _PLUGIN_DIST_RE.match(module):
                failures.append(
                    f"{path.relative_to(ROOT)}: imports plugin/SDK distribution "
                    f"{module!r} (core must not depend on a plugin)"
                )

    assert not failures, "\n".join(failures)
