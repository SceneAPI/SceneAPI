from __future__ import annotations

import pytest

from bench.cli import main as bench_main
from sceneapi.server.cli import main
from sfm_hub.state import record_manual_install

pytestmark = pytest.mark.unit


def test_cli_lists_and_searches_plugins(capsys: pytest.CaptureFixture[str]) -> None:
    main(["plugins", "search", "hloc"])

    out = capsys.readouterr().out
    assert "hloc" in out
    assert "available" in out


def test_cli_install_from_github_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "plugins",
            "install",
            "local_test",
            "--github",
            "https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            "--package",
            "sfmapi-custom",
            "--dry-run",
        ]
    )

    out = capsys.readouterr().out
    assert "uv" in out
    assert "sfmapi-custom @ git+https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0" in out


def test_cli_container_service_install_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    main(["plugins", "install", "hloc", "--method", "container_service", "--dry-run"])

    out = capsys.readouterr().out
    assert "container_service:hloc" in out
    assert "does not define a container_service runtime" in out


def test_cli_install_redacts_provisioning_env(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sceneapi.server.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: {
            "available": True,
            "provisioned": True,
            "steps": [{"name": "secret", "api_key": "secret-value"}],
            "env": {"SFMAPI_PLUGIN_TOKEN": "secret-value"},
            "warnings": [],
        },
    )

    main(
        [
            "plugins",
            "install",
            "local_test",
            "--github",
            "https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            "--package",
            "sfmapi-custom",
        ]
    )

    out = capsys.readouterr().out
    assert "secret-value" not in out
    assert "SFMAPI_PLUGIN_TOKEN" in out
    assert "<redacted>" in out


def test_cli_profiles_and_providers(capsys: pytest.CaptureFixture[str]) -> None:
    record_manual_install("colmap_cli", method="external_tool")

    main(["profiles", "create", "hybrid", "--route", "features=colmap_cli@colmap_cli"])
    main(["profiles", "set-default", "hybrid"])
    main(["profiles", "assign-project", "project-1", "hybrid"])
    main(["plugins", "entry-points"])
    main(["providers", "list"])

    out = capsys.readouterr().out
    assert "hybrid" in out
    assert "project-1" in out
    assert "colmap_cli" in out


def test_bench_validates_plugin_registry(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench_main(["plugins"]) == 0

    out = capsys.readouterr().out
    assert "colmap_cli" in out
    assert "spheresfm" in out
