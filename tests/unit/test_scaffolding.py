"""Tests for the ``sfmapi scaffold-plugin`` CLI subcommand and the
``app.scaffolding`` module that powers it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.scaffolding import (
    ScaffoldedFile,
    _to_class_name,
    scaffold_plugin,
    validate_plugin_id,
)

# ---- naming validation -----------------------------------------------------


@pytest.mark.parametrize(
    "plugin_id",
    ["a", "a1", "my_engine", "x_2_y", "longidwith_underscores"],
)
def test_validate_plugin_id_accepts_canonical_shape(plugin_id: str) -> None:
    validate_plugin_id(plugin_id)  # does not raise


@pytest.mark.parametrize(
    "plugin_id",
    [
        "1nodigitstart",  # leading digit
        "Capitals",  # uppercase rejected
        "with-hyphen",  # hyphens rejected (module suffix can't have them)
        "with.dot",  # dots rejected
        "trailing_",  # trailing underscore is fine on the regex, but
        # this row exists to document the regex shape;
        # update if behavior changes (it currently passes).
    ],
)
def test_validate_plugin_id_rejects_bad_shapes(plugin_id: str) -> None:
    if plugin_id == "trailing_":
        # Document: trailing underscore is currently accepted by the
        # regex. If that's tightened, flip this branch.
        validate_plugin_id(plugin_id)
        return
    with pytest.raises(ValueError, match="plugin_id must match"):
        validate_plugin_id(plugin_id)


def test_to_class_name_handles_underscored_ids() -> None:
    assert _to_class_name("colmap") == "Colmap"
    assert _to_class_name("my_engine") == "MyEngine"
    assert _to_class_name("a_b_c") == "ABC"


# ---- scaffold_plugin (module-level) ----------------------------------------


def test_scaffold_plugin_writes_expected_files(tmp_path: Path) -> None:
    files = scaffold_plugin("demo", output_dir=tmp_path)
    root = tmp_path / "sfmapi_demo"
    expected = {
        root / "pyproject.toml",
        root / "README.md",
        root / "src" / "sfmapi_demo" / "__init__.py",
        root / "src" / "sfmapi_demo" / "plugin.py",
        root / "src" / "sfmapi_demo" / "backend.py",
        root / "tests" / "__init__.py",
        root / "tests" / "test_plugin.py",
    }
    written_paths = {f.path for f in files}
    assert written_paths == expected
    for f in files:
        assert isinstance(f, ScaffoldedFile)
        assert f.path.exists()
        assert f.bytes_written == f.path.stat().st_size


def test_pyproject_has_entry_point_for_plugin_id(tmp_path: Path) -> None:
    scaffold_plugin("demo", output_dir=tmp_path)
    text = (tmp_path / "sfmapi_demo" / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.entry-points."sfmapi.backends"]' in text
    assert 'demo = "sfmapi_demo.plugin:plugin"' in text


def test_plugin_py_uses_canonical_plugin_class(tmp_path: Path) -> None:
    scaffold_plugin("demo", output_dir=tmp_path)
    text = (tmp_path / "sfmapi_demo" / "src" / "sfmapi_demo" / "plugin.py").read_text(
        encoding="utf-8"
    )
    assert "from sfmapi.backends import Plugin" in text
    assert "plugin = Plugin(" in text


def test_scaffolded_manifest_passes_pluginmanifest_validation(tmp_path: Path) -> None:
    """The scaffolded MANIFEST dict must validate against the live
    PluginManifest model — if it doesn't, a fresh `sfmapi check-backend`
    on the new plugin would fail straight out of the gate.
    """
    scaffold_plugin("demo", output_dir=tmp_path)
    # Import the scaffolded plugin module via an isolated import
    src_root = tmp_path / "sfmapi_demo" / "src"
    sys.path.insert(0, str(src_root))
    try:
        # Reload-safe: ensure we don't pick up a stale module.
        import importlib

        for mod in [m for m in list(sys.modules) if m.startswith("sfmapi_demo")]:
            del sys.modules[mod]
        plugin_mod = importlib.import_module("sfmapi_demo.plugin")
        manifest = plugin_mod.plugin.get_plugin_manifest()
    finally:
        sys.path.remove(str(src_root))
    from sfm_hub.models import PluginManifest

    PluginManifest.model_validate(manifest)


def test_scaffold_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    scaffold_plugin("demo", output_dir=tmp_path)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        scaffold_plugin("demo", output_dir=tmp_path)


def test_scaffold_overwrite_flag_replaces_files(tmp_path: Path) -> None:
    scaffold_plugin("demo", output_dir=tmp_path, description="first")
    second = scaffold_plugin("demo", output_dir=tmp_path, description="second", overwrite=True)
    pyproject = (tmp_path / "sfmapi_demo" / "pyproject.toml").read_text(encoding="utf-8")
    assert "second" in pyproject
    assert "first" not in pyproject
    assert len(second) == 7  # all 7 files rewritten


def test_scaffold_rejects_bad_plugin_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plugin_id must match"):
        scaffold_plugin("BadName", output_dir=tmp_path)


# ---- CLI ------------------------------------------------------------------


def test_cli_scaffold_plugin_creates_files(tmp_path: Path) -> None:
    """End-to-end sanity: invoke `python -m app.cli scaffold-plugin <id>`
    and verify it produces the expected tree. Catches argparse wiring,
    path-resolution, and import bugs the unit-level test would miss.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "scaffold-plugin",
            "clitest",
            "--output-dir",
            str(tmp_path),
            "--display-name",
            "CLI Test",
            "--description",
            "Verifies the cli wiring.",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "scaffolded 7 files" in completed.stdout
    assert (tmp_path / "sfmapi_clitest" / "pyproject.toml").exists()
    assert (tmp_path / "sfmapi_clitest" / "src" / "sfmapi_clitest" / "plugin.py").exists()
    # The description we passed should be in the README.
    readme = (tmp_path / "sfmapi_clitest" / "README.md").read_text(encoding="utf-8")
    assert "Verifies the cli wiring." in readme


# ---- scaffold_contract -----------------------------------------------------


def test_validate_contract_name_accepts_and_rejects() -> None:
    from app.scaffolding import validate_contract_name

    validate_contract_name("colmap_db")
    validate_contract_name("a1")
    for bad in ("BadName", "1leading", "with-dash", "with.dot"):
        with pytest.raises(ValueError, match="contract name must match"):
            validate_contract_name(bad)


def _load_module(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec
    assert spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scaffold_contract_writes_module_and_test(tmp_path: Path) -> None:
    from app.scaffolding import scaffold_contract

    core = tmp_path / "core"
    tests = tmp_path / "tests"
    written = scaffold_contract("demo_std", core_dir=core, tests_dir=tests)
    paths = {f.path for f in written}
    assert paths == {core / "demo_std.py", tests / "test_demo_std_contract.py"}
    for f in written:
        assert f.path.exists()
        assert f.bytes_written == f.path.stat().st_size


def test_scaffolded_contract_module_satisfies_the_protocol(tmp_path: Path) -> None:

    from app.scaffolding import scaffold_contract

    scaffold_contract("demo_std", core_dir=tmp_path, tests_dir=tmp_path)
    mod = _load_module(tmp_path / "demo_std.py", "demo_std")
    # The contract-coverage gate keys off exactly these two symbols.
    assert mod.CONTRACT_NAME == "demo_std"
    assert callable(mod.contract_dict)
    payload = mod.contract_dict()
    assert json.loads(json.dumps(payload)) == payload  # JSON round-trips
    assert payload["contract"] == "demo_std"


def test_scaffolded_contract_test_is_valid_python(tmp_path: Path) -> None:
    from app.scaffolding import scaffold_contract

    scaffold_contract("demo_std", core_dir=tmp_path, tests_dir=tmp_path)
    src = (tmp_path / "test_demo_std_contract.py").read_text(encoding="utf-8")
    compile(src, "test_demo_std_contract.py", "exec")  # parses/compiles


def test_scaffold_contract_overwrite_semantics(tmp_path: Path) -> None:
    from app.scaffolding import scaffold_contract

    scaffold_contract("demo_std", core_dir=tmp_path, tests_dir=tmp_path)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        scaffold_contract("demo_std", core_dir=tmp_path, tests_dir=tmp_path)
    # overwrite=True succeeds
    again = scaffold_contract("demo_std", core_dir=tmp_path, tests_dir=tmp_path, overwrite=True)
    assert len(again) == 2
