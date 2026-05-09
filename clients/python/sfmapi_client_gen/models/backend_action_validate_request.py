from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_action_validate_request_inputs import BackendActionValidateRequestInputs


T = TypeVar("T", bound="BackendActionValidateRequest")


@_attrs_define
class BackendActionValidateRequest:
    """Validate action input without submitting work.

    Attributes:
        inputs (BackendActionValidateRequestInputs | Unset):
    """

    inputs: BackendActionValidateRequestInputs | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        inputs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.inputs, Unset):
            inputs = self.inputs.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if inputs is not UNSET:
            field_dict["inputs"] = inputs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_action_validate_request_inputs import (
            BackendActionValidateRequestInputs,
        )

        d = dict(src_dict)
        _inputs = d.pop("inputs", UNSET)
        inputs: BackendActionValidateRequestInputs | Unset
        if isinstance(_inputs, Unset):
            inputs = UNSET
        else:
            inputs = BackendActionValidateRequestInputs.from_dict(_inputs)

        backend_action_validate_request = cls(
            inputs=inputs,
        )

        return backend_action_validate_request
