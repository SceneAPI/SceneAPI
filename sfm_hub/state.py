"""Local plugin installation, enablement, and routing-profile state."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sfm_hub.install import InstallPlan

_log = logging.getLogger(__name__)

ProvisioningStatus = Literal[
    "not_requested",
    "planned",
    "running",
    "succeeded",
    "skipped",
    "failed",
]
ROUTING_ROUTE_KEYS = frozenset({
    "features",
    "pairs",
    "matcher",
    "verify",
    "mapping",
    "radiance",
    "actions",
})


class InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    method: str = "uv"
    source_url: str
    ref: str = "main"
    resolved_commit: str | None = None
    installed_at: str
    enabled: bool = True
    provision_runtime: bool = False
    provisioned: bool = False
    provisioning_status: ProvisioningStatus = "not_requested"
    provisioning_error: str | None = None
    request_id: str | None = None


class RoutingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    routes: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("routes")
    @classmethod
    def _routes_are_supported(cls, routes: dict[str, list[str]]) -> dict[str, list[str]]:
        unsupported = sorted(set(routes) - ROUTING_ROUTE_KEYS)
        if unsupported:
            raise ValueError(
                "routes contain unsupported route key(s): " + ", ".join(unsupported)
            )
        return routes


class PluginState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installed: dict[str, InstalledPlugin] = Field(default_factory=dict)
    provider_priority: list[str] = Field(default_factory=list)
    profiles: dict[str, RoutingProfile] = Field(default_factory=dict)
    default_profile: str | None = None
    project_profiles: dict[str, str] = Field(default_factory=dict)
    workspace_profiles: dict[str, str] = Field(default_factory=dict)


def default_state_path() -> Path:
    override = os.environ.get("SFMAPI_PLUGIN_STATE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "sfmapi" / "plugins.json"


@contextmanager
def _state_file_lock(state_path: Path) -> Iterator[None]:
    lock_path = Path(f"{state_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _state_path(path: Path | None) -> Path:
    return path or default_state_path()


def _load_state_unlocked(state_path: Path) -> PluginState:
    if not state_path.exists():
        return PluginState()
    return PluginState.model_validate_json(state_path.read_text(encoding="utf-8"))


def load_state(path: Path | None = None) -> PluginState:
    state_path = _state_path(path)
    with _state_file_lock(state_path):
        return _load_state_unlocked(state_path)


def _save_state_unlocked(state: PluginState, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    tmp = state_path.with_name(f"{state_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, state_path)
    if os.name != "nt":
        dir_fd = os.open(state_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def save_state(state: PluginState, path: Path | None = None) -> None:
    state_path = _state_path(path)
    with _state_file_lock(state_path):
        _save_state_unlocked(state, state_path)


def _mutate_state(path: Path | None, update: Callable[[PluginState], PluginState]) -> PluginState:
    state_path = _state_path(path)
    with _state_file_lock(state_path):
        state = _load_state_unlocked(state_path)
        state = update(state)
        _save_state_unlocked(state, state_path)
        return state


def _record_installed(
    state: PluginState,
    record: InstalledPlugin,
) -> PluginState:
    """Persist an install record, idempotent-by-ref.

    Re-recording the same plugin with an identical method / source / ref /
    enabled state is a no-op (no rewritten ``installed_at``). A record that
    differs is treated as a reinstall: it overwrites the prior one but logs
    a warning first, so a method/ref change isn't silent.
    """
    plugin_id = record.plugin_id
    existing = state.installed.get(plugin_id)
    if existing is not None:
        same = (
            existing.method == record.method
            and existing.source_url == record.source_url
            and existing.ref == record.ref
            and existing.resolved_commit == record.resolved_commit
            and existing.enabled == record.enabled
            and existing.provision_runtime == record.provision_runtime
            and existing.provisioned == record.provisioned
            and existing.provisioning_status == record.provisioning_status
            and existing.provisioning_error == record.provisioning_error
            and existing.request_id == record.request_id
        )
        if same:
            return state
        _log.warning(
            "sfm_hub.state.reinstall_overwrites_record",
            extra={
                "plugin_id": plugin_id,
                "old": f"{existing.method}@{existing.ref}",
                "new": f"{record.method}@{record.ref}",
            },
        )
    state.installed[plugin_id] = record
    return state


def record_install(
    plugin_id: str,
    plan: InstallPlan,
    *,
    path: Path | None = None,
    enabled: bool = True,
    provision_runtime: bool = False,
    provisioned: bool = False,
    provisioning_status: ProvisioningStatus = "not_requested",
    provisioning_error: str | None = None,
    request_id: str | None = None,
) -> PluginState:
    record = InstalledPlugin(
        plugin_id=plugin_id,
        method=plan.method,
        source_url=plan.source.normalized_url,
        ref=plan.source.ref,
        resolved_commit=plan.resolved_commit,
        installed_at=datetime.now(UTC).isoformat(),
        enabled=enabled,
        provision_runtime=provision_runtime,
        provisioned=provisioned,
        provisioning_status=provisioning_status,
        provisioning_error=provisioning_error,
        request_id=request_id,
    )
    return _mutate_state(path, lambda state: _record_installed(state, record))


def record_manual_install(
    plugin_id: str,
    *,
    method: str,
    source_url: str = "",
    ref: str = "",
    path: Path | None = None,
    enabled: bool = True,
    provision_runtime: bool = False,
    provisioned: bool = False,
    provisioning_status: ProvisioningStatus = "not_requested",
    provisioning_error: str | None = None,
    request_id: str | None = None,
) -> PluginState:
    record = InstalledPlugin(
        plugin_id=plugin_id,
        method=method,
        source_url=source_url,
        ref=ref,
        installed_at=datetime.now(UTC).isoformat(),
        enabled=enabled,
        provision_runtime=provision_runtime,
        provisioned=provisioned,
        provisioning_status=provisioning_status,
        provisioning_error=provisioning_error,
        request_id=request_id,
    )
    return _mutate_state(path, lambda state: _record_installed(state, record))


def set_enabled(plugin_id: str, enabled: bool, *, path: Path | None = None) -> PluginState:
    def _update(state: PluginState) -> PluginState:
        if plugin_id not in state.installed:
            raise KeyError(f"plugin {plugin_id!r} is not installed")
        state.installed[plugin_id].enabled = enabled
        return state

    return _mutate_state(path, _update)


def upsert_profile(profile: RoutingProfile, *, path: Path | None = None) -> PluginState:
    """Create or replace a routing profile.

    Every provider id in ``profile.routes`` must be declared by some known
    plugin manifest (bundled or discovered) — otherwise a typo'd id
    silently no-ops at routing time. Validating against the full manifest
    universe (not just *installed* plugins) still lets operators stage a
    routing profile before installing the plugin.
    """
    known, ambiguous_bare = _known_provider_ids_and_ambiguous_bare()
    referenced = {pid for providers in profile.routes.values() for pid in providers}
    unknown = sorted(referenced - known)
    if unknown:
        raise KeyError(
            f"routing profile {profile.name!r} references unknown provider id(s): "
            f"{', '.join(unknown)}"
        )
    ambiguous = sorted(
        pid for pid in referenced if "@" not in pid and pid in ambiguous_bare
    )
    if ambiguous:
        raise KeyError(
            f"routing profile {profile.name!r} references ambiguous provider id(s): "
            f"{', '.join(ambiguous)}; use provider@plugin"
        )
    def _update(state: PluginState) -> PluginState:
        state.profiles[profile.name] = profile
        return state

    return _mutate_state(path, _update)


def _known_provider_ids_and_ambiguous_bare() -> tuple[set[str], set[str]]:
    """Provider ids and provider@plugin selectors declared by manifests.

    Late import: ``sfm_hub.registry`` -> ``sfm_hub.discovery`` -> this
    module, so importing it at module load would cycle.
    """
    from sfm_hub.registry import list_manifests

    known: set[str] = set()
    plugins_by_provider: dict[str, set[str]] = {}
    for manifest in list_manifests():
        for provider_id in manifest.provider_ids():
            known.add(provider_id)
            known.add(f"{provider_id}@{manifest.plugin_id}")
            plugins_by_provider.setdefault(provider_id, set()).add(manifest.plugin_id)
    ambiguous = {
        provider_id
        for provider_id, plugin_ids in plugins_by_provider.items()
        if len(plugin_ids) > 1
    }
    return known, ambiguous


def _known_provider_ids() -> set[str]:
    known, _ = _known_provider_ids_and_ambiguous_bare()
    return known


def set_default_profile(name: str, *, path: Path | None = None) -> PluginState:
    if not name:
        raise KeyError("profile name must be non-empty")

    def _update(state: PluginState) -> PluginState:
        if name not in state.profiles:
            raise KeyError(f"unknown routing profile {name!r}")
        state.default_profile = name
        return state

    return _mutate_state(path, _update)


def set_provider_priority(providers: list[str], *, path: Path | None = None) -> PluginState:
    known, ambiguous_bare = _known_provider_ids_and_ambiguous_bare()
    unknown = sorted(set(providers) - known)
    if unknown:
        raise KeyError(
            "provider priority references unknown provider id(s): "
            f"{', '.join(unknown)}"
        )
    ambiguous = sorted(
        pid for pid in set(providers) if "@" not in pid and pid in ambiguous_bare
    )
    if ambiguous:
        raise KeyError(
            "provider priority references ambiguous provider id(s): "
            f"{', '.join(ambiguous)}; use provider@plugin"
        )
    def _update(state: PluginState) -> PluginState:
        state.provider_priority = list(providers)
        return state

    return _mutate_state(path, _update)


def set_project_profile(
    project_id: str,
    profile_name: str,
    *,
    path: Path | None = None,
) -> PluginState:
    if not profile_name:
        raise KeyError("profile name must be non-empty")
    def _update(state: PluginState) -> PluginState:
        if profile_name not in state.profiles:
            raise KeyError(f"unknown routing profile {profile_name!r}")
        state.project_profiles[project_id] = profile_name
        return state

    return _mutate_state(path, _update)


def set_workspace_profile(
    workspace: str,
    profile_name: str,
    *,
    path: Path | None = None,
) -> PluginState:
    if not profile_name:
        raise KeyError("profile name must be non-empty")
    def _update(state: PluginState) -> PluginState:
        if profile_name not in state.profiles:
            raise KeyError(f"unknown routing profile {profile_name!r}")
        state.workspace_profiles[workspace] = profile_name
        return state

    return _mutate_state(path, _update)
