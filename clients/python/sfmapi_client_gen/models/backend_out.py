from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_out_links_type_0 import BackendOutLinksType0
    from ..models.backend_out_runtime_versions import BackendOutRuntimeVersions


T = TypeVar("T", bound="BackendOut")


@_attrs_define
class BackendOut:
    """Active backend summary plus extension-action availability.

    Attributes:
        name (str):
        version (str):
        vendor (str | Unset):  Default: ''.
        runtime_versions (BackendOutRuntimeVersions | Unset):
        action_count (int | Unset):  Default: 0.
        config_schema_count (int | Unset):  Default: 0.
        field_links (BackendOutLinksType0 | None | Unset):
    """

    name: str
    version: str
    vendor: str | Unset = ""
    runtime_versions: BackendOutRuntimeVersions | Unset = UNSET
    action_count: int | Unset = 0
    config_schema_count: int | Unset = 0
    field_links: BackendOutLinksType0 | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.backend_out_links_type_0 import BackendOutLinksType0

        name = self.name

        version = self.version

        vendor = self.vendor

        runtime_versions: dict[str, Any] | Unset = UNSET
        if not isinstance(self.runtime_versions, Unset):
            runtime_versions = self.runtime_versions.to_dict()

        action_count = self.action_count

        config_schema_count = self.config_schema_count

        field_links: dict[str, Any] | None | Unset
        if isinstance(self.field_links, Unset):
            field_links = UNSET
        elif isinstance(self.field_links, BackendOutLinksType0):
            field_links = self.field_links.to_dict()
        else:
            field_links = self.field_links

        field_dict: dict[str, Any] = {}

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
        if action_count is not UNSET:
            field_dict["action_count"] = action_count
        if config_schema_count is not UNSET:
            field_dict["config_schema_count"] = config_schema_count
        if field_links is not UNSET:
            field_dict["_links"] = field_links

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_out_links_type_0 import BackendOutLinksType0
        from ..models.backend_out_runtime_versions import BackendOutRuntimeVersions

        d = dict(src_dict)
        name = d.pop("name")

        version = d.pop("version")

        vendor = d.pop("vendor", UNSET)

        _runtime_versions = d.pop("runtime_versions", UNSET)
        runtime_versions: BackendOutRuntimeVersions | Unset
        if isinstance(_runtime_versions, Unset):
            runtime_versions = UNSET
        else:
            runtime_versions = BackendOutRuntimeVersions.from_dict(_runtime_versions)

        action_count = d.pop("action_count", UNSET)

        config_schema_count = d.pop("config_schema_count", UNSET)

        def _parse_field_links(data: object) -> BackendOutLinksType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                field_links_type_0 = BackendOutLinksType0.from_dict(data)

                return field_links_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendOutLinksType0 | None | Unset, data)

        field_links = _parse_field_links(d.pop("_links", UNSET))

        backend_out = cls(
            name=name,
            version=version,
            vendor=vendor,
            runtime_versions=runtime_versions,
            action_count=action_count,
            config_schema_count=config_schema_count,
            field_links=field_links,
        )

        return backend_out
