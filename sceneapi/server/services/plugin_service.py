"""Operator-facing plugin hub service helpers."""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from sceneapi.server.core.errors import NotFoundError, ValidationError
from sceneapi.server.core.public_outputs import sanitize_public_error_message
from sfm_hub.discovery import discover_plugins, discovered_plugin_ids
from sfm_hub.doctor import detect_external_tools, doctor_manifest
from sfm_hub.install import (
    build_container_service_install_plan,
    build_docker_install_plan,
    build_external_tool_plan,
    build_install_plan,
    parse_github_source,
    run_install_command,
    run_uv_install,
)
from sfm_hub.models import PluginManifest
from sfm_hub.provision import (
    ProvisioningError,
    normalize_provisioning_result,
    planned_package_provisioning,
    run_package_provisioner,
)
from sfm_hub.registry import get_manifest, list_manifests, search_manifests
from sfm_hub.routing import provider_records
from sfm_hub.state import (
    RoutingProfile,
    load_state,
    record_install,
    record_manual_install,
    set_default_profile,
    set_project_profile,
    set_provider_priority,
    set_workspace_profile,
    upsert_profile,
)
from sfm_hub.state import (
    set_enabled as set_plugin_enabled,
)


def _public_provisioning_error(value: object) -> str | None:
    if value is None:
        return None
    return sanitize_public_error_message(value)


IMAGE_BACKED_CONTAINER_SERVICE_ATTACH_WARNING = (
    "container_service mode attaches to an already-running plugin service; "
    "provision image-backed services with the Python hub provisioner, "
    "Compose/Kubernetes, or a deployment job before registering state"
)


def _manifest_summary(manifest: PluginManifest) -> dict[str, Any]:
    state = load_state()
    installed = state.installed.get(manifest.plugin_id)
    discovered = manifest.plugin_id in discovered_plugin_ids()
    return {
        "plugin_id": manifest.plugin_id,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "package_name": manifest.package_name,
        "github_url": manifest.github_url,
        "trust_tier": manifest.trust_tier,
        "runtime_modes": manifest.runtime_mode_names(),
        "providers": manifest.provider_ids(),
        "installed": installed is not None or discovered,
        "enabled": discovered if installed is None else installed.enabled,
        "_links": {
            "self": {"href": f"/v1/admin/plugins/{manifest.plugin_id}"},
            "doctor": {"href": f"/v1/admin/plugins/{manifest.plugin_id}:doctor"},
            "install": {"href": f"/v1/admin/plugins/{manifest.plugin_id}:install"},
        },
    }


def list_plugins(query: str | None = None) -> list[dict[str, Any]]:
    manifests = search_manifests(query) if query else list_manifests()
    return [_manifest_summary(manifest) for manifest in manifests]


def get_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        manifest = get_manifest(plugin_id)
    except KeyError as exc:
        raise NotFoundError(f"plugin {plugin_id!r} is not registered") from exc
    state = load_state()
    installed = state.installed.get(plugin_id)
    discovered = plugin_id in discovered_plugin_ids()
    return {
        "manifest": manifest,
        "installed": installed is not None or discovered,
        "enabled": discovered if installed is None else installed.enabled,
        "_links": {
            "self": {"href": f"/v1/admin/plugins/{plugin_id}"},
            "doctor": {"href": f"/v1/admin/plugins/{plugin_id}:doctor"},
            "install": {"href": f"/v1/admin/plugins/{plugin_id}:install"},
            "enable": {"href": f"/v1/admin/plugins/{plugin_id}:enable"},
            "disable": {"href": f"/v1/admin/plugins/{plugin_id}:disable"},
        },
    }


_HOST_OS = {"linux": "linux", "darwin": "macos", "win32": "windows"}


def _compatibility_problems(manifest: PluginManifest) -> list[str]:
    """Check the manifest's declared host compatibility against this host.

    Returns a list of human-readable mismatch strings (empty == compatible).
    Covers ``os`` (stdlib platform check) and ``python`` (PEP 440 specifier
    via ``packaging``); ``cuda`` is advisory metadata and left ungated.
    A malformed ``python`` specifier is skipped here — manifest validation
    is the place to reject those.
    """
    compat = manifest.compatibility
    problems: list[str] = []
    host_os = _HOST_OS.get(sys.platform, sys.platform)
    if compat.os and host_os not in compat.os:
        problems.append(f"plugin targets os {sorted(compat.os)}, this host is {host_os!r}")
    if compat.python:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        with suppress(InvalidSpecifier, InvalidVersion):
            host_py = Version(f"{sys.version_info.major}.{sys.version_info.minor}")
            if host_py not in SpecifierSet(compat.python):
                problems.append(
                    f"plugin requires python {compat.python}, this interpreter is {host_py}"
                )
    return problems


def install_plugin(
    plugin_id: str,
    *,
    method: str = "uv",
    github_url: str | None = None,
    ref: str | None = None,
    package_name: str | None = None,
    dry_run: bool = True,
    allow_unsafe_execution: bool = False,
    request_id: str | None = None,
    provision_runtime: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    _validate_request_id(request_id)
    if method not in {"uv", "docker", "container_service", "external_tool"}:
        raise ValidationError("method must be one of: uv, docker, container_service, external_tool")
    if not dry_run and not allow_unsafe_execution:
        raise ValidationError(
            "plugin installation can execute local tools; set allow_unsafe_execution=true "
            "for operator-approved execution, or keep dry_run=true"
        )

    manifest = None
    with suppress(KeyError):
        manifest = get_manifest(plugin_id)

    # Pre-flight host-compatibility gate. A real install (not a dry-run
    # plan) into an incompatible host is blocked unless force=true; the
    # mismatches always ride back as ``warnings`` so a dry-run still
    # surfaces them.
    compat_warnings: list[str] = []
    if manifest is not None:
        compat_warnings = _compatibility_problems(manifest)
        if compat_warnings and not dry_run and not force:
            raise ValidationError(
                "plugin is not compatible with this host: "
                + "; ".join(compat_warnings)
                + " — pass force=true to install anyway"
            )

    if method != "uv":
        if manifest is None:
            raise ValidationError(f"plugin {plugin_id!r} is not registered")
        runtime = manifest.runtime_modes.uv
        source = parse_github_source(
            github_url or manifest.github_url,
            ref=ref or (runtime.ref if runtime is not None else None),
            package=package_name or manifest.package_name,
        )
        if method == "docker":
            plan = build_docker_install_plan(
                plugin_id, manifest.runtime_modes.docker, source=source
            )
        elif method == "container_service":
            plan = build_container_service_install_plan(
                plugin_id,
                manifest.runtime_modes.container_service,
                source=source,
            )
        else:
            plan = build_external_tool_plan(plugin_id, source=source)
        if not dry_run:
            existing = load_state().installed.get(plugin_id)
            if (
                request_id
                and existing is not None
                and existing.request_id == request_id
                and existing.method == method
            ):
                if existing.provisioning_status == "failed":
                    raise ValidationError(
                        _public_provisioning_error(existing.provisioning_error)
                        or "plugin install/provisioning failed"
                    )
                return {
                    "plugin_id": plugin_id,
                    "method": method,
                    "dry_run": False,
                    "installed": True,
                    "command": plan.command,
                    "direct_reference": plan.direct_reference,
                    "warnings": plan.warnings + compat_warnings,
                    "resolved_commit": plan.resolved_commit,
                    "provision_runtime": existing.provision_runtime,
                    "provisioned": existing.provisioned,
                    "provisioning_status": existing.provisioning_status,
                    "provisioning_error": _public_provisioning_error(existing.provisioning_error),
                    "request_id": request_id,
                    "provisioning": None,
                }
            if method == "docker":
                docker_runtime = manifest.runtime_modes.docker
                if docker_runtime is None:
                    raise ValidationError(f"plugin {plugin_id!r} does not define a docker runtime")
                if not plan.command:
                    raise ValidationError(
                        f"plugin {plugin_id!r} docker runtime has no pull/build command"
                    )
            if method == "container_service" and manifest.runtime_modes.container_service is None:
                raise ValidationError(
                    f"plugin {plugin_id!r} does not define a container_service runtime"
                )
            if method == "container_service":

                def fail_install(message: str) -> None:
                    public_message = sanitize_public_error_message(message)
                    record_manual_install(
                        plugin_id,
                        method=method,
                        source_url=manifest.github_url,
                        ref=source.ref,
                        enabled=True,
                        provisioning_status="failed",
                        provisioning_error=public_message,
                        request_id=request_id,
                    )
                    raise ValidationError(public_message)

                pre_report = doctor_manifest(manifest)
                pre_check = next(
                    item for item in pre_report.checks if item.name == "container_service"
                )
                if pre_check.status == "warn" and "endpoint is not configured" in pre_check.detail:
                    fail_install(f"container_service health check failed: {pre_check.detail}")
                if not provision_runtime:
                    if pre_check.status != "pass":
                        fail_install(f"container_service health check failed: {pre_check.detail}")
                    check = pre_check
                else:
                    if plan.command:
                        try:
                            run_install_command(plan)
                        except Exception as exc:
                            fail_install(f"container_service provisioning failed: {exc}")
                        report = doctor_manifest(manifest)
                        check = next(
                            item for item in report.checks if item.name == "container_service"
                        )
                    elif pre_check.status == "pass":
                        check = pre_check
                    else:
                        fail_install(f"container_service health check failed: {pre_check.detail}")
                if check.status != "pass":
                    fail_install(f"container_service health check failed: {check.detail}")
            else:
                run_install_command(plan)
            record_manual_install(
                plugin_id,
                method=method,
                source_url=manifest.github_url,
                ref=source.ref,
                enabled=True,
                provision_runtime=provision_runtime,
                provisioned=bool(method == "container_service" and plan.command),
                provisioning_status=(
                    "succeeded"
                    if method == "container_service" and provision_runtime and plan.command
                    else "not_requested"
                ),
                request_id=request_id,
            )
        response_command = plan.command
        response_warnings = plan.warnings + compat_warnings
        if (
            dry_run
            and method == "container_service"
            and manifest.runtime_modes.container_service is not None
            and manifest.runtime_modes.container_service.image is not None
        ):
            response_command = []
            response_warnings = [
                IMAGE_BACKED_CONTAINER_SERVICE_ATTACH_WARNING,
                *compat_warnings,
            ]
        return {
            "plugin_id": plugin_id,
            "method": method,
            "dry_run": dry_run,
            "installed": not dry_run,
            "command": response_command,
            "direct_reference": plan.direct_reference,
            "warnings": response_warnings,
            "resolved_commit": plan.resolved_commit,
            "provision_runtime": bool(
                method == "container_service"
                and manifest.runtime_modes.container_service is not None
                and provision_runtime
            ),
            "provisioned": bool(
                not dry_run and method == "container_service" and provision_runtime and plan.command
            ),
            "provisioning_status": (
                "succeeded"
                if not dry_run
                and method == "container_service"
                and provision_runtime
                and plan.command
                else "not_requested"
            ),
            "provisioning_error": None,
            "request_id": request_id,
            "provisioning": None,
        }

    if manifest is not None and github_url is None:
        runtime = manifest.runtime_modes.uv
        if runtime is None:
            raise ValidationError(f"plugin {plugin_id!r} does not define a uv runtime")
        github_url = runtime.url
        ref = ref or runtime.ref
        package_name = package_name or runtime.package
    if github_url is None:
        raise ValidationError("github_url is required when installing an unregistered plugin")

    try:
        source = parse_github_source(github_url, ref=ref, package=package_name)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    plan = build_install_plan(source)
    installed = False
    provisioning: dict[str, Any] | None = None
    provisioning_status = "not_requested"
    provisioning_error: str | None = None
    if not dry_run:
        existing = load_state().installed.get(plugin_id)
        if request_id and existing is not None and existing.request_id == request_id:
            if existing.provisioning_status == "failed":
                public_error = (
                    _public_provisioning_error(existing.provisioning_error)
                    or "previous attempt failed"
                )
                raise ValidationError("plugin runtime provisioning failed: " + public_error)
            return {
                "plugin_id": plugin_id,
                "method": "uv",
                "dry_run": False,
                "installed": True,
                "command": plan.command,
                "direct_reference": plan.direct_reference,
                "warnings": plan.warnings + compat_warnings,
                "resolved_commit": plan.resolved_commit,
                "provision_runtime": existing.provision_runtime,
                "provisioned": existing.provisioned,
                "provisioning_status": existing.provisioning_status,
                "provisioning_error": _public_provisioning_error(existing.provisioning_error),
                "request_id": request_id,
                "provisioning": None,
            }
        run_uv_install(plan)
        if provision_runtime:
            record_install(
                plugin_id,
                plan,
                provision_runtime=True,
                provisioned=False,
                provisioning_status="running",
                request_id=request_id,
            )
            try:
                provisioning = run_package_provisioner(
                    plan.source.inferred_package,
                    dry_run=False,
                    force=force,
                )
                provisioning = normalize_provisioning_result(provisioning)
            except ProvisioningError as exc:
                provisioning_error = sanitize_public_error_message(exc)
                record_install(
                    plugin_id,
                    plan,
                    provision_runtime=True,
                    provisioned=False,
                    provisioning_status="failed",
                    provisioning_error=provisioning_error,
                    request_id=request_id,
                )
                raise ValidationError(
                    f"plugin runtime provisioning failed: {provisioning_error}"
                ) from exc
            provisioned = bool(provisioning["provisioned"])
            provisioning_status = "succeeded" if provisioned else "skipped"
            record_install(
                plugin_id,
                plan,
                provision_runtime=True,
                provisioned=provisioned,
                provisioning_status=provisioning_status,
                request_id=request_id,
            )
        else:
            record_install(
                plugin_id,
                plan,
                provision_runtime=False,
                provisioned=False,
                provisioning_status="not_requested",
                request_id=request_id,
            )
        installed = True
    elif provision_runtime:
        provisioning = planned_package_provisioning(plan.source.inferred_package)
        provisioning_status = "planned"
    provisioning_warnings = [] if provisioning is None else list(provisioning["warnings"])
    return {
        "plugin_id": plugin_id,
        "method": "uv",
        "dry_run": dry_run,
        "installed": installed,
        "command": plan.command,
        "direct_reference": plan.direct_reference,
        "warnings": plan.warnings + provisioning_warnings + compat_warnings,
        "resolved_commit": plan.resolved_commit,
        "provision_runtime": provision_runtime,
        "provisioned": bool(provisioning and provisioning["provisioned"]),
        "provisioning_status": provisioning_status,
        "provisioning_error": provisioning_error,
        "request_id": request_id,
        "provisioning": provisioning,
    }


def _validate_request_id(request_id: str | None) -> None:
    if request_id is None:
        return
    try:
        request_id.encode("ascii")
        parsed = UUID(request_id)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValidationError("request_id must be a UUID string") from exc
    if str(parsed) != request_id.lower():
        raise ValidationError("request_id must be a canonical hyphenated UUID string")


def enable_plugin(plugin_id: str) -> dict[str, Any]:
    get_plugin(plugin_id)
    try:
        set_plugin_enabled(plugin_id, True)
    except KeyError as exc:
        if plugin_id not in discovered_plugin_ids():
            raise ValidationError(f"plugin {plugin_id!r} is not installed") from exc
        record_manual_install(plugin_id, method="entry_point", enabled=True)
    return get_plugin(plugin_id)


def disable_plugin(plugin_id: str) -> dict[str, Any]:
    get_plugin(plugin_id)
    try:
        set_plugin_enabled(plugin_id, False)
    except KeyError as exc:
        if plugin_id not in discovered_plugin_ids():
            raise ValidationError(f"plugin {plugin_id!r} is not installed") from exc
        record_manual_install(plugin_id, method="entry_point", enabled=False)
    return get_plugin(plugin_id)


def doctor_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        manifest = get_manifest(plugin_id)
    except KeyError as exc:
        raise NotFoundError(f"plugin {plugin_id!r} is not registered") from exc
    return doctor_manifest(manifest).model_dump(mode="json")


def detect_tools() -> dict[str, Any]:
    return {"tools": detect_external_tools(list_manifests())}


def list_entry_points(*, load: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "plugin_id": item.plugin_id,
            "entry_point": item.entry_point,
            "distribution": item.distribution,
            "version": item.version,
            "manifest": item.manifest,
            "load_error": (
                sanitize_public_error_message(item.load_error) if item.load_error else None
            ),
        }
        for item in discover_plugins(load=load)
    ]


def list_providers() -> list[dict[str, Any]]:
    return [
        {
            "provider_id": row.provider.provider_id,
            "plugin_id": row.plugin_id,
            "display_name": row.provider.display_name,
            "description": row.provider.description,
            "capabilities": row.provider.capabilities,
            "backend_actions": row.provider.backend_actions,
            "runtime_modes": row.runtime_modes,
            "installed": row.installed,
            "enabled": row.enabled,
            "_links": {"plugin": {"href": f"/v1/admin/plugins/{row.plugin_id}"}},
        }
        for row in provider_records()
    ]


def routing_state() -> dict[str, Any]:
    state = load_state()
    return {
        "default_profile": state.default_profile,
        "provider_priority": state.provider_priority,
        "profiles": state.profiles,
        "project_profiles": state.project_profiles,
        "workspace_profiles": state.workspace_profiles,
    }


def create_profile(name: str, routes: dict[str, list[str]]) -> dict[str, Any]:
    try:
        upsert_profile(RoutingProfile(name=name, routes=routes))
    except (KeyError, PydanticValidationError) as exc:
        raise ValidationError(str(exc)) from exc
    return routing_state()


def use_default_profile(name: str) -> dict[str, Any]:
    try:
        set_default_profile(name)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    return routing_state()


def use_provider_priority(providers: list[str]) -> dict[str, Any]:
    try:
        set_provider_priority(providers)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    return routing_state()


def assign_project_profile(project_id: str, profile: str) -> dict[str, Any]:
    try:
        set_project_profile(project_id, profile)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    return routing_state()


def assign_workspace_profile(workspace: str, profile: str) -> dict[str, Any]:
    try:
        set_workspace_profile(workspace, profile)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    return routing_state()
