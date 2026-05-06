from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_version import BackendVersion


T = TypeVar("T", bound="VersionResponse")


@_attrs_define
class VersionResponse:
    """
    Attributes:
        sfmapi (str):
        backend (BackendVersion | None | Unset):
    """

    sfmapi: str
    backend: BackendVersion | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.backend_version import BackendVersion

        sfmapi = self.sfmapi

        backend: dict[str, Any] | None | Unset
        if isinstance(self.backend, Unset):
            backend = UNSET
        elif isinstance(self.backend, BackendVersion):
            backend = self.backend.to_dict()
        else:
            backend = self.backend

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "sfmapi": sfmapi,
            }
        )
        if backend is not UNSET:
            field_dict["backend"] = backend

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_version import BackendVersion

        d = dict(src_dict)
        sfmapi = d.pop("sfmapi")

        def _parse_backend(data: object) -> BackendVersion | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                backend_type_0 = BackendVersion.from_dict(data)

                return backend_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BackendVersion | None | Unset, data)

        backend = _parse_backend(d.pop("backend", UNSET))

        version_response = cls(
            sfmapi=sfmapi,
            backend=backend,
        )

        version_response.additional_properties = d
        return version_response

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
