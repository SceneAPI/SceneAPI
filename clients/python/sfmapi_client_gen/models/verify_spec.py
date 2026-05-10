from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.verify_spec_backend_options import VerifySpecBackendOptions
    from ..models.verify_spec_input_artifacts import VerifySpecInputArtifacts


T = TypeVar("T", bound="VerifySpec")


@_attrs_define
class VerifySpec:
    """
    Attributes:
        version (Literal[1] | Unset):  Default: 1.
        provider (None | str | Unset): Optional backend implementation selector for geometric verification when multiple
            providers expose matches.verify.
        use_gpu (bool | Unset):  Default: True.
        min_inlier_ratio (float | Unset):  Default: 0.25.
        backend_options (VerifySpecBackendOptions | Unset): Backend-specific geometric-verification options. Discover
            supported keys with GET /v1/backend/config-schemas.
        input_artifacts (VerifySpecInputArtifacts | Unset): Optional role-keyed input artifact references. Use role
            'matches' to verify a specific match artifact.
    """

    version: Literal[1] | Unset = 1
    provider: None | str | Unset = UNSET
    use_gpu: bool | Unset = True
    min_inlier_ratio: float | Unset = 0.25
    backend_options: VerifySpecBackendOptions | Unset = UNSET
    input_artifacts: VerifySpecInputArtifacts | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        use_gpu = self.use_gpu

        min_inlier_ratio = self.min_inlier_ratio

        backend_options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.backend_options, Unset):
            backend_options = self.backend_options.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if version is not UNSET:
            field_dict["version"] = version
        if provider is not UNSET:
            field_dict["provider"] = provider
        if use_gpu is not UNSET:
            field_dict["use_gpu"] = use_gpu
        if min_inlier_ratio is not UNSET:
            field_dict["min_inlier_ratio"] = min_inlier_ratio
        if backend_options is not UNSET:
            field_dict["backend_options"] = backend_options
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.verify_spec_backend_options import VerifySpecBackendOptions
        from ..models.verify_spec_input_artifacts import VerifySpecInputArtifacts

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

        use_gpu = d.pop("use_gpu", UNSET)

        min_inlier_ratio = d.pop("min_inlier_ratio", UNSET)

        _backend_options = d.pop("backend_options", UNSET)
        backend_options: VerifySpecBackendOptions | Unset
        if isinstance(_backend_options, Unset):
            backend_options = UNSET
        else:
            backend_options = VerifySpecBackendOptions.from_dict(_backend_options)

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: VerifySpecInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = VerifySpecInputArtifacts.from_dict(_input_artifacts)

        verify_spec = cls(
            version=version,
            provider=provider,
            use_gpu=use_gpu,
            min_inlier_ratio=min_inlier_ratio,
            backend_options=backend_options,
            input_artifacts=input_artifacts,
        )

        verify_spec.additional_properties = d
        return verify_spec

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
