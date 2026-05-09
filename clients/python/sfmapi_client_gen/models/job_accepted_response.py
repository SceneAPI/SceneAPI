from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.sim_3 import Sim3


T = TypeVar("T", bound="JobAcceptedResponse")


@_attrs_define
class JobAcceptedResponse:
    """Canonical 202 envelope for endpoints that submit a Job.

    Returned by every ``POST`` that enqueues SfM work
    (`/datasets/{id}/features`, `/matches`, `/verify`, the
    `/pipelines/{recipe}` recipes, and the localize / dense / mesh /
    cubemap stages). Clients should follow ``Location`` to ``GET
    /v1/jobs/{job_id}`` for status.

    Stage-specific optional fields are typed here so SDK codegen can
    surface them as named accessors:

    - ``recon_id`` — endpoints nested under a reconstruction
    - ``dataset_id`` / ``project_id`` — parent-pointer for top-level routes
    - ``method`` — mesh submission method
    - ``applied_sim3`` — georegister applied transform
    - ``target_recon_id`` / ``source_recon_ids`` — ``reconstructions:merge``
    - ``strategy`` — ``similarity:build``
    - ``action_id`` / ``backend`` — backend-native extension actions

        Attributes:
            job_id (str):
            task_ids (list[str] | Unset):
            recon_id (None | str | Unset):
            dataset_id (None | str | Unset):
            project_id (None | str | Unset):
            method (None | str | Unset):
            applied_sim3 (None | Sim3 | Unset):
            target_recon_id (None | str | Unset):
            source_recon_ids (list[str] | None | Unset):
            strategy (None | str | Unset):
            action_id (None | str | Unset):
            backend (None | str | Unset):
    """

    job_id: str
    task_ids: list[str] | Unset = UNSET
    recon_id: None | str | Unset = UNSET
    dataset_id: None | str | Unset = UNSET
    project_id: None | str | Unset = UNSET
    method: None | str | Unset = UNSET
    applied_sim3: None | Sim3 | Unset = UNSET
    target_recon_id: None | str | Unset = UNSET
    source_recon_ids: list[str] | None | Unset = UNSET
    strategy: None | str | Unset = UNSET
    action_id: None | str | Unset = UNSET
    backend: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.sim_3 import Sim3

        job_id = self.job_id

        task_ids: list[str] | Unset = UNSET
        if not isinstance(self.task_ids, Unset):
            task_ids = self.task_ids

        recon_id: None | str | Unset
        if isinstance(self.recon_id, Unset):
            recon_id = UNSET
        else:
            recon_id = self.recon_id

        dataset_id: None | str | Unset
        if isinstance(self.dataset_id, Unset):
            dataset_id = UNSET
        else:
            dataset_id = self.dataset_id

        project_id: None | str | Unset
        if isinstance(self.project_id, Unset):
            project_id = UNSET
        else:
            project_id = self.project_id

        method: None | str | Unset
        if isinstance(self.method, Unset):
            method = UNSET
        else:
            method = self.method

        applied_sim3: dict[str, Any] | None | Unset
        if isinstance(self.applied_sim3, Unset):
            applied_sim3 = UNSET
        elif isinstance(self.applied_sim3, Sim3):
            applied_sim3 = self.applied_sim3.to_dict()
        else:
            applied_sim3 = self.applied_sim3

        target_recon_id: None | str | Unset
        if isinstance(self.target_recon_id, Unset):
            target_recon_id = UNSET
        else:
            target_recon_id = self.target_recon_id

        source_recon_ids: list[str] | None | Unset
        if isinstance(self.source_recon_ids, Unset):
            source_recon_ids = UNSET
        elif isinstance(self.source_recon_ids, list):
            source_recon_ids = self.source_recon_ids

        else:
            source_recon_ids = self.source_recon_ids

        strategy: None | str | Unset
        if isinstance(self.strategy, Unset):
            strategy = UNSET
        else:
            strategy = self.strategy

        action_id: None | str | Unset
        if isinstance(self.action_id, Unset):
            action_id = UNSET
        else:
            action_id = self.action_id

        backend: None | str | Unset
        if isinstance(self.backend, Unset):
            backend = UNSET
        else:
            backend = self.backend

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
            }
        )
        if task_ids is not UNSET:
            field_dict["task_ids"] = task_ids
        if recon_id is not UNSET:
            field_dict["recon_id"] = recon_id
        if dataset_id is not UNSET:
            field_dict["dataset_id"] = dataset_id
        if project_id is not UNSET:
            field_dict["project_id"] = project_id
        if method is not UNSET:
            field_dict["method"] = method
        if applied_sim3 is not UNSET:
            field_dict["applied_sim3"] = applied_sim3
        if target_recon_id is not UNSET:
            field_dict["target_recon_id"] = target_recon_id
        if source_recon_ids is not UNSET:
            field_dict["source_recon_ids"] = source_recon_ids
        if strategy is not UNSET:
            field_dict["strategy"] = strategy
        if action_id is not UNSET:
            field_dict["action_id"] = action_id
        if backend is not UNSET:
            field_dict["backend"] = backend

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.sim_3 import Sim3

        d = dict(src_dict)
        job_id = d.pop("job_id")

        task_ids = cast(list[str], d.pop("task_ids", UNSET))

        def _parse_recon_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        recon_id = _parse_recon_id(d.pop("recon_id", UNSET))

        def _parse_dataset_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dataset_id = _parse_dataset_id(d.pop("dataset_id", UNSET))

        def _parse_project_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        project_id = _parse_project_id(d.pop("project_id", UNSET))

        def _parse_method(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        method = _parse_method(d.pop("method", UNSET))

        def _parse_applied_sim3(data: object) -> None | Sim3 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                applied_sim3_type_0 = Sim3.from_dict(data)

                return applied_sim3_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Sim3 | Unset, data)

        applied_sim3 = _parse_applied_sim3(d.pop("applied_sim3", UNSET))

        def _parse_target_recon_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        target_recon_id = _parse_target_recon_id(d.pop("target_recon_id", UNSET))

        def _parse_source_recon_ids(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                source_recon_ids_type_0 = cast(list[str], data)

                return source_recon_ids_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        source_recon_ids = _parse_source_recon_ids(d.pop("source_recon_ids", UNSET))

        def _parse_strategy(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        strategy = _parse_strategy(d.pop("strategy", UNSET))

        def _parse_action_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        action_id = _parse_action_id(d.pop("action_id", UNSET))

        def _parse_backend(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        backend = _parse_backend(d.pop("backend", UNSET))

        job_accepted_response = cls(
            job_id=job_id,
            task_ids=task_ids,
            recon_id=recon_id,
            dataset_id=dataset_id,
            project_id=project_id,
            method=method,
            applied_sim3=applied_sim3,
            target_recon_id=target_recon_id,
            source_recon_ids=source_recon_ids,
            strategy=strategy,
            action_id=action_id,
            backend=backend,
        )

        job_accepted_response.additional_properties = d
        return job_accepted_response

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
