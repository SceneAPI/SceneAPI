from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.backend_action_run_request_inputs import BackendActionRunRequestInputs


T = TypeVar("T", bound="BackendActionRunRequest")


@_attrs_define
class BackendActionRunRequest:
    """Submit a backend-native action as an sfmapi job.

    Attributes:
        project_id (str):
        inputs (BackendActionRunRequestInputs | Unset):
    """

    project_id: str
    inputs: BackendActionRunRequestInputs | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        project_id = self.project_id

        inputs: dict[str, Any] | Unset = UNSET
        if not isinstance(self.inputs, Unset):
            inputs = self.inputs.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "project_id": project_id,
            }
        )
        if inputs is not UNSET:
            field_dict["inputs"] = inputs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.backend_action_run_request_inputs import BackendActionRunRequestInputs

        d = dict(src_dict)
        project_id = d.pop("project_id")

        _inputs = d.pop("inputs", UNSET)
        inputs: BackendActionRunRequestInputs | Unset
        if isinstance(_inputs, Unset):
            inputs = UNSET
        else:
            inputs = BackendActionRunRequestInputs.from_dict(_inputs)

        backend_action_run_request = cls(
            project_id=project_id,
            inputs=inputs,
        )

        return backend_action_run_request
