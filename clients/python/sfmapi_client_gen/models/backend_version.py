from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_version_runtime_versions import BackendVersionRuntimeVersions


T = TypeVar("T", bound="BackendVersion")


@_attrs_define
class BackendVersion:
    """Backend identity + freeform engine version map.

    sfmapi has no concrete backend; whatever real backend is
    registered fills in its own ``runtime_versions`` keys (e.g.
    ``{"colmap_sha": "...", "cuda_arch": "120"}``). ``None`` when
    no backend is registered.

        Attributes:
            name (str):
            version (str):
            vendor (None | str | Unset):
            runtime_versions (BackendVersionRuntimeVersions | Unset):
    """

    name: str
    version: str
    vendor: None | str | Unset = UNSET
    runtime_versions: BackendVersionRuntimeVersions | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        version = self.version

        vendor: None | str | Unset
        if isinstance(self.vendor, Unset):
            vendor = UNSET
        else:
            vendor = self.vendor

        runtime_versions: dict[str, Any] | Unset = UNSET
        if not isinstance(self.runtime_versions, Unset):
            runtime_versions = self.runtime_versions.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "version": version,
            }
        )
        if vendor is not UNSET:
            field_dict["vendor"] = vendor
        if runtime_versions is not UNSET:
            field_dict["runtime_versions"] = runtime_versions

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_version_runtime_versions import BackendVersionRuntimeVersions

        d = dict(src_dict)
        name = d.pop("name")

        version = d.pop("version")

        def _parse_vendor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        vendor = _parse_vendor(d.pop("vendor", UNSET))

        _runtime_versions = d.pop("runtime_versions", UNSET)
        runtime_versions: BackendVersionRuntimeVersions | Unset
        if isinstance(_runtime_versions, Unset):
            runtime_versions = UNSET
        else:
            runtime_versions = BackendVersionRuntimeVersions.from_dict(_runtime_versions)

        backend_version = cls(
            name=name,
            version=version,
            vendor=vendor,
            runtime_versions=runtime_versions,
        )

        backend_version.additional_properties = d
        return backend_version

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
