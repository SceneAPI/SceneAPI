"""Local diagnostic checks for plugin registry entries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from sceneapi.server.core.public_outputs import sanitize_public_error_message
from sfm_hub.models import PluginManifest, _public_url_issue
from sfm_hub.state import PluginState, load_state


class DoctorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    metadata: dict[str, str] = Field(default_factory=dict)


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    status: Literal["pass", "warn", "fail"]
    checks: list[DoctorCheck] = Field(default_factory=list)


class ToolDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["env", "path"]
    path: str | None = None
    version: str | None = None
    error: str | None = None


def _version_for(path: str, args: list[str]) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            [path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # pragma: no cover - defensive around local tools
        return None, sanitize_public_error_message(f"{type(exc).__name__}: {exc}")
    text = sanitize_public_error_message((result.stdout or result.stderr).strip()[:500])
    if result.returncode != 0:
        return text or None, f"exit {result.returncode}"
    return text or None, None


def _public_tool_path(path: str) -> str:
    return os.path.basename(path) or "tool"


def detect_external_tools(manifests: list[PluginManifest]) -> dict[str, list[ToolDetection]]:
    out: dict[str, list[ToolDetection]] = {}
    for manifest in manifests:
        external = manifest.runtime_modes.external_tool
        if external is None:
            continue
        detections: list[ToolDetection] = []
        seen_paths: set[str] = set()
        for env_var in external.env_vars:
            value = os.environ.get(env_var)
            if not value:
                detections.append(ToolDetection(name=env_var, source="env"))
                continue
            version, error = _version_for(value, external.version_args)
            seen_paths.add(value)
            detections.append(
                ToolDetection(
                    name=env_var,
                    source="env",
                    path=_public_tool_path(value),
                    version=version,
                    error=error,
                )
            )
        for executable in external.executable_names:
            path = shutil.which(executable)
            if path is None:
                detections.append(ToolDetection(name=executable, source="path"))
                continue
            if path in seen_paths:
                continue
            version, error = _version_for(path, external.version_args)
            detections.append(
                ToolDetection(
                    name=executable,
                    source="path",
                    path=_public_tool_path(path),
                    version=version,
                    error=error,
                )
            )
        out[manifest.plugin_id] = detections
    return out


def _probe_uv_plugin(manifest: PluginManifest) -> DoctorCheck:
    """Is a uv-installed plugin actually importable in this environment?

    A discoverable ``sceneapi.backends`` entry point is the real signal that
    the package is installed and loadable — far more than "the manifest
    parsed". Imported lazily to avoid a load-time hub-internal cycle.
    """
    from sfm_hub.discovery import discovered_plugin_ids

    if manifest.plugin_id in discovered_plugin_ids():
        return DoctorCheck(
            name="loadable",
            status="pass",
            detail=f"{manifest.plugin_id} entry point is discoverable",
        )
    return DoctorCheck(
        name="loadable",
        status="fail",
        detail=(
            f"{manifest.plugin_id} declares a uv runtime but no sceneapi.backends "
            "entry point is discoverable — is the package installed here?"
        ),
    )


def _probe_docker_plugin(manifest: PluginManifest) -> DoctorCheck:
    """Verify the plugin's docker image is present (graceful if no docker)."""
    docker = shutil.which("docker")
    docker_runtime = manifest.runtime_modes.docker
    image = docker_runtime.image if docker_runtime is not None else None
    if docker is None:
        return DoctorCheck(
            name="docker_image",
            status="warn",
            detail="docker not on PATH; cannot verify the plugin image",
        )
    if not image:
        return DoctorCheck(
            name="docker_image",
            status="warn",
            detail="manifest docker runtime declares no image to inspect",
        )
    _out, err = _version_for(docker, ["image", "inspect", image])
    if err:
        return DoctorCheck(
            name="docker_image",
            status="fail",
            detail=f"docker image {image!r} not available: {err}",
        )
    return DoctorCheck(
        name="docker_image",
        status="pass",
        detail=f"docker image {image!r} is present",
    )


def _resolve_container_service_url(
    manifest: PluginManifest,
) -> tuple[str | None, str | None, str | None]:
    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        return None, None, None
    if runtime.service.url_env:
        value = os.environ.get(runtime.service.url_env)
        if value:
            return value, runtime.service.url_env, runtime.service.url_env
    return runtime.service.default_url, None, runtime.service.url_env


def _container_service_url_issue(base_url: str) -> str | None:
    issue = _public_url_issue(base_url, allowed_schemes={"http"})
    if issue is None:
        return None
    if "credentials" in issue:
        return "credentialed_endpoint"
    if "query" in issue or "fragment" in issue or "signed" in issue:
        return "signed_endpoint"
    return "invalid_endpoint"


def _probe_container_service_plugin(manifest: PluginManifest) -> DoctorCheck:
    """Verify a container service endpoint is configured and reachable."""

    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        return DoctorCheck(
            name="container_service",
            status="warn",
            detail="manifest does not declare a container_service runtime",
        )

    base_url, source_env_var, configured_env_var = _resolve_container_service_url(manifest)
    if not base_url:
        suffix = f"; set {configured_env_var}" if configured_env_var else ""
        return DoctorCheck(
            name="container_service",
            status="warn",
            detail=f"container service endpoint is not configured{suffix}",
        )
    url_issue = _container_service_url_issue(base_url)
    if url_issue is not None:
        source = f" from {source_env_var}" if source_env_var else ""
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service endpoint{source} is invalid",
            metadata={"reason": url_issue},
        )

    health_path = runtime.healthcheck.path
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    health_url = f"{base_url.rstrip('/')}{health_path}"
    version_url = f"{base_url.rstrip('/')}/version"
    status, error = _http_status(health_url, timeout=runtime.healthcheck.timeout_seconds)
    if error is not None:
        return error
    assert status is not None

    if not 200 <= status < 300:
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check returned HTTP {status}",
            metadata={"reason": "bad_health_status", "status_code": str(status)},
        )

    version_body, error = _http_json(version_url, timeout=runtime.healthcheck.timeout_seconds)
    if error is not None:
        return error
    if not isinstance(version_body, Mapping):
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail="container service version response must be a JSON object",
            metadata={"reason": "bad_version_shape"},
        )

    actual_protocol = str(version_body.get("protocol", ""))
    actual_version = str(version_body.get("protocol_version", ""))
    if actual_protocol != runtime.protocol:
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail=(
                "container service protocol mismatch: "
                f"expected {runtime.protocol}, got {actual_protocol or '<missing>'}"
            ),
            metadata={
                "reason": "protocol_mismatch",
                "expected_protocol": runtime.protocol,
                "actual_protocol": actual_protocol,
            },
        )
    if not _compatible_protocol_version(runtime.protocol_version, actual_version):
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail=(
                "container service protocol version mismatch: "
                f"expected compatible with {runtime.protocol_version}, got {actual_version or '<missing>'}"
            ),
            metadata={
                "reason": "protocol_version_mismatch",
                "expected_protocol_version": runtime.protocol_version,
                "actual_protocol_version": actual_version,
            },
        )

    catalog_metadata, catalog_error = _probe_container_catalog(
        manifest,
        base_url=base_url,
        timeout=runtime.healthcheck.timeout_seconds,
        actual_protocol_version=actual_version,
    )
    if catalog_error is not None:
        return catalog_error

    version_detail = (
        f" for {actual_protocol} {actual_version}" if actual_protocol and actual_version else ""
    )
    metadata = {
        "protocol": actual_protocol,
        "protocol_version": actual_version,
        "version_path": "/version",
    }
    if catalog_metadata:
        metadata.update(catalog_metadata)
    catalog_detail = (
        "legacy extension catalog is absent"
        if metadata.get("catalog") == "absent"
        else "extension catalog is valid"
    )
    return DoctorCheck(
        name="container_service",
        status="pass",
        detail=(
            f"{manifest.plugin_id} service is healthy at configured endpoint{version_detail}; "
            f"{catalog_detail}"
        ),
        metadata=metadata,
    )


def _catalog_capabilities(manifest: PluginManifest) -> list[str]:
    capabilities = set(manifest.capabilities)
    for provider in manifest.providers:
        capabilities.update(provider.capabilities)
    return sorted(capabilities)


def _catalog_endpoint_error(
    *,
    endpoint: str,
    detail: str,
    reason: str,
    **metadata: str,
) -> DoctorCheck:
    return DoctorCheck(
        name="container_service",
        status="fail",
        detail=f"container service {endpoint} catalog check failed: {detail}",
        metadata={"reason": reason, "endpoint": endpoint, **metadata},
    )


def _probe_container_catalog(
    manifest: PluginManifest,
    *,
    base_url: str,
    timeout: int,
    actual_protocol_version: str,
) -> tuple[dict[str, str] | None, DoctorCheck | None]:
    endpoint_arrays = {
        "datatypes": ("datatypes",),
        "processors": ("processors", "processor_extensions"),
        "pipelines": ("pipelines",),
    }
    payloads: dict[str, Mapping[str, object]] = {}
    missing_endpoints: list[str] = []
    for endpoint in ("datatypes", "processors", "pipelines"):
        url = f"{base_url.rstrip('/')}/{endpoint}"
        body, error = _http_json(url, timeout=timeout)
        if error is not None:
            status_code = error.metadata.get("status_code")
            if status_code == "404":
                missing_endpoints.append(endpoint)
                continue
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail=error.detail,
                reason=error.metadata.get("reason", "catalog_http_error"),
            )
        if not isinstance(body, Mapping):
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail=f"{endpoint} catalog endpoint returned a non-object JSON value",
                reason="bad_catalog_shape",
            )
        if body.get("schema_version") != 1:
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail="schema_version must be 1",
                reason="bad_catalog_schema_version",
                schema_version=str(body.get("schema_version")),
            )
        if body.get("plugin_id") != manifest.plugin_id:
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail=(f"plugin_id must be {manifest.plugin_id!r}, got {body.get('plugin_id')!r}"),
                reason="catalog_plugin_id_mismatch",
            )
        expected_keys = {"schema_version", "plugin_id", *endpoint_arrays[endpoint]}
        missing = sorted(expected_keys - set(body))
        if missing:
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail="missing required field(s): " + ", ".join(missing),
                reason="bad_catalog_shape",
            )
        unexpected = sorted(set(body) - expected_keys)
        if unexpected:
            return None, _catalog_endpoint_error(
                endpoint=endpoint,
                detail="unexpected field(s): " + ", ".join(unexpected),
                reason="bad_catalog_shape",
            )
        for array_name in endpoint_arrays[endpoint]:
            if not isinstance(body.get(array_name), list):
                return None, _catalog_endpoint_error(
                    endpoint=endpoint,
                    detail=f"{array_name} must be a list",
                    reason="bad_catalog_shape",
                )
        payloads[endpoint] = body
    if missing_endpoints:
        if len(missing_endpoints) == len(endpoint_arrays):
            try:
                version_parts = tuple(int(part) for part in actual_protocol_version.split(".")[:2])
            except ValueError:
                version_parts = (0, 0)
            if version_parts < (1, 1):
                return {
                    "catalog": "absent",
                    "catalog_reason": "legacy protocol 1.x service",
                }, None
        return None, _catalog_endpoint_error(
            endpoint="combined",
            detail="missing catalog endpoint(s): " + ", ".join(missing_endpoints),
            reason="missing_catalog_endpoint",
        )
    try:
        from sfm_hub.models import PluginBackendCatalog

        catalog = PluginBackendCatalog.model_validate(
            {
                "schema_version": 1,
                "plugin_id": manifest.plugin_id,
                "capabilities": _catalog_capabilities(manifest),
                "datatypes": payloads["datatypes"].get("datatypes", []),
                "processors": payloads["processors"].get("processors", []),
                "processor_extensions": payloads["processors"].get(
                    "processor_extensions",
                    [],
                ),
                "pipelines": payloads["pipelines"].get("pipelines", []),
            }
        )
    except PydanticValidationError as exc:
        return None, _catalog_endpoint_error(
            endpoint="combined",
            detail=str(exc.errors()[0].get("msg", exc))[:500],
            reason="catalog_validation_error",
        )
    return {
        "catalog_schema_version": str(catalog.schema_version),
        "catalog_datatypes": str(len(catalog.datatypes)),
        "catalog_processors": str(len(catalog.processors)),
        "catalog_processor_extensions": str(len(catalog.processor_extensions)),
        "catalog_pipelines": str(len(catalog.pipelines)),
    }, None


def _inactive_runtime_check(name: str, *, installed_method: str) -> DoctorCheck:
    return DoctorCheck(
        name=name,
        status="warn",
        detail=f"runtime is not active for this install; installed method is {installed_method}",
        metadata={"installed_method": installed_method},
    )


def _runtime_is_active(runtime: str, installed_method: str | None) -> bool:
    if installed_method is None:
        return True
    package_methods = {"uv", "entry_point"}
    if runtime == "uv":
        return installed_method in package_methods
    if runtime == "external_tool":
        return installed_method in {*package_methods, "external_tool"}
    return installed_method == runtime


def _http_status(url: str, *, timeout: int) -> tuple[int | None, DoctorCheck | None]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, None
    except HTTPError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check returned HTTP {exc.code}",
            metadata={"reason": "http_error", "status_code": str(exc.code)},
        )
    except URLError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check failed for configured endpoint: {exc}",
            metadata={"reason": "connection_error"},
        )
    except OSError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check failed for configured endpoint: {exc}",
            metadata={"reason": "connection_error"},
        )


def _http_json(url: str, *, timeout: int) -> tuple[object | None, DoctorCheck | None]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(1_000_000)
            status = response.status
    except HTTPError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check returned HTTP {exc.code}",
            metadata={"reason": "version_http_error", "status_code": str(exc.code)},
        )
    except URLError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check failed for configured endpoint: {exc}",
            metadata={"reason": "version_connection_error"},
        )
    except OSError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check failed for configured endpoint: {exc}",
            metadata={"reason": "version_connection_error"},
        )
    if not 200 <= status < 300:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check returned HTTP {status}",
            metadata={"reason": "bad_version_status", "status_code": str(status)},
        )
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail="container service version response is not valid JSON",
            metadata={"reason": "bad_version_json"},
        )


def _compatible_protocol_version(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    expected_major = expected.split(".", 1)[0]
    actual_major = actual.split(".", 1)[0]
    return expected_major == actual_major


def doctor_manifest(manifest: PluginManifest, *, state: PluginState | None = None) -> DoctorReport:
    state = state or load_state()
    checks: list[DoctorCheck] = []
    installed = state.installed.get(manifest.plugin_id)
    # The manifest reached us via ``get_manifest`` -> pydantic validation,
    # so structural validity is genuine — but that's all this check asserts.
    checks.append(
        DoctorCheck(
            name="manifest",
            status="pass",
            detail=f"{manifest.plugin_id} manifest schema is valid",
        )
    )
    checks.append(
        DoctorCheck(
            name="installation",
            status="pass" if installed is not None else "warn",
            detail="plugin is recorded as installed" if installed else "plugin is not installed",
        )
    )
    if installed is not None:
        checks.append(
            DoctorCheck(
                name="enabled",
                status="pass" if installed.enabled else "warn",
                detail="plugin is enabled" if installed.enabled else "plugin is disabled",
            )
        )
        # Real runtime probes — one per declared runtime mode. These are the
        # checks that can actually return ``fail``.
        if installed.provisioning_status == "failed":
            metadata = {"provisioning_status": installed.provisioning_status}
            if installed.request_id:
                metadata["request_id"] = installed.request_id
            checks.append(
                DoctorCheck(
                    name="provisioning",
                    status="fail",
                    detail=sanitize_public_error_message(installed.provisioning_error)
                    or "plugin install/provisioning failed",
                    metadata=metadata,
                )
            )
    installed_method = installed.method if installed is not None else None
    if manifest.runtime_modes.uv is not None:
        if not _runtime_is_active("uv", installed_method):
            checks.append(_inactive_runtime_check("loadable", installed_method=installed_method))
        else:
            checks.append(_probe_uv_plugin(manifest))
    if manifest.runtime_modes.docker is not None:
        if not _runtime_is_active("docker", installed_method):
            checks.append(
                _inactive_runtime_check("docker_image", installed_method=installed_method)
            )
        else:
            checks.append(_probe_docker_plugin(manifest))
    if manifest.runtime_modes.container_service is not None:
        if not _runtime_is_active("container_service", installed_method):
            checks.append(
                _inactive_runtime_check(
                    "container_service",
                    installed_method=installed_method,
                )
            )
        else:
            checks.append(_probe_container_service_plugin(manifest))
    external = manifest.runtime_modes.external_tool
    if external is not None and not _runtime_is_active("external_tool", installed_method):
        checks.append(_inactive_runtime_check("external_tool", installed_method=installed_method))
    elif external is not None:
        found = detect_external_tools([manifest]).get(manifest.plugin_id, [])
        usable = [item for item in found if item.path and not item.error]
        found_paths = [item for item in found if item.path]
        checks.append(
            DoctorCheck(
                name="external_tool",
                status="pass" if usable else "warn",
                detail="; ".join(
                    f"{item.name}={item.path or 'missing'}"
                    + (f" version={item.version}" if item.version else "")
                    + (f" error={item.error}" if item.error else "")
                    for item in found_paths or found
                ),
            )
        )
    # Surface manifest metadata that nothing else acts on, so ``doctor`` is
    # the one place an operator sees conformance + license posture.
    _conformance_status: dict[str, Literal["pass", "warn", "fail"]] = {
        "passing": "pass",
        "failing": "fail",
        "partial": "warn",
        "not_run": "warn",
    }
    checks.append(
        DoctorCheck(
            name="conformance",
            status=_conformance_status.get(manifest.conformance.status, "warn"),
            detail=(
                f"conformance status={manifest.conformance.status}"
                + (f" suite={manifest.conformance.suite}" if manifest.conformance.suite else "")
            ),
        )
    )
    checks.append(
        DoctorCheck(
            name="licenses",
            status="pass" if manifest.licenses else "warn",
            detail=(
                ", ".join(item.name for item in manifest.licenses)
                if manifest.licenses
                else "manifest declares no licenses"
            ),
        )
    )
    if any(check.status == "fail" for check in checks):
        status: Literal["pass", "warn", "fail"] = "fail"
    elif any(check.status == "warn" for check in checks):
        status = "warn"
    else:
        status = "pass"
    return DoctorReport(plugin_id=manifest.plugin_id, status=status, checks=checks)
