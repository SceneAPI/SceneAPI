from __future__ import annotations

import pytest

from app.cli import main
from bench.cli import main as bench_main
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


def test_cli_profiles_and_providers(capsys: pytest.CaptureFixture[str]) -> None:
    record_manual_install("colmap_cli", method="external_tool")

    main(["profiles", "create", "hybrid", "--route", "features=colmap_cli"])
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
