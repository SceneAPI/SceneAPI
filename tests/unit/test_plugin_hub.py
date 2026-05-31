from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.services import plugin_service, sfm_stage_service
from sfm_hub.discovery import discover_plugins, load_backend_entry_points
from sfm_hub.doctor import detect_external_tools, doctor_manifest
from sfm_hub.install import (
    build_container_service_install_plan,
    build_docker_install_plan,
    build_install_plan,
    parse_github_source,
)
from sfm_hub.models import ContainerServiceRuntime, PluginManifest, UvRuntime
from sfm_hub.provision import package_module_name
from sfm_hub.registry import get_manifest, list_manifests, search_manifests
from sfm_hub.routing import ProviderAmbiguityError, ensure_provider_enabled, resolve_provider
from sfm_hub.state import (
    RoutingProfile,
    load_state,
    record_manual_install,
    save_state,
    set_enabled,
    set_project_profile,
    upsert_profile,
)

pytestmark = pytest.mark.unit


def test_bundled_manifests_validate_and_include_initial_entries() -> None:
    manifests = list_manifests()
    plugin_ids = {manifest.plugin_id for manifest in manifests}

    assert {
        "colmap_cli",
        "pycolmap",
        "colmap_native",
        "realityscan_cli",
        "hloc",
        "instantsfm",
        "spheresfm",
        "gsplat",
        "brush",
        "lfs",
        "spirulae",
        "fastergs",
    } <= plugin_ids
    assert "gaussian_splatting_cuda" not in plugin_ids

    for manifest in manifests:
        assert manifest.github_url.startswith("https://github.com/SFMAPI/")
        assert manifest.entry_points
        assert manifest.providers
        assert manifest.runtime_mode_names()
        assert set(manifest.provider_ids()) == {
            provider.provider_id for provider in manifest.providers
        }


def test_torch_backed_plugins_declare_explicit_torch_runtime() -> None:
    expected_policy = {
        "fastergs": "required",
        "gsplat": "required",
        "hloc": "recommended",
        "instantsfm": "required",
        "spirulae": "required",
        "vismatch": "recommended",
    }
    for plugin_id, policy in expected_policy.items():
        torch_runtime = get_manifest(plugin_id).compatibility.torch

        assert torch_runtime is not None
        assert torch_runtime.policy == policy
        assert torch_runtime.device == "cuda"
        assert torch_runtime.cpu_index_url == "https://download.pytorch.org/whl/cpu"
        assert torch_runtime.install_env["TORCH_DEVICE"] == "cuda"
        if plugin_id == "gsplat":
            assert torch_runtime.index_url == "https://download.pytorch.org/whl/cu128"
            assert torch_runtime.packages == ["torch"]
            assert torch_runtime.install_env["GSPLAT_PACKAGE"] == "gsplat==1.5.3"
            assert torch_runtime.install_env["TORCH_CUDA_ARCH_LIST"] == "12.0"
        else:
            assert torch_runtime.index_url == "https://download.pytorch.org/whl/cu128"
            assert torch_runtime.packages == ["torch", "torchvision", "torchaudio"]
            assert torch_runtime.install_env["TORCH_PACKAGES"] == "torch torchvision torchaudio"

    instantsfm_runtime = get_manifest("instantsfm").runtime_modes.container_service
    assert instantsfm_runtime is not None
    assert instantsfm_runtime.execution.gpu == "required"
    assert instantsfm_runtime.image is not None
    assert instantsfm_runtime.image.build is not None
    assert instantsfm_runtime.image.build.args["TORCH_DEVICE"] == "cuda"


def test_3dgs_plugins_declare_container_service_radiance_contracts() -> None:
    expected = {
        "gsplat",
        "brush",
        "lfs",
        "spirulae",
        "fastergs",
    }

    for plugin_id in expected:
        manifest = get_manifest(plugin_id)
        runtime = manifest.runtime_modes.container_service

        assert runtime is not None
        assert manifest.runtime_modes.docker is not None
        assert "radiance.train" in manifest.capabilities
        assert "radiance.train" in manifest.providers[0].capabilities
        assert runtime.protocol == "sfmapi-plugin-http-v1"
        assert runtime.execution.path == "/execute"
        assert runtime.object_store is not None
        assert runtime.object_store.input_prefix == f"{plugin_id}/input/"
        assert runtime.object_store.output_prefix == f"{plugin_id}/output/"

    assert get_manifest("brush").compatibility.torch is None
    assert get_manifest("lfs").compatibility.torch is None
    assert get_manifest("gsplat").runtime_modes.container_service.execution.gpu == "required"
    assert get_manifest("spirulae").runtime_modes.container_service.execution.gpu == "required"
    assert get_manifest("fastergs").runtime_modes.container_service.execution.gpu == "required"


def test_package_module_name_strips_extras_for_provisioning() -> None:
    assert package_module_name("sfmapi-vismatch[engine]") == "sfmapi_vismatch"


def test_schema_file_lists_required_manifest_fields() -> None:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())

    assert "plugin_id" in schema["required"]
    assert "github_url" in schema["required"]
    assert "entry_points" in schema["required"]
    assert "providers" in schema["required"]
    assert "runtime_modes" in schema["required"]
    assert "container_service" in schema["properties"]["runtime_modes"]["properties"]


def _schema_errors(def_name: str, value: dict[str, object]) -> list[str]:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())
    validator = Draft202012Validator(schema["$defs"][def_name])
    return [error.message for error in validator.iter_errors(value)]


@pytest.mark.parametrize(
    ("model", "def_name", "value"),
    [
        (
            UvRuntime,
            "uv_runtime",
            {
                "source": "git",
                "url": "https://github.com/SFMAPI/sfmapi_hloc",
                "package": "sfmapi-hloc",
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://plugin-hloc:8080"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "SFMAPI_TEST_PLUGIN_URL"},
            },
        ),
    ],
)
def test_runtime_schema_and_pydantic_accept_same_valid_shapes(
    model: type[UvRuntime | ContainerServiceRuntime],
    def_name: str,
    value: dict[str, object],
) -> None:
    assert _schema_errors(def_name, value) == []
    model.model_validate(value)


@pytest.mark.parametrize(
    ("model", "def_name", "value"),
    [
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "plugin_url"},
            },
        ),
    ],
)
def test_runtime_schema_and_pydantic_reject_same_invalid_shapes(
    model: type[ContainerServiceRuntime],
    def_name: str,
    value: dict[str, object],
) -> None:
    assert _schema_errors(def_name, value)
    with pytest.raises(PydanticValidationError):
        model.model_validate(value)


def test_registry_search_and_github_install_plan() -> None:
    assert [manifest.plugin_id for manifest in search_manifests("hloc")] == ["hloc"]

    source = parse_github_source(
        "https://github.com/SFMAPI/sfmapi_colmap_cli/tree/v1.2.3",
        package="sfmapi-colmap-cli",
    )
    plan = build_install_plan(source)

    assert source.normalized_url == "https://github.com/SFMAPI/sfmapi_colmap_cli.git"
    assert source.ref == "v1.2.3"
    assert plan.command == [
        "uv",
        "pip",
        "install",
        "sfmapi-colmap-cli @ git+https://github.com/SFMAPI/sfmapi_colmap_cli.git@v1.2.3",
    ]
    assert not plan.warnings


def test_mutable_github_refs_warn() -> None:
    plan = build_install_plan(parse_github_source("SFMAPI/sfmapi_hloc"))

    assert plan.source.ref == "main"
    assert plan.warnings


def test_all_bundled_uv_plugins_plan_repo_install_and_provisioning() -> None:
    planned = []
    for manifest in list_manifests():
        if manifest.runtime_modes.uv is None:
            continue
        result = plugin_service.install_plugin(
            manifest.plugin_id,
            method="uv",
            dry_run=True,
            provision_runtime=True,
        )
        planned.append(manifest.plugin_id)

        assert result["installed"] is False
        assert result["command"][:3] == ["uv", "pip", "install"]
        assert result["direct_reference"].startswith(f"{manifest.package_name} @ git+")
        assert manifest.github_url in result["direct_reference"]
        assert result["provision_runtime"] is True
        assert result["provisioning"] is not None
        assert result["provisioning"]["steps"]

    assert planned


def test_docker_install_plan_reports_missing_image() -> None:
    source = parse_github_source("SFMAPI/sfmapi_colmap_cli", package="sfmapi-colmap-cli")
    plan = build_docker_install_plan(
        "colmap_cli", get_manifest("colmap_cli").runtime_modes.docker, source=source
    )

    assert plan.method == "docker"
    assert plan.warnings


def test_hloc_does_not_advertise_unimplemented_docker_runtime() -> None:
    manifest = get_manifest("hloc")
    source = parse_github_source(manifest.github_url, package=manifest.package_name)
    plan = build_docker_install_plan("hloc", manifest.runtime_modes.docker, source=source)

    assert manifest.runtime_modes.enabled_modes() == ["uv"]
    assert plan.method == "docker"
    assert plan.command == []
    assert plan.warnings == ["plugin 'hloc' does not define a docker runtime"]


def test_container_service_runtime_is_typed_and_plannable() -> None:
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://plugin-hloc:8080"},
            "healthcheck": {"path": "/healthz", "timeout_seconds": 5},
        }
    )
    source = parse_github_source("SFMAPI/sfmapi_hloc", package="sfmapi-hloc")

    plan = build_container_service_install_plan("hloc", runtime, source=source)

    assert plan.method == "container_service"
    assert plan.command == []
    assert plan.direct_reference == "container_service:http://plugin-hloc:8080"
    assert plan.warnings


def test_container_service_install_plan_provisions_declared_image() -> None:
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8098"},
            "image": {"image": "ghcr.io/sfmapi/hloc-plugin:1.0"},
        }
    )
    source = parse_github_source("SFMAPI/sfmapi_hloc", package="sfmapi-hloc")

    plan = build_container_service_install_plan("hloc", runtime, source=source)

    assert plan.method == "container_service"
    assert plan.direct_reference == "ghcr.io/sfmapi/hloc-plugin:1.0"
    assert plan.command[1:] == ["-m", "sfm_hub.container_runtime", "provision", "hloc"]


def test_container_service_runtime_rejects_malformed_endpoint() -> None:
    with pytest.raises(PydanticValidationError, match="default_url or url_env"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {},
            }
        )

    with pytest.raises(PydanticValidationError, match="must include a host"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://"},
            }
        )
    for bad_url in [
        "https://user:pass@plugin-hloc",
        "http://plugin-hloc/path#fragment",
        "http://plugin-hloc?token=secret",
        "http://bad host",
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceRuntime.model_validate(
                {
                    "protocol": "sfmapi-plugin-http-v1",
                    "protocol_version": "1.0",
                    "service": {"default_url": bad_url},
                }
            )

    with pytest.raises(PydanticValidationError, match="url_env must match"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "plugin_url"},
            }
        )


def test_container_service_doctor_reports_unconfigured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"url_env": "SFMAPI_TEST_PLUGIN_URL"},
        }
    )
    monkeypatch.delenv("SFMAPI_TEST_PLUGIN_URL", raising=False)

    report = doctor_manifest(manifest)
    check = next(item for item in report.checks if item.name == "container_service")

    assert check.status == "warn"
    assert "SFMAPI_TEST_PLUGIN_URL" in check.detail


def _start_container_service(
    responses: dict[str, tuple[int, bytes]],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response = responses.get(self.path)
            if response is None:
                self.send_response(404)
                self.end_headers()
                return
            status, body = response
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def test_container_service_doctor_checks_health_endpoint() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "pass"
        assert "sfmapi-plugin-http-v1 1.0" in check.detail
        assert check.metadata["protocol"] == "sfmapi-plugin-http-v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    ("responses", "reason"),
    [
        (
            {"/version": (200, b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}')},
            "http_error",
        ),
        ({"/healthz": (503, b'{"status":"down"}')}, "http_error"),
        ({"/healthz": (200, b'{"status":"ok"}')}, "version_http_error"),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (200, b"not-json"),
            },
            "bad_version_json",
        ),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (200, b'{"protocol":"other","protocol_version":"1.0"}'),
            },
            "protocol_mismatch",
        ),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (
                    200,
                    b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"2.0"}',
                ),
            },
            "protocol_version_mismatch",
        ),
    ],
)
def test_container_service_doctor_rejects_bad_protocol_health(
    responses: dict[str, tuple[int, bytes]],
    reason: str,
) -> None:
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "fail"
        assert check.metadata["reason"] == reason
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_plan_reports_missing_runtime() -> None:
    manifest = get_manifest("hloc")
    source = parse_github_source(manifest.github_url, package=manifest.package_name)
    result = plugin_service.install_plugin(
        "hloc",
        method="container_service",
        dry_run=True,
    )

    assert manifest.runtime_modes.container_service is None
    assert build_container_service_install_plan(
        "hloc",
        manifest.runtime_modes.container_service,
        source=source,
    ).warnings == ["plugin 'hloc' does not define a container_service runtime"]
    assert result["method"] == "container_service"
    assert result["command"] == []
    assert result["warnings"] == ["plugin 'hloc' does not define a container_service runtime"]


def test_container_service_install_rejects_missing_runtime_execution() -> None:
    with pytest.raises(ValidationError, match="does not define a container_service runtime"):
        plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
        )


def test_container_service_install_dry_run_does_not_contact_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:9"},
        }
    )
    monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

    result = plugin_service.install_plugin(
        "hloc",
        method="container_service",
        dry_run=True,
    )

    assert result["installed"] is False
    assert result["direct_reference"] == "container_service:http://127.0.0.1:9"


def test_container_service_install_requires_healthy_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"2.0"}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

        with pytest.raises(ValidationError, match="protocol version mismatch"):
            plugin_service.install_plugin(
                "hloc",
                method="container_service",
                dry_run=False,
                allow_unsafe_execution=True,
                request_id="550e8400-e29b-41d4-a716-446655440042",
            )

        record = load_state().installed["hloc"]
        assert record.method == "container_service"
        assert record.enabled is True
        assert record.provisioning_status == "failed"
        assert record.request_id == "550e8400-e29b-41d4-a716-446655440042"
        assert "protocol version mismatch" in (record.provisioning_error or "")

        report = doctor_manifest(manifest, state=load_state())
        provisioning = next(item for item in report.checks if item.name == "provisioning")
        assert provisioning.status == "fail"
        assert provisioning.metadata == {
            "provisioning_status": "failed",
            "request_id": "550e8400-e29b-41d4-a716-446655440042",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_records_after_healthy_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
    }
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440043",
        )
        record = load_state().installed["hloc"]

        assert result["installed"] is True
        assert result["request_id"] == "550e8400-e29b-41d4-a716-446655440043"
        assert record.method == "container_service"
        assert record.request_id == "550e8400-e29b-41d4-a716-446655440043"
        report = doctor_manifest(manifest, state=load_state())
        container_check = next(
            item for item in report.checks if item.name == "container_service"
        )
        loadable_check = next(item for item in report.checks if item.name == "loadable")
        assert report.status != "fail"
        assert container_check.status == "pass"
        assert loadable_check.status == "warn"
        assert loadable_check.metadata == {"installed_method": "container_service"}
        responses["/healthz"] = (503, b'{"status":"down"}')

        replay = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440043",
        )
        assert replay == result

        with pytest.raises(ValidationError, match="health check returned HTTP 503"):
            plugin_service.install_plugin(
                "hloc",
                method="container_service",
                dry_run=False,
                allow_unsafe_execution=True,
                request_id="550e8400-e29b-41d4-a716-446655440044",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_provisions_declared_image_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
    }
    calls: list[list[str]] = []
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
                "image": {
                    "build": {
                        "source": "local",
                        "context": "C:/plugins/hloc",
                        "dockerfile": "Dockerfile",
                    }
                },
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)
        monkeypatch.setattr(
            plugin_service,
            "run_install_command",
            lambda plan: calls.append(list(plan.command)),
        )

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440045",
        )
        record = load_state().installed["hloc"]

        assert calls == [result["command"]]
        assert result["provision_runtime"] is True
        assert result["provisioned"] is True
        assert result["provisioning_status"] == "succeeded"
        assert record.provision_runtime is True
        assert record.provisioned is True
        assert record.provisioning_status == "succeeded"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_can_attach_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
    }
    calls: list[list[str]] = []
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
                "image": {
                    "build": {
                        "source": "local",
                        "context": "C:/plugins/hloc",
                        "dockerfile": "Dockerfile",
                    }
                },
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)
        monkeypatch.setattr(
            plugin_service,
            "run_install_command",
            lambda plan: calls.append(list(plan.command)),
        )

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            provision_runtime=False,
        )

        assert calls == []
        assert result["installed"] is True
        assert result["provision_runtime"] is False
        assert result["provisioned"] is False
        assert result["provisioning_status"] == "not_requested"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_runtime_requests_gpu_for_required_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8098"},
            "image": {"image": "sfmapi-plugin-gsplat:test"},
            "execution": {
                "gpu": "required",
                "env": ["TORCH_DEVICE", "CUDA_VISIBLE_DEVICES"],
            },
        }
    )
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service("gsplat", "sfmapi-plugin-gsplat:test", runtime, 8098)

    run_command = calls[1]
    assert "--gpus" in run_command
    assert run_command[run_command.index("--gpus") + 1] == "all"
    assert "127.0.0.1:8098:8080" in run_command
    assert "-e" in run_command
    assert "TORCH_DEVICE=cuda" in run_command
    assert "CUDA_VISIBLE_DEVICES" in run_command


def test_container_runtime_does_not_inject_torch_device_for_non_torch_gpu_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8096"},
            "image": {"image": "sfmapi-plugin-brush:test"},
            "execution": {
                "gpu": "required",
                "env": ["WGPU_BACKEND", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"],
            },
        }
    )
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service("brush", "sfmapi-plugin-brush:test", runtime, 8096)

    run_command = calls[1]
    assert "--gpus" in run_command
    assert "127.0.0.1:8096:8080" in run_command
    assert "NVIDIA_VISIBLE_DEVICES=all" in run_command
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics" in run_command
    assert "TORCH_DEVICE=cuda" not in run_command


def test_container_runtime_allows_container_port_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8096"},
            "image": {"image": "sfmapi-plugin-brush:test"},
            "execution": {"gpu": "none"},
        }
    )
    monkeypatch.setenv("SFMAPI_PLUGIN_CONTAINER_PORT", "9090")
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service("brush", "sfmapi-plugin-brush:test", runtime, 8096)

    assert "127.0.0.1:8096:9090" in calls[1]


def test_docker_install_rejects_missing_runtime_execution() -> None:
    with pytest.raises(ValidationError, match="does not define a docker runtime"):
        plugin_service.install_plugin(
            "hloc",
            method="docker",
            dry_run=False,
            allow_unsafe_execution=True,
        )


def test_no_plugin_advertises_empty_docker_runtime() -> None:
    for manifest in list_manifests():
        runtime = manifest.runtime_modes.docker
        assert runtime is None or runtime.image or runtime.build_context, manifest.plugin_id


def test_provider_resolution_uses_profiles_and_rejects_ambiguity() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ProviderAmbiguityError):
        resolve_provider(stage="features", capability="features.extract.sift")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            requested_provider="colmap_pycolmap",
        )
        == "colmap_pycolmap"
    )

    upsert_profile(
        RoutingProfile(name="prefer-cli", routes={"features": ["colmap_cli"]}),
    )
    state = load_state()
    state.default_profile = "prefer-cli"
    save_state(state)

    assert resolve_provider(stage="features", capability="features.extract.sift") == "colmap_cli"


def test_provider_resolution_uses_provider_priority_fallback() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")
    state = load_state()
    state.provider_priority = ["colmap_pycolmap"]
    save_state(state)

    assert (
        resolve_provider(stage="features", capability="features.extract.sift") == "colmap_pycolmap"
    )


def test_disabled_provider_is_rejected_for_runtime_resolution() -> None:
    record_manual_install("hloc", method="entry_point", enabled=False)

    with pytest.raises(KeyError, match="disabled"):
        ensure_provider_enabled("hloc")

    ensure_provider_enabled("not_registered_in_hub")


def test_provider_resolution_uses_project_profile_before_default() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")
    upsert_profile(RoutingProfile(name="default", routes={"features": ["colmap_cli"]}))
    upsert_profile(RoutingProfile(name="project", routes={"features": ["colmap_pycolmap"]}))
    state = load_state()
    state.default_profile = "default"
    save_state(state)
    set_project_profile("project-1", "project")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            project_id="project-1",
        )
        == "colmap_pycolmap"
    )


def test_stage_validation_applies_provider_resolution() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    spec = {"type": "sift", "backend_options": {}}

    sfm_stage_service.validate_features_config(spec)

    assert spec["provider"] == "colmap_cli"


def test_stage_validation_reports_ambiguous_provider() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ValidationError, match="multiple candidate providers"):
        sfm_stage_service.validate_features_config({"type": "sift", "backend_options": {}})


def test_manifest_lookup_returns_expected_install_metadata() -> None:
    manifest = get_manifest("colmap_cli")

    assert manifest.runtime_modes.uv is not None
    assert manifest.runtime_modes.uv.package == "sfmapi-colmap-cli"
    assert "colmap_cli" in manifest.provider_ids()


def test_external_tool_manifests_use_runtime_env_vars() -> None:
    colmap_cli = get_manifest("colmap_cli")
    colmap_native = get_manifest("colmap_native")
    realityscan = get_manifest("realityscan_cli")
    spheresfm = get_manifest("spheresfm")

    assert colmap_cli.runtime_modes.external_tool is not None
    assert colmap_native.runtime_modes.external_tool is not None
    assert realityscan.runtime_modes.external_tool is not None
    assert spheresfm.runtime_modes.external_tool is not None
    assert "SFMAPI_COLMAP_EXECUTABLE" in colmap_cli.runtime_modes.external_tool.env_vars
    assert "SFMAPI_COLMAP_EXECUTABLE" in colmap_native.runtime_modes.external_tool.env_vars
    assert "SFMAPI_RC_EXECUTABLE" in realityscan.runtime_modes.external_tool.env_vars
    assert "SFMAPI_SPHERESFM_EXECUTABLE" in spheresfm.runtime_modes.external_tool.env_vars


def test_upstream_license_metadata_is_specific() -> None:
    upstream = {
        item.name: item.license
        for manifest in list_manifests(include_entry_points=False)
        for item in manifest.upstream_projects
    }

    assert upstream["COLMAP"] == "BSD-3-Clause"
    assert upstream["Hierarchical Localization"] == "Apache-2.0"
    assert upstream["InstantSfM"] == "CC-BY-NC-4.0"
    assert upstream["SphereSfM"] == "BSD-3-Clause"
    assert all(value != "Upstream license" for value in upstream.values())


def test_external_tool_detection_checks_env_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("colmap_cli").model_copy(deep=True)
    assert manifest.runtime_modes.external_tool is not None
    manifest.runtime_modes.external_tool.executable_names = []
    manifest.runtime_modes.external_tool.env_vars = ["TEST_TOOL_EXE"]
    manifest.runtime_modes.external_tool.version_args = ["--version"]
    monkeypatch.setenv("TEST_TOOL_EXE", sys.executable)

    tools = detect_external_tools([manifest])["colmap_cli"]

    assert tools[0].source == "env"
    assert tools[0].path == sys.executable
    assert tools[0].version


def test_entry_point_discovery_loads_plugin_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("hloc")

    class FakeEntryPoint:
        name = "hloc"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginManifest:
            return manifest

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    found = discover_plugins(load=True)

    assert found[0].plugin_id == "hloc"
    assert found[0].manifest == manifest


def test_entry_point_loader_registers_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        backend_name = "entry_backend"
        manifest = manifest_obj

        @staticmethod
        def backend_factory() -> object:
            return object()

    class FakeEntryPoint:
        name = "entry_backend"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    def register_backend(name: str, factory: object) -> None:
        registered[name] = factory

    def register_provider(provider_id: str, factory: object) -> None:
        providers[provider_id] = factory

    loaded = load_backend_entry_points(  # type: ignore[arg-type]
        register_backend,
        register_provider=register_provider,
    )

    assert loaded[0].plugin_id == "hloc"
    assert "entry_backend" in registered
    assert providers["hloc"] is registered["entry_backend"]


def test_entry_point_loader_skips_disabled_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_obj = get_manifest("hloc")
    record_manual_install("hloc", method="uv")
    set_enabled("hloc", False)

    class PluginObject:
        backend_name = "hloc"
        manifest = manifest_obj

        @staticmethod
        def backend_factory() -> object:
            return object()

    class FakeEntryPoint:
        name = "hloc"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    loaded = load_backend_entry_points(  # type: ignore[arg-type]
        registered.setdefault,
        register_provider=providers.setdefault,
    )

    assert loaded[0].plugin_id == "hloc"
    assert loaded[0].skipped is True
    assert loaded[0].load_error is None
    assert not registered
    assert not providers


def test_entry_point_loader_registrar_accepts_providers_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry-point plugins can declare provider aliases through the
    ``registrar`` callback rather than only through the manifest."""
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        manifest = manifest_obj

        @staticmethod
        def register(registrar) -> None:  # type: ignore[no-untyped-def]
            registrar(
                "explicit_backend",
                lambda: object(),
                providers=["explicit.provider"],
            )

    class FakeEntryPoint:
        name = "explicit_entry"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    load_backend_entry_points(  # type: ignore[arg-type]
        registered.setdefault,
        register_provider=providers.setdefault,
    )

    # Callback-declared provider wins; manifest providers (hloc) for the same
    # single backend still register via the manifest fallback path.
    assert "explicit.provider" in providers
    assert providers["explicit.provider"] is registered["explicit_backend"]
    assert "hloc" in providers


def test_entry_point_loader_logs_unmatched_manifest_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When a plugin registers >1 backend and the manifest lists provider
    ids that don't match any registered backend name, sfm_hub warns
    instead of silently dropping the alias."""
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        manifest = manifest_obj

        @staticmethod
        def register(registrar) -> None:  # type: ignore[no-untyped-def]
            registrar("alpha", lambda: object())
            registrar("beta", lambda: object())

    class FakeEntryPoint:
        name = "multi_entry"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    with caplog.at_level("WARNING", logger="sfm_hub.discovery"):
        load_backend_entry_points(  # type: ignore[arg-type]
            registered.setdefault,
            register_provider=providers.setdefault,
        )

    assert {"alpha", "beta"} <= registered.keys()
    # Manifest provider id "hloc" matches neither "alpha" nor "beta" so it
    # must NOT be silently aliased to one of them, and a warning must fire.
    assert "hloc" not in providers
    assert any("unmatched_manifest_provider" in str(record.msg) for record in caplog.records)


def test_plugin_service_enable_records_entry_point_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enable_plugin on a discovered-but-not-yet-installed entry-point
    plugin records a manual install instead of raising."""
    from app.services import plugin_service

    monkeypatch.setattr(
        "app.services.plugin_service.discovered_plugin_ids",
        lambda: {"hloc"},
    )

    detail = plugin_service.enable_plugin("hloc")

    state = load_state()
    assert "hloc" in state.installed
    assert state.installed["hloc"].method == "entry_point"
    assert state.installed["hloc"].enabled is True
    assert detail["enabled"] is True


def test_plugin_service_disable_records_entry_point_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric to enable: disable on a discovered-but-not-yet-installed
    entry-point plugin records the manual install (disabled)."""
    from app.services import plugin_service

    monkeypatch.setattr(
        "app.services.plugin_service.discovered_plugin_ids",
        lambda: {"hloc"},
    )

    plugin_service.disable_plugin("hloc")

    state = load_state()
    assert "hloc" in state.installed
    assert state.installed["hloc"].enabled is False


def test_plugin_service_runs_package_provisioner_after_uv_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    calls: list[str] = []

    def fake_uv_install(plan) -> None:  # type: ignore[no-untyped-def]
        calls.append("uv:" + plan.source.inferred_package)

    def fake_provisioner(
        package_name: str,
        *,
        dry_run: bool,
        force: bool,
    ) -> dict[str, object]:
        calls.append(f"provision:{package_name}:{dry_run}:{force}")
        return {
            "available": True,
            "provisioned": True,
            "steps": [{"name": "engine", "status": "done"}],
            "env": {"ENGINE": "ready"},
            "outputs": {"ENGINE_HOME": "C:/engine"},
            "warnings": [],
        }

    monkeypatch.setattr(plugin_service, "run_uv_install", fake_uv_install)
    monkeypatch.setattr(plugin_service, "run_package_provisioner", fake_provisioner)

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
    )

    assert result["installed"] is True
    assert result["provisioned"] is True
    assert result["provisioning"]["env_keys"] == ["ENGINE"]
    assert result["provisioning"]["redacted_env"] == {"ENGINE": "<redacted>"}
    assert result["provisioning"]["outputs"] == {"ENGINE_HOME": "C:/engine"}
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]


def test_plugin_service_redacts_secret_provisioner_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: {
            "available": True,
            "provisioned": True,
            "steps": [{"name": "token", "api_token": "secret-value"}],
            "env": {"SFMAPI_PLUGIN_TOKEN": "secret-value"},
            "outputs": {"PUBLIC_PATH": "C:/cache", "ACCESS_KEY": "secret-value"},
            "metadata": {"nested": {"password": "secret-value"}},
            "warnings": [],
        },
    )

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
    )
    serialized = json.dumps(result)

    assert "secret-value" not in serialized
    assert result["provisioning"]["env_keys"] == ["SFMAPI_PLUGIN_TOKEN"]
    assert result["provisioning"]["redacted_env"] == {"SFMAPI_PLUGIN_TOKEN": "<redacted>"}
    assert result["provisioning"]["outputs"]["ACCESS_KEY"] == "<redacted>"
    assert result["provisioning"]["steps"][0]["api_token"] == "<redacted>"


def test_plugin_service_records_failed_provisioning_and_dedupes_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service
    from sfm_hub.provision import ProvisioningError

    request_id = "123e4567-e89b-12d3-a456-426614174000"
    calls: list[str] = []

    def fake_uv_install(plan) -> None:  # type: ignore[no-untyped-def]
        calls.append("uv:" + plan.source.inferred_package)

    def fake_provisioner(package_name: str, *, dry_run: bool, force: bool) -> None:
        calls.append(f"provision:{package_name}:{dry_run}:{force}")
        raise ProvisioningError("download failed")

    monkeypatch.setattr(plugin_service, "run_uv_install", fake_uv_install)
    monkeypatch.setattr(plugin_service, "run_package_provisioner", fake_provisioner)

    with pytest.raises(ValidationError, match="download failed"):
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )
    state = load_state()
    record = state.installed["local_test"]

    assert record.provisioning_status == "failed"
    assert record.provisioning_error == "download failed"
    assert record.request_id == request_id
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]

    with pytest.raises(ValidationError, match=r"previous attempt|download failed"):
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )

    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]


def test_plugin_service_dedupes_successful_install_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    request_id = "123e4567-e89b-12d3-a456-426614174000"
    calls: list[str] = []

    monkeypatch.setattr(
        plugin_service,
        "run_uv_install",
        lambda plan: calls.append("uv:" + plan.source.inferred_package),
    )
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: (
            calls.append(f"provision:{package_name}")
            or {
                "available": True,
                "provisioned": True,
                "steps": [{"name": "engine", "status": "done"}],
                "env": {},
                "warnings": [],
            }
        ),
    )

    first = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        request_id=request_id,
    )
    second = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        request_id=request_id,
    )

    assert first["provisioning_status"] == "succeeded"
    assert second["provisioning_status"] == "succeeded"
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom"]


def test_plugin_service_reruns_install_with_different_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    calls: list[str] = []

    monkeypatch.setattr(
        plugin_service,
        "run_uv_install",
        lambda plan: calls.append("uv:" + plan.source.inferred_package),
    )
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: (
            calls.append(f"provision:{package_name}")
            or {
                "available": True,
                "provisioned": True,
                "steps": [{"name": "engine", "status": "done"}],
                "env": {},
                "warnings": [],
            }
        ),
    )

    for request_id in [
        "123e4567-e89b-12d3-a456-426614174000",
        "123e4567-e89b-12d3-a456-426614174001",
    ]:
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )

    assert calls == [
        "uv:sfmapi-custom",
        "provision:sfmapi-custom",
        "uv:sfmapi-custom",
        "provision:sfmapi-custom",
    ]


def test_plugin_service_no_provision_runtime_records_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        provision_runtime=False,
    )
    record = load_state().installed["local_test"]

    assert result["provisioning_status"] == "not_requested"
    assert record.provisioning_status == "not_requested"
