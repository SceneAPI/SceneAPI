"""Operator-facing plugin hub service helpers."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from app.core.errors import NotFoundError, ValidationError
from sfm_hub.discovery import discover_plugins, discovered_plugin_ids
from sfm_hub.doctor import detect_external_tools, doctor_manifest
from sfm_hub.install import (
    build_docker_install_plan,
    build_external_tool_plan,
    build_install_plan,
    parse_github_source,
    run_install_command,
    run_uv_install,
)
from sfm_hub.models import PluginManifest
from sfm_hub.registry import get_manifest, list_manifests, search_manifests
from sfm_hub.routing import provider_records
from sfm_hub.state import (
    RoutingProfile,
    load_state,
    record_install,
    record_manual_install,
    set_default_profile,
    set_project_profile,
    set_workspace_profile,
    upsert_profile,
)
from sfm_hub.state import set_enabled as set_plugin_enabled


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


def install_plugin(
    plugin_id: str,
    *,
    method: str = "uv",
    github_url: str | None = None,
    ref: str | None = None,
    package_name: str | None = None,
    dry_run: bool = True,
    allow_unsafe_execution: bool = False,
) -> dict[str, Any]:
    if not dry_run and not allow_unsafe_execution:
        raise ValidationError(
            "plugin installation can execute local tools; set allow_unsafe_execution=true "
            "for operator-approved execution, or keep dry_run=true"
        )

    manifest = None
    with suppress(KeyError):
        manifest = get_manifest(plugin_id)

    if method != "uv":
        if manifest is None:
            raise ValidationError(f"plugin {plugin_id!r} is not registered")
        runtime = manifest.runtime_modes.uv
        source = parse_github_source(
            github_url or manifest.github_url,
            ref=ref or (runtime.ref if runtime is not None else None),
            package=package_name or manifest.package_name,
        )
        plan = (
            build_docker_install_plan(plugin_id, manifest.runtime_modes.docker, source=source)
            if method == "docker"
            else build_external_tool_plan(plugin_id, source=source)
        )
        if not dry_run:
            run_install_command(plan)
            record_manual_install(
                plugin_id,
                method=method,
                source_url=manifest.github_url,
                ref=source.ref,
                enabled=True,
            )
        return {
            "plugin_id": plugin_id,
            "method": method,
            "dry_run": dry_run,
            "installed": not dry_run,
            "command": plan.command,
            "direct_reference": plan.direct_reference,
            "warnings": plan.warnings,
            "resolved_commit": plan.resolved_commit,
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
    if not dry_run:
        run_uv_install(plan)
        record_install(plugin_id, plan)
        installed = True
    return {
        "plugin_id": plugin_id,
        "method": "uv",
        "dry_run": dry_run,
        "installed": installed,
        "command": plan.command,
        "direct_reference": plan.direct_reference,
        "warnings": plan.warnings,
        "resolved_commit": plan.resolved_commit,
    }


def enable_plugin(plugin_id: str) -> dict[str, Any]:
    get_plugin(plugin_id)
    try:
        set_plugin_enabled(plugin_id, True)
    except KeyError as exc:
        raise ValidationError(f"plugin {plugin_id!r} is not installed") from exc
    return get_plugin(plugin_id)


def disable_plugin(plugin_id: str) -> dict[str, Any]:
    get_plugin(plugin_id)
    try:
        set_plugin_enabled(plugin_id, False)
    except KeyError as exc:
        raise ValidationError(f"plugin {plugin_id!r} is not installed") from exc
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
            "load_error": item.load_error,
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
    upsert_profile(RoutingProfile(name=name, routes=routes))
    return routing_state()


def use_default_profile(name: str) -> dict[str, Any]:
    set_default_profile(name)
    return routing_state()


def assign_project_profile(project_id: str, profile: str) -> dict[str, Any]:
    set_project_profile(project_id, profile)
    return routing_state()


def assign_workspace_profile(workspace: str, profile: str) -> dict[str, Any]:
    set_workspace_profile(workspace, profile)
    return routing_state()
