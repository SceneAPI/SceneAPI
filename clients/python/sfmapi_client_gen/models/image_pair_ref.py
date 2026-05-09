from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ImagePairRef")


@_attrs_define
class ImagePairRef:
    """One explicit pair of dataset image names.

    Attributes:
        image_name1 (str):
        image_name2 (str):
    """

    image_name1: str
    image_name2: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        image_name1 = self.image_name1

        image_name2 = self.image_name2

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "image_name1": image_name1,
                "image_name2": image_name2,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        image_name1 = d.pop("image_name1")

        image_name2 = d.pop("image_name2")

        image_pair_ref = cls(
            image_name1=image_name1,
            image_name2=image_name2,
        )

        image_pair_ref.additional_properties = d
        return image_pair_ref

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
