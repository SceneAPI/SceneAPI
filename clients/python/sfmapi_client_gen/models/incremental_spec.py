from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.incremental_spec_backend_options import IncrementalSpecBackendOptions


T = TypeVar("T", bound="IncrementalSpec")


@_attrs_define
class IncrementalSpec:
    """
    Attributes:
        version (Literal[1] | Unset):  Default: 1.
        provider (None | str | Unset): Optional backend implementation selector when more than one registered provider
            can run the same portable mapping recipe.
        seed (int | Unset):  Default: 0.
        max_runtime_seconds (int | None | Unset):
        snapshot_frames_freq (int | None | Unset):  Default: 50.
        backend_options (IncrementalSpecBackendOptions | Unset): Backend-specific mapping options. Discover supported
            keys with GET /v1/backend/config-schemas and keep portable settings in the top-level spec fields.
        kind (Literal['incremental'] | Unset):  Default: 'incremental'.
        init_image_pair (list[str] | None | Unset):
        multiple_models (bool | Unset):  Default: True.
        max_num_models (int | Unset):  Default: 50.
        min_num_matches (int | Unset):  Default: 15.
        ba_global_use_pba (bool | Unset):  Default: True.
        extract_colors (bool | Unset):  Default: True.
    """

    version: Literal[1] | Unset = 1
    provider: None | str | Unset = UNSET
    seed: int | Unset = 0
    max_runtime_seconds: int | None | Unset = UNSET
    snapshot_frames_freq: int | None | Unset = 50
    backend_options: IncrementalSpecBackendOptions | Unset = UNSET
    kind: Literal["incremental"] | Unset = "incremental"
    init_image_pair: list[str] | None | Unset = UNSET
    multiple_models: bool | Unset = True
    max_num_models: int | Unset = 50
    min_num_matches: int | Unset = 15
    ba_global_use_pba: bool | Unset = True
    extract_colors: bool | Unset = True
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

        init_image_pair: list[str] | None | Unset
        if isinstance(self.init_image_pair, Unset):
            init_image_pair = UNSET
        elif isinstance(self.init_image_pair, list):
            init_image_pair = []
            for init_image_pair_type_0_item_data in self.init_image_pair:
                init_image_pair_type_0_item: str
                init_image_pair_type_0_item = init_image_pair_type_0_item_data
                init_image_pair.append(init_image_pair_type_0_item)

        else:
            init_image_pair = self.init_image_pair

        multiple_models = self.multiple_models

        max_num_models = self.max_num_models

        min_num_matches = self.min_num_matches

        ba_global_use_pba = self.ba_global_use_pba

        extract_colors = self.extract_colors

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
        if init_image_pair is not UNSET:
            field_dict["init_image_pair"] = init_image_pair
        if multiple_models is not UNSET:
            field_dict["multiple_models"] = multiple_models
        if max_num_models is not UNSET:
            field_dict["max_num_models"] = max_num_models
        if min_num_matches is not UNSET:
            field_dict["min_num_matches"] = min_num_matches
        if ba_global_use_pba is not UNSET:
            field_dict["ba_global_use_pba"] = ba_global_use_pba
        if extract_colors is not UNSET:
            field_dict["extract_colors"] = extract_colors

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.incremental_spec_backend_options import IncrementalSpecBackendOptions

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
        backend_options: IncrementalSpecBackendOptions | Unset
        if isinstance(_backend_options, Unset):
            backend_options = UNSET
        else:
            backend_options = IncrementalSpecBackendOptions.from_dict(_backend_options)

        kind = cast(Literal["incremental"] | Unset, d.pop("kind", UNSET))
        if kind != "incremental" and not isinstance(kind, Unset):
            raise ValueError(f"kind must match const 'incremental', got '{kind}'")

        def _parse_init_image_pair(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                init_image_pair_type_0 = []
                _init_image_pair_type_0 = data
                for init_image_pair_type_0_item_data in _init_image_pair_type_0:

                    def _parse_init_image_pair_type_0_item(data: object) -> str:
                        return cast(str, data)

                    init_image_pair_type_0_item = _parse_init_image_pair_type_0_item(
                        init_image_pair_type_0_item_data
                    )

                    init_image_pair_type_0.append(init_image_pair_type_0_item)

                return init_image_pair_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        init_image_pair = _parse_init_image_pair(d.pop("init_image_pair", UNSET))

        multiple_models = d.pop("multiple_models", UNSET)

        max_num_models = d.pop("max_num_models", UNSET)

        min_num_matches = d.pop("min_num_matches", UNSET)

        ba_global_use_pba = d.pop("ba_global_use_pba", UNSET)

        extract_colors = d.pop("extract_colors", UNSET)

        incremental_spec = cls(
            version=version,
            provider=provider,
            seed=seed,
            max_runtime_seconds=max_runtime_seconds,
            snapshot_frames_freq=snapshot_frames_freq,
            backend_options=backend_options,
            kind=kind,
            init_image_pair=init_image_pair,
            multiple_models=multiple_models,
            max_num_models=max_num_models,
            min_num_matches=min_num_matches,
            ba_global_use_pba=ba_global_use_pba,
            extract_colors=extract_colors,
        )

        incremental_spec.additional_properties = d
        return incremental_spec

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
