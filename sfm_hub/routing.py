"""Provider discovery and deterministic stage-provider resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sfm_hub.discovery import discovered_plugin_ids
from sfm_hub.models import PluginManifest, ProviderManifest, RuntimeMode
from sfm_hub.registry import list_manifests
from sfm_hub.state import PluginState, load_state

STAGE_TO_ROUTE_KEY = {
    "features": "features",
    "pairs": "pairs",
    "matcher": "matcher",
    "verify": "verify",
    "mapping": "mapping",
    "actions": "actions",
}


@dataclass(frozen=True)
class ProviderRecord:
    plugin_id: str
    installed: bool
    enabled: bool
    runtime_modes: list[RuntimeMode]
    provider: ProviderManifest


class ProviderAmbiguityError(ValueError):
    def __init__(self, stage: str, candidates: list[str]) -> None:
        self.stage = stage
        self.candidates = sorted(candidates)
        super().__init__(
            f"{stage} has multiple candidate providers: {', '.join(self.candidates)}; "
            "set provider on the request or configure a routing profile"
        )


def provider_records(
    *,
    state: PluginState | None = None,
    manifests: list[PluginManifest] | None = None,
    installed_only: bool = True,
    enabled_only: bool = True,
) -> list[ProviderRecord]:
    state = state or load_state()
    manifests = manifests or list_manifests()
    rows: list[ProviderRecord] = []
    entry_point_installed = discovered_plugin_ids()
    for manifest in manifests:
        state_row = state.installed.get(manifest.plugin_id)
        installed = state_row is not None or manifest.plugin_id in entry_point_installed
        enabled = installed and (state_row.enabled if state_row is not None else True)
        if installed_only and not installed:
            continue
        if enabled_only and not enabled:
            continue
        for provider in manifest.providers:
            rows.append(
                ProviderRecord(
                    plugin_id=manifest.plugin_id,
                    installed=installed,
                    enabled=enabled,
                    runtime_modes=manifest.runtime_mode_names(),
                    provider=provider,
                )
            )
    return sorted(rows, key=lambda row: (row.provider.priority_hint, row.provider.provider_id))


def _candidate_records(
    *,
    stage: str,
    capability: str | None,
    state: PluginState,
    manifests: list[PluginManifest] | None = None,
) -> list[ProviderRecord]:
    route_key = STAGE_TO_ROUTE_KEY.get(stage, stage)
    candidates: list[ProviderRecord] = []
    for row in provider_records(state=state, manifests=manifests):
        supports_capability = capability is None or capability in row.provider.capabilities
        supports_route_key = (
            route_key in row.provider.capabilities or route_key in row.provider.backend_actions
        )
        if supports_capability or supports_route_key:
            candidates.append(row)
    return candidates


def resolve_provider(
    *,
    stage: str,
    capability: str | None = None,
    requested_provider: str | None = None,
    project_id: str | None = None,
    workspace: str | None = None,
    state: PluginState | None = None,
    manifests: list[PluginManifest] | None = None,
) -> str | None:
    """Resolve one provider without choosing arbitrarily.

    Empty hub state returns ``None`` so a clean sfmapi install can keep using
    its configured backend directly. Once plugins are installed, ambiguity is
    reported instead of falling through to whichever backend happens to run.
    """

    state = state or load_state()
    candidates = _candidate_records(
        stage=stage,
        capability=capability,
        state=state,
        manifests=manifests,
    )
    if not candidates:
        return requested_provider

    candidate_ids = [row.provider.provider_id for row in candidates]
    if requested_provider:
        if requested_provider not in candidate_ids:
            raise KeyError(
                f"provider {requested_provider!r} is not enabled for {stage}; "
                f"candidates: {', '.join(sorted(candidate_ids))}"
            )
        return requested_provider

    profile_names = []
    if project_id and project_id in state.project_profiles:
        profile_names.append(state.project_profiles[project_id])
    if workspace and workspace in state.workspace_profiles:
        profile_names.append(state.workspace_profiles[workspace])
    if state.default_profile:
        profile_names.append(state.default_profile)
    for profile_name in profile_names:
        profile = state.profiles.get(profile_name)
        if profile is None:
            continue
        for provider_id in profile.routes.get(stage, []):
            if provider_id in candidate_ids:
                return provider_id

    for provider_id in state.provider_priority:
        if provider_id in candidate_ids:
            return provider_id

    if len(set(candidate_ids)) == 1:
        return candidate_ids[0]
    raise ProviderAmbiguityError(stage, candidate_ids)
