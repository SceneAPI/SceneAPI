from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="ArtifactKindOut")


@_attrs_define
class ArtifactKindOut:
    """Documented core artifact kind.

    Attributes:
        kind (str):
        title (str):
        description (str):
        durable (bool):
    """

    kind: str
    title: str
    description: str
    durable: bool

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        title = self.title

        description = self.description

        durable = self.durable

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
                "title": title,
                "description": description,
                "durable": durable,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = d.pop("kind")

        title = d.pop("title")

        description = d.pop("description")

        durable = d.pop("durable")

        artifact_kind_out = cls(
            kind=kind,
            title=title,
            description=description,
            durable=durable,
        )

        return artifact_kind_out
