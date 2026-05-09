from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.spherical_spec_backend_options import SphericalSpecBackendOptions


T = TypeVar("T", bound="SphericalSpec")


@_attrs_define
class SphericalSpec:
    """
    Attributes:
        version (Literal[1] | Unset):  Default: 1.
        provider (None | str | Unset): Optional backend implementation selector when more than one registered provider
            can run the same portable mapping recipe.
        seed (int | Unset):  Default: 0.
        max_runtime_seconds (int | None | Unset):
        snapshot_frames_freq (int | None | Unset):  Default: 50.
        backend_options (SphericalSpecBackendOptions | Unset): Backend-specific mapping options. Discover supported keys
            with GET /v1/backend/config-schemas and keep portable settings in the top-level spec fields.
        kind (Literal['spherical'] | Unset):  Default: 'spherical'.
        panorama (bool | Unset):  Default: True.
    """

    version: Literal[1] | Unset = 1
    provider: None | str | Unset = UNSET
    seed: int | Unset = 0
    max_runtime_seconds: int | None | Unset = UNSET
    snapshot_frames_freq: int | None | Unset = 50
    backend_options: SphericalSpecBackendOptions | Unset = UNSET
    kind: Literal["spherical"] | Unset = "spherical"
    panorama: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        seed = self.seed

        max_runtime_seconds: int | None | Unset
        if isinstance(self.max_runtime_seconds, Unset):
            max_runtime_seconds = UNSET
        else:
            max_runtime_seconds = self.max_runtime_seconds

        snapshot_frames_freq: int | None | Unset
        if isinstance(self.snapshot_frames_freq, Unset):
            snapshot_frames_freq = UNSET
        else:
            snapshot_frames_freq = self.snapshot_frames_freq

        backend_options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.backend_options, Unset):
            backend_options = self.backend_options.to_dict()

        kind = self.kind

        panorama = self.panorama

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if version is not UNSET:
            field_dict["version"] = version
        if provider is not UNSET:
            field_dict["provider"] = provider
        if seed is not UNSET:
            field_dict["seed"] = seed
        if max_runtime_seconds is not UNSET:
            field_dict["max_runtime_seconds"] = max_runtime_seconds
        if snapshot_frames_freq is not UNSET:
            field_dict["snapshot_frames_freq"] = snapshot_frames_freq
        if backend_options is not UNSET:
            field_dict["backend_options"] = backend_options
        if kind is not UNSET:
            field_dict["kind"] = kind
        if panorama is not UNSET:
            field_dict["panorama"] = panorama

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.spherical_spec_backend_options import SphericalSpecBackendOptions

        d = dict(src_dict)
        version = cast(Literal[1] | Unset, d.pop("version", UNSET))
        if version != 1 and not isinstance(version, Unset):
            raise ValueError(f"version must match const 1, got '{version}'")

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        seed = d.pop("seed", UNSET)

        def _parse_max_runtime_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_runtime_seconds = _parse_max_runtime_seconds(d.pop("max_runtime_seconds", UNSET))

        def _parse_snapshot_frames_freq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        snapshot_frames_freq = _parse_snapshot_frames_freq(d.pop("snapshot_frames_freq", UNSET))

        _backend_options = d.pop("backend_options", UNSET)
        backend_options: SphericalSpecBackendOptions | Unset
        if isinstance(_backend_options, Unset):
            backend_options = UNSET
        else:
            backend_options = SphericalSpecBackendOptions.from_dict(_backend_options)

        kind = cast(Literal["spherical"] | Unset, d.pop("kind", UNSET))
        if kind != "spherical" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'spherical', got '{kind}'")

        panorama = d.pop("panorama", UNSET)

        spherical_spec = cls(
            version=version,
            provider=provider,
            seed=seed,
            max_runtime_seconds=max_runtime_seconds,
            snapshot_frames_freq=snapshot_frames_freq,
            backend_options=backend_options,
            kind=kind,
            panorama=panorama,
        )

        spherical_spec.additional_properties = d
        return spherical_spec

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
