from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_action_validate_response_normalized_inputs import (
        BackendActionValidateResponseNormalizedInputs,
    )
    from ..models.backend_action_validation_error_out import BackendActionValidationErrorOut


T = TypeVar("T", bound="BackendActionValidateResponse")


@_attrs_define
class BackendActionValidateResponse:
    """
    Attributes:
        action_id (str):
        valid (bool):
        errors (list[BackendActionValidationErrorOut] | Unset):
        normalized_inputs (BackendActionValidateResponseNormalizedInputs | Unset):
    """

    action_id: str
    valid: bool
    errors: list[BackendActionValidationErrorOut] | Unset = UNSET
    normalized_inputs: BackendActionValidateResponseNormalizedInputs | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action_id = self.action_id

        valid = self.valid

        errors: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.errors, Unset):
            errors = []
            for errors_item_data in self.errors:
                errors_item = errors_item_data.to_dict()
                errors.append(errors_item)

        normalized_inputs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.normalized_inputs, Unset):
            normalized_inputs = self.normalized_inputs.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action_id": action_id,
                "valid": valid,
            }
        )
        if errors is not UNSET:
            field_dict["errors"] = errors
        if normalized_inputs is not UNSET:
            field_dict["normalized_inputs"] = normalized_inputs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_action_validate_response_normalized_inputs import (
            BackendActionValidateResponseNormalizedInputs,
        )
        from ..models.backend_action_validation_error_out import BackendActionValidationErrorOut

        d = dict(src_dict)
        action_id = d.pop("action_id")

        valid = d.pop("valid")

        _errors = d.pop("errors", UNSET)
        errors: list[BackendActionValidationErrorOut] | Unset = UNSET
        if _errors is not UNSET:
            errors = []
            for errors_item_data in _errors:
                errors_item = BackendActionValidationErrorOut.from_dict(errors_item_data)

                errors.append(errors_item)

        _normalized_inputs = d.pop("normalized_inputs", UNSET)
        normalized_inputs: BackendActionValidateResponseNormalizedInputs | Unset
        if isinstance(_normalized_inputs, Unset):
            normalized_inputs = UNSET
        else:
            normalized_inputs = BackendActionValidateResponseNormalizedInputs.from_dict(
                _normalized_inputs
            )

        backend_action_validate_response = cls(
            action_id=action_id,
            valid=valid,
            errors=errors,
            normalized_inputs=normalized_inputs,
        )

        backend_action_validate_response.additional_properties = d
        return backend_action_validate_response

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
