from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ArtifactRef")


@_attrs_define
class ArtifactRef:
    """Reference to a stage artifact used as a downstream stage input.

    Attributes:
        artifact_id (str):
        kind (None | str | Unset): Optional expected artifact kind. The request fails if it does not match.
    """

    artifact_id: str
    kind: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        artifact_id = self.artifact_id

        kind: None | str | Unset
        if isinstance(self.kind, Unset):
            kind = UNSET
        else:
            kind = self.kind

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "artifact_id": artifact_id,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        artifact_id = d.pop("artifact_id")

        def _parse_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        kind = _parse_kind(d.pop("kind", UNSET))

        artifact_ref = cls(
            artifact_id=artifact_id,
            kind=kind,
        )

        return artifact_ref
