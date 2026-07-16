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
            "set a plugin-qualified provider selector (provider@plugin) on the "
            "request or configure one in a routing profile"
        )


def provider_records(
    *,
    state: PluginState | None = None,
    manifests: list[PluginManifest] | None = None,
    installed_only: bool = True,
    enabled_only: bool = True,
) -> list[ProviderRecord]:
    state = state or load_state()
    if manifests is None:
        manifests = list_manifests()
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


def _split_provider_selector(selector: str) -> tuple[str, str | None]:
    provider_id, sep, plugin_id = selector.partition("@")
    return provider_id, plugin_id if sep else None


def provider_enabled(provider_id: str, *, state: PluginState | None = None) -> bool | None:
    """Return enabled state for a known installed provider, or None if unknown to sfm_hub."""

    bare_provider_id, plugin_id = _split_provider_selector(provider_id)
    matches = [
        row
        for row in provider_records(
            state=state,
            installed_only=True,
            enabled_only=False,
        )
        if row.provider.provider_id == bare_provider_id
        and (plugin_id is None or row.plugin_id == plugin_id)
    ]
    if not matches:
        return None
    return any(row.enabled for row in matches)


def ensure_provider_enabled(provider_id: str, *, state: PluginState | None = None) -> None:
    enabled = provider_enabled(provider_id, state=state)
    if enabled is False:
        raise KeyError(f"provider {provider_id!r} is disabled")


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
        if capability is not None:
            if capability in row.provider.capabilities:
                candidates.append(row)
            continue
        if route_key in row.provider.capabilities or route_key in row.provider.backend_actions:
            candidates.append(row)
    return candidates


def _provider_selector(row: ProviderRecord) -> str:
    return f"{row.provider.provider_id}@{row.plugin_id}"


def _display_candidates(rows: list[ProviderRecord]) -> list[str]:
    return [_provider_selector(row) for row in rows]


def _known_provider_matches(
    selector: str,
    *,
    state: PluginState,
    manifests: list[PluginManifest] | None = None,
) -> list[ProviderRecord]:
    rows = provider_records(
        state=state,
        manifests=manifests,
        installed_only=True,
        enabled_only=False,
    )
    return _matches_provider(rows, selector)


def _matches_provider(rows: list[ProviderRecord], selector: str) -> list[ProviderRecord]:
    provider_id, sep, plugin_id = selector.partition("@")
    if sep:
        return [
            row
            for row in rows
            if row.provider.provider_id == provider_id and row.plugin_id == plugin_id
        ]
    return [row for row in rows if row.provider.provider_id == selector]


def _single_provider_selector_or_raise(
    stage: str,
    rows: list[ProviderRecord],
    *,
    qualified: bool = False,
) -> str:
    if len({(row.provider.provider_id, row.plugin_id) for row in rows}) == 1:
        return _provider_selector(rows[0]) if qualified else rows[0].provider.provider_id
    raise ProviderAmbiguityError(stage, _display_candidates(rows))


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
        if requested_provider:
            known_matches = _known_provider_matches(
                requested_provider,
                state=state,
                manifests=manifests,
            )
            if known_matches:
                if not any(row.enabled for row in known_matches):
                    raise KeyError(f"provider {requested_provider!r} is disabled")
                raise KeyError(
                    f"provider {requested_provider!r} is not enabled for {stage}; candidates: "
                )
        return requested_provider

    candidate_ids = [row.provider.provider_id for row in candidates]
    candidate_selectors = _display_candidates(candidates)
    if requested_provider:
        matches = _matches_provider(candidates, requested_provider)
        if not matches:
            raise KeyError(
                f"provider {requested_provider!r} is not enabled for {stage}; "
                f"candidates: {', '.join(sorted(candidate_selectors))}"
            )
        return _single_provider_selector_or_raise(
            stage,
            matches,
            qualified="@" in requested_provider,
        )

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
            matches = _matches_provider(candidates, provider_id)
            if matches:
                return _single_provider_selector_or_raise(
                    stage,
                    matches,
                    qualified="@" in provider_id,
                )

    for provider_id in state.provider_priority:
        matches = _matches_provider(candidates, provider_id)
        if matches:
            return _single_provider_selector_or_raise(
                stage,
                matches,
                qualified="@" in provider_id,
            )

    if len({(row.provider.provider_id, row.plugin_id) for row in candidates}) == 1:
        return candidate_ids[0]
    raise ProviderAmbiguityError(stage, _display_candidates(candidates))
