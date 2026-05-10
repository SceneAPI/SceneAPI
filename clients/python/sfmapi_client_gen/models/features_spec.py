from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.features_spec_type import FeaturesSpecType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.features_spec_backend_options import FeaturesSpecBackendOptions
    from ..models.features_spec_extractor_options import FeaturesSpecExtractorOptions
    from ..models.features_spec_input_artifacts import FeaturesSpecInputArtifacts


T = TypeVar("T", bound="FeaturesSpec")


@_attrs_define
class FeaturesSpec:
    """Type-tagged feature extractor request.

    Backends report which ``type`` values they support via the
    ``features.extract.{type}`` capability flags. Unsupported types
    return 501 with the canonical capability name.

    Backwards compat: the legacy ``sift_max_num_features`` /
    ``sift_first_octave`` fields are accepted as aliases when
    ``type=="sift"``.

        Attributes:
            version (Literal[1] | Unset):  Default: 1.
            type_ (FeaturesSpecType | Unset):  Default: FeaturesSpecType.SIFT.
            provider (None | str | Unset): Optional backend implementation selector when more than one registered provider
                can run the same feature type, for example 'colmap' or 'hloc'. Portable capability checks still use type.
            max_num_features (int | Unset):  Default: 8192.
            use_gpu (bool | Unset):  Default: True.
            seed (int | Unset):  Default: 0.
            backend_options (FeaturesSpecBackendOptions | Unset): Backend-specific feature-extraction options. Discover
                supported keys with GET /v1/backend/config-schemas.
            input_artifacts (FeaturesSpecInputArtifacts | Unset): Optional role-keyed input artifact references for advanced
                or backend-specific feature extraction flows.
            extractor_options (FeaturesSpecExtractorOptions | Unset): Deprecated compatibility alias for backend-specific
                extractor options. Prefer backend_options.
            sift_max_num_features (int | None | Unset):
            sift_first_octave (int | None | Unset):
    """

    version: Literal[1] | Unset = 1
    type_: FeaturesSpecType | Unset = FeaturesSpecType.SIFT
    provider: None | str | Unset = UNSET
    max_num_features: int | Unset = 8192
    use_gpu: bool | Unset = True
    seed: int | Unset = 0
    backend_options: FeaturesSpecBackendOptions | Unset = UNSET
    input_artifacts: FeaturesSpecInputArtifacts | Unset = UNSET
    extractor_options: FeaturesSpecExtractorOptions | Unset = UNSET
    sift_max_num_features: int | None | Unset = UNSET
    sift_first_octave: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        type_: str | Unset = UNSET
        if not isinstance(self.type_, Unset):
            type_ = self.type_.value

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        max_num_features = self.max_num_features

        use_gpu = self.use_gpu

        seed = self.seed

        backend_options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.backend_options, Unset):
            backend_options = self.backend_options.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        extractor_options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.extractor_options, Unset):
            extractor_options = self.extractor_options.to_dict()

        sift_max_num_features: int | None | Unset
        if isinstance(self.sift_max_num_features, Unset):
            sift_max_num_features = UNSET
        else:
            sift_max_num_features = self.sift_max_num_features

        sift_first_octave: int | None | Unset
        if isinstance(self.sift_first_octave, Unset):
            sift_first_octave = UNSET
        else:
            sift_first_octave = self.sift_first_octave

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if version is not UNSET:
            field_dict["version"] = version
        if type_ is not UNSET:
            field_dict["type"] = type_
        if provider is not UNSET:
            field_dict["provider"] = provider
        if max_num_features is not UNSET:
            field_dict["max_num_features"] = max_num_features
        if use_gpu is not UNSET:
            field_dict["use_gpu"] = use_gpu
        if seed is not UNSET:
            field_dict["seed"] = seed
        if backend_options is not UNSET:
            field_dict["backend_options"] = backend_options
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts
        if extractor_options is not UNSET:
            field_dict["extractor_options"] = extractor_options
        if sift_max_num_features is not UNSET:
            field_dict["sift_max_num_features"] = sift_max_num_features
        if sift_first_octave is not UNSET:
            field_dict["sift_first_octave"] = sift_first_octave

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.features_spec_backend_options import FeaturesSpecBackendOptions
        from ..models.features_spec_extractor_options import FeaturesSpecExtractorOptions
        from ..models.features_spec_input_artifacts import FeaturesSpecInputArtifacts

        d = dict(src_dict)
        version = cast(Literal[1] | Unset, d.pop("version", UNSET))
        if version != 1 and not isinstance(version, Unset):
            raise ValueError(f"version must match const 1, got '{version}'")

        _type_ = d.pop("type", UNSET)
        type_: FeaturesSpecType | Unset
        if isinstance(_type_, Unset):
            type_ = UNSET
        else:
            type_ = FeaturesSpecType(_type_)

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        max_num_features = d.pop("max_num_features", UNSET)

        use_gpu = d.pop("use_gpu", UNSET)

        seed = d.pop("seed", UNSET)

        _backend_options = d.pop("backend_options", UNSET)
        backend_options: FeaturesSpecBackendOptions | Unset
        if isinstance(_backend_options, Unset):
            backend_options = UNSET
        else:
            backend_options = FeaturesSpecBackendOptions.from_dict(_backend_options)

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: FeaturesSpecInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = FeaturesSpecInputArtifacts.from_dict(_input_artifacts)

        _extractor_options = d.pop("extractor_options", UNSET)
        extractor_options: FeaturesSpecExtractorOptions | Unset
        if isinstance(_extractor_options, Unset):
            extractor_options = UNSET
        else:
            extractor_options = FeaturesSpecExtractorOptions.from_dict(_extractor_options)

        def _parse_sift_max_num_features(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        sift_max_num_features = _parse_sift_max_num_features(d.pop("sift_max_num_features", UNSET))

        def _parse_sift_first_octave(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        sift_first_octave = _parse_sift_first_octave(d.pop("sift_first_octave", UNSET))

        features_spec = cls(
            version=version,
            type_=type_,
            provider=provider,
            max_num_features=max_num_features,
            use_gpu=use_gpu,
            seed=seed,
            backend_options=backend_options,
            input_artifacts=input_artifacts,
            extractor_options=extractor_options,
            sift_max_num_features=sift_max_num_features,
            sift_first_octave=sift_first_octave,
        )

        features_spec.additional_properties = d
        return features_spec

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
