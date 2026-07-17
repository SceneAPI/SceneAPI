"""Compat seams of the 0.1.0 ``sfmapi`` -> ``sceneapi`` rename.

Two one-release bridges (both removed in 0.2.0):

* the ``SFMAPI_*`` env-prefix alias in :mod:`sceneapi.server.core.config`
  (legacy vars fill fields the ``SCENEAPI_*`` prefix left unset), and
* the legacy ``sfmapi.backends`` entry-point group in
  :mod:`sfm_hub.discovery` (old plugin declarations keep loading,
  deduped against the new group).

The ``sfmapi`` import-package alias itself is pinned by
``tests/unit/test_app_starts.py::test_deprecated_sfmapi_alias_shim_contract``.
"""

from __future__ import annotations

import warnings

import pytest

import sfm_hub.discovery as discovery
from sceneapi.server.core import config as config_mod

pytestmark = pytest.mark.unit


def _legacy_deprecations(caught: list[warnings.WarningMessage], needle: str):
    return [
        w for w in caught if issubclass(w.category, DeprecationWarning) and needle in str(w.message)
    ]


def test_legacy_sfmapi_env_prefix_is_honored_and_warns_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_mod, "_legacy_env_warning_emitted", False)
    monkeypatch.delenv("SCENEAPI_DEFAULT_TENANT", raising=False)
    monkeypatch.setenv("SFMAPI_DEFAULT_TENANT", "legacy-tenant")
    # New prefix must win when both spellings are set.
    monkeypatch.setenv("SCENEAPI_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("SFMAPI_LOG_LEVEL", "ERROR")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = config_mod.Settings()
        config_mod.Settings()  # second construction: no second warning

    assert settings.default_tenant == "legacy-tenant"
    assert settings.log_level == "WARNING"
    dep = _legacy_deprecations(caught, "SCENEAPI_")
    assert len(dep) == 1, [str(w.message) for w in caught]
    message = str(dep[0].message)
    assert "SFMAPI_DEFAULT_TENANT" in message
    assert "0.2.0" in message


def test_non_settings_sfmapi_env_names_do_not_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SFMAPI_SDK_REPO`` (and the plugin-manifest-owned names) are not
    Settings fields and stay legitimate this release — constructing
    Settings with only those in the environment must not deprecation-warn."""
    monkeypatch.setattr(config_mod, "_legacy_env_warning_emitted", False)
    for key in list(config_mod.os.environ):
        if key.upper().startswith("SFMAPI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SFMAPI_SDK_REPO", r"C:\somewhere\sfmapi-sdk")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config_mod.Settings()

    assert _legacy_deprecations(caught, "SCENEAPI_") == []


class _FakeEntryPoint:
    dist = None

    def __init__(self, name: str, group_tag: str) -> None:
        self.name = name
        self.value = f"fake.{name}:{group_tag}"

    def load(self):  # a bare callable factory: ep.name becomes the backend id
        return lambda: object()


class _FakeEntryPoints(list):
    def __init__(self, by_group: dict[str, list[_FakeEntryPoint]]) -> None:
        super().__init__()
        self._by_group = by_group

    def select(self, *, group: str) -> list[_FakeEntryPoint]:
        assert group in (
            discovery.ENTRY_POINT_GROUP,
            discovery.LEGACY_ENTRY_POINT_GROUP,
        )
        return list(self._by_group.get(group, []))


def test_legacy_entry_point_group_loads_dedupes_and_warns_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEntryPoints(
        {
            "sceneapi.backends": [
                _FakeEntryPoint("new_only", "new"),
                _FakeEntryPoint("both_groups", "new"),
            ],
            "sfmapi.backends": [
                _FakeEntryPoint("both_groups", "legacy"),
                _FakeEntryPoint("legacy_only", "legacy"),
            ],
        }
    )
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: fake)
    monkeypatch.setattr(discovery, "_legacy_group_warning_emitted", False)

    registered: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = discovery.load_backend_entry_points(lambda name, factory: registered.append(name))
        discovery.load_backend_entry_points(  # second sweep: no second warning
            lambda name, factory: None
        )

    # Both groups load; the plugin declared in both groups loads exactly
    # once, from its new-group declaration.
    assert sorted(registered) == ["both_groups", "legacy_only", "new_only"]
    both = [item for item in loaded if item.plugin_id == "both_groups"]
    assert len(both) == 1
    assert both[0].entry_point == "fake.both_groups:new"

    dep = _legacy_deprecations(caught, "sfmapi.backends")
    assert len(dep) == 1, [str(w.message) for w in caught]
    message = str(dep[0].message)
    assert "legacy_only" in message
    assert "0.2.0" in message


def test_new_group_only_never_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeEntryPoints({"sceneapi.backends": [_FakeEntryPoint("new_only", "new")]})
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: fake)
    monkeypatch.setattr(discovery, "_legacy_group_warning_emitted", False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        found = discovery.discover_plugins()

    assert [item.plugin_id for item in found] == ["new_only"]
    assert _legacy_deprecations(caught, "sfmapi.backends") == []
