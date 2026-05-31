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

from sfm_hub.models import PluginManifest
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
        return None, f"{type(exc).__name__}: {exc}"
    text = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return text[:500] or None, f"exit {result.returncode}"
    return text[:500] or None, None


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
                    path=value,
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
                    path=path,
                    version=version,
                    error=error,
                )
            )
        out[manifest.plugin_id] = detections
    return out


def _probe_uv_plugin(manifest: PluginManifest) -> DoctorCheck:
    """Is a uv-installed plugin actually importable in this environment?

    A discoverable ``sfmapi.backends`` entry point is the real signal that
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
            f"{manifest.plugin_id} declares a uv runtime but no sfmapi.backends "
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


def _resolve_container_service_url(manifest: PluginManifest) -> tuple[str | None, str | None]:
    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        return None, None
    if runtime.service.url_env:
        value = os.environ.get(runtime.service.url_env)
        if value:
            return value, runtime.service.url_env
    return runtime.service.default_url, runtime.service.url_env


def _probe_container_service_plugin(manifest: PluginManifest) -> DoctorCheck:
    """Verify a container service endpoint is configured and reachable."""

    runtime = manifest.runtime_modes.container_service
    if runtime is None:
        return DoctorCheck(
            name="container_service",
            status="warn",
            detail="manifest does not declare a container_service runtime",
        )

    base_url, env_var = _resolve_container_service_url(manifest)
    if not base_url:
        suffix = f"; set {env_var}" if env_var else ""
        return DoctorCheck(
            name="container_service",
            status="warn",
            detail=f"container service endpoint is not configured{suffix}",
        )
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail="container service endpoint must be http:// or https://",
            metadata={"reason": "invalid_endpoint"},
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
            detail=f"container service health check returned HTTP {status}: {health_url}",
            metadata={"reason": "bad_health_status", "status_code": str(status)},
        )

    version_body, error = _http_json(version_url, timeout=runtime.healthcheck.timeout_seconds)
    if error is not None:
        return error
    if not isinstance(version_body, Mapping):
        return DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version response must be a JSON object: {version_url}",
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

    version_detail = (
        f" for {actual_protocol} {actual_version}"
        if actual_protocol and actual_version
        else ""
    )
    return DoctorCheck(
        name="container_service",
        status="pass",
        detail=f"{manifest.plugin_id} service is healthy at {health_url}{version_detail}",
        metadata={
            "protocol": actual_protocol,
            "protocol_version": actual_version,
            "version_url": version_url,
        },
    )


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
            detail=f"container service health check returned HTTP {exc.code}: {url}",
            metadata={"reason": "http_error", "status_code": str(exc.code)},
        )
    except URLError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check failed for {url}: {exc}",
            metadata={"reason": "connection_error"},
        )
    except OSError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service health check failed for {url}: {exc}",
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
            detail=f"container service version check returned HTTP {exc.code}: {url}",
            metadata={"reason": "version_http_error", "status_code": str(exc.code)},
        )
    except URLError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check failed for {url}: {exc}",
            metadata={"reason": "version_connection_error"},
        )
    except OSError as exc:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check failed for {url}: {exc}",
            metadata={"reason": "version_connection_error"},
        )
    if not 200 <= status < 300:
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version check returned HTTP {status}: {url}",
            metadata={"reason": "bad_version_status", "status_code": str(status)},
        )
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, DoctorCheck(
            name="container_service",
            status="fail",
            detail=f"container service version response is not valid JSON: {url}",
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
                    detail=installed.provisioning_error
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
