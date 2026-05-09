from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.job_progress_out_status import JobProgressOutStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.job_progress_out_latest_event_type_0 import JobProgressOutLatestEventType0
    from ..models.job_progress_out_task_counts import JobProgressOutTaskCounts
    from ..models.task_progress_out import TaskProgressOut


T = TypeVar("T", bound="JobProgressOut")


@_attrs_define
class JobProgressOut:
    """Compact polling snapshot for job progress.

    This endpoint complements ``/events`` for dashboards and CLIs that
    prefer polling over holding an SSE connection open.

        Attributes:
            job_id (str):
            recipe (str):
            status (JobProgressOutStatus):
            progress (float):
            total_tasks (int):
            completed_tasks (int):
            task_counts (JobProgressOutTaskCounts):
            current_task_id (None | str | Unset):
            current_task_kind (None | str | Unset):
            current_phase (None | str | Unset):
            latest_event_id (int | None | Unset):
            latest_event (JobProgressOutLatestEventType0 | None | Unset):
            tasks (list[TaskProgressOut] | Unset):
    """

    job_id: str
    recipe: str
    status: JobProgressOutStatus
    progress: float
    total_tasks: int
    completed_tasks: int
    task_counts: JobProgressOutTaskCounts
    current_task_id: None | str | Unset = UNSET
    current_task_kind: None | str | Unset = UNSET
    current_phase: None | str | Unset = UNSET
    latest_event_id: int | None | Unset = UNSET
    latest_event: JobProgressOutLatestEventType0 | None | Unset = UNSET
    tasks: list[TaskProgressOut] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.job_progress_out_latest_event_type_0 import JobProgressOutLatestEventType0

        job_id = self.job_id

        recipe = self.recipe

        status = self.status.value

        progress = self.progress

        total_tasks = self.total_tasks

        completed_tasks = self.completed_tasks

        task_counts = self.task_counts.to_dict()

        current_task_id: None | str | Unset
        if isinstance(self.current_task_id, Unset):
            current_task_id = UNSET
        else:
            current_task_id = self.current_task_id

        current_task_kind: None | str | Unset
        if isinstance(self.current_task_kind, Unset):
            current_task_kind = UNSET
        else:
            current_task_kind = self.current_task_kind

        current_phase: None | str | Unset
        if isinstance(self.current_phase, Unset):
            current_phase = UNSET
        else:
            current_phase = self.current_phase

        latest_event_id: int | None | Unset
        if isinstance(self.latest_event_id, Unset):
            latest_event_id = UNSET
        else:
            latest_event_id = self.latest_event_id

        latest_event: dict[str, Any] | None | Unset
        if isinstance(self.latest_event, Unset):
            latest_event = UNSET
        elif isinstance(self.latest_event, JobProgressOutLatestEventType0):
            latest_event = self.latest_event.to_dict()
        else:
            latest_event = self.latest_event

        tasks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.tasks, Unset):
            tasks = []
            for tasks_item_data in self.tasks:
                tasks_item = tasks_item_data.to_dict()
                tasks.append(tasks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "job_id": job_id,
                "recipe": recipe,
                "status": status,
                "progress": progress,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "task_counts": task_counts,
            }
        )
        if current_task_id is not UNSET:
            field_dict["current_task_id"] = current_task_id
        if current_task_kind is not UNSET:
            field_dict["current_task_kind"] = current_task_kind
        if current_phase is not UNSET:
            field_dict["current_phase"] = current_phase
        if latest_event_id is not UNSET:
            field_dict["latest_event_id"] = latest_event_id
        if latest_event is not UNSET:
            field_dict["latest_event"] = latest_event
        if tasks is not UNSET:
            field_dict["tasks"] = tasks

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.job_progress_out_latest_event_type_0 import JobProgressOutLatestEventType0
        from ..models.job_progress_out_task_counts import JobProgressOutTaskCounts
        from ..models.task_progress_out import TaskProgressOut

        d = dict(src_dict)
        job_id = d.pop("job_id")

        recipe = d.pop("recipe")

        status = JobProgressOutStatus(d.pop("status"))

        progress = d.pop("progress")

        total_tasks = d.pop("total_tasks")

        completed_tasks = d.pop("completed_tasks")

        task_counts = JobProgressOutTaskCounts.from_dict(d.pop("task_counts"))

        def _parse_current_task_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        current_task_id = _parse_current_task_id(d.pop("current_task_id", UNSET))

        def _parse_current_task_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        current_task_kind = _parse_current_task_kind(d.pop("current_task_kind", UNSET))

        def _parse_current_phase(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        current_phase = _parse_current_phase(d.pop("current_phase", UNSET))

        def _parse_latest_event_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        latest_event_id = _parse_latest_event_id(d.pop("latest_event_id", UNSET))

        def _parse_latest_event(data: object) -> JobProgressOutLatestEventType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                latest_event_type_0 = JobProgressOutLatestEventType0.from_dict(data)

                return latest_event_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(JobProgressOutLatestEventType0 | None | Unset, data)

        latest_event = _parse_latest_event(d.pop("latest_event", UNSET))

        _tasks = d.pop("tasks", UNSET)
        tasks: list[TaskProgressOut] | Unset = UNSET
        if _tasks is not UNSET:
            tasks = []
            for tasks_item_data in _tasks:
                tasks_item = TaskProgressOut.from_dict(tasks_item_data)

                tasks.append(tasks_item)

        job_progress_out = cls(
            job_id=job_id,
            recipe=recipe,
            status=status,
            progress=progress,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            task_counts=task_counts,
            current_task_id=current_task_id,
            current_task_kind=current_task_kind,
            current_phase=current_phase,
            latest_event_id=latest_event_id,
            latest_event=latest_event,
            tasks=tasks,
        )

        job_progress_out.additional_properties = d
        return job_progress_out

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
