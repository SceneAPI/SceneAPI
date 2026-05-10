from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.verify_request_input_artifacts import VerifyRequestInputArtifacts
    from ..models.verify_spec import VerifySpec


T = TypeVar("T", bound="VerifyRequest")


@_attrs_define
class VerifyRequest:
    """
    Attributes:
        spec (VerifySpec | Unset):
        input_artifacts (VerifyRequestInputArtifacts | Unset):
    """

    spec: VerifySpec | Unset = UNSET
    input_artifacts: VerifyRequestInputArtifacts | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        spec: dict[str, Any] | Unset = UNSET
        if not isinstance(self.spec, Unset):
            spec = self.spec.to_dict()

        input_artifacts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_artifacts, Unset):
            input_artifacts = self.input_artifacts.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if spec is not UNSET:
            field_dict["spec"] = spec
        if input_artifacts is not UNSET:
            field_dict["input_artifacts"] = input_artifacts

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.verify_request_input_artifacts import VerifyRequestInputArtifacts
        from ..models.verify_spec import VerifySpec

        d = dict(src_dict)
        _spec = d.pop("spec", UNSET)
        spec: VerifySpec | Unset
        if isinstance(_spec, Unset):
            spec = UNSET
        else:
            spec = VerifySpec.from_dict(_spec)

        _input_artifacts = d.pop("input_artifacts", UNSET)
        input_artifacts: VerifyRequestInputArtifacts | Unset
        if isinstance(_input_artifacts, Unset):
            input_artifacts = UNSET
        else:
            input_artifacts = VerifyRequestInputArtifacts.from_dict(_input_artifacts)

        verify_request = cls(
            spec=spec,
            input_artifacts=input_artifacts,
        )

        return verify_request
