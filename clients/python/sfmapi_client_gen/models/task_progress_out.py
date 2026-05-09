from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.task_progress_out_status import TaskProgressOutStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="TaskProgressOut")


@_attrs_define
class TaskProgressOut:
    """Per-task progress snapshot for polling clients.

    ``progress`` is a best-effort fraction in ``[0, 1]``. It is ``1``
    for terminal tasks, event-derived for running tasks when the
    latest ``phase_progress`` event carries ``current`` / ``total``,
    and ``0`` otherwise.

        Attributes:
            task_id (str):
            kind (str):
            status (TaskProgressOutStatus):
            progress (float):
            phase (None | str | Unset):
            current (int | None | Unset):
            total (int | None | Unset):
            latest_event_id (int | None | Unset):
            latest_event_kind (None | str | Unset):
            started_at (datetime.datetime | None | Unset):
            finished_at (datetime.datetime | None | Unset):
            elapsed_seconds (float | None | Unset):
    """

    task_id: str
    kind: str
    status: TaskProgressOutStatus
    progress: float
    phase: None | str | Unset = UNSET
    current: int | None | Unset = UNSET
    total: int | None | Unset = UNSET
    latest_event_id: int | None | Unset = UNSET
    latest_event_kind: None | str | Unset = UNSET
    started_at: datetime.datetime | None | Unset = UNSET
    finished_at: datetime.datetime | None | Unset = UNSET
    elapsed_seconds: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        kind = self.kind

        status = self.status.value

        progress = self.progress

        phase: None | str | Unset
        if isinstance(self.phase, Unset):
            phase = UNSET
        else:
            phase = self.phase

        current: int | None | Unset
        if isinstance(self.current, Unset):
            current = UNSET
        else:
            current = self.current

        total: int | None | Unset
        if isinstance(self.total, Unset):
            total = UNSET
        else:
            total = self.total

        latest_event_id: int | None | Unset
        if isinstance(self.latest_event_id, Unset):
            latest_event_id = UNSET
        else:
            latest_event_id = self.latest_event_id

        latest_event_kind: None | str | Unset
        if isinstance(self.latest_event_kind, Unset):
            latest_event_kind = UNSET
        else:
            latest_event_kind = self.latest_event_kind

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        elif isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        finished_at: None | str | Unset
        if isinstance(self.finished_at, Unset):
            finished_at = UNSET
        elif isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        elapsed_seconds: float | None | Unset
        if isinstance(self.elapsed_seconds, Unset):
            elapsed_seconds = UNSET
        else:
            elapsed_seconds = self.elapsed_seconds

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "task_id": task_id,
                "kind": kind,
                "status": status,
                "progress": progress,
            }
        )
        if phase is not UNSET:
            field_dict["phase"] = phase
        if current is not UNSET:
            field_dict["current"] = current
        if total is not UNSET:
            field_dict["total"] = total
        if latest_event_id is not UNSET:
            field_dict["latest_event_id"] = latest_event_id
        if latest_event_kind is not UNSET:
            field_dict["latest_event_kind"] = latest_event_kind
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if finished_at is not UNSET:
            field_dict["finished_at"] = finished_at
        if elapsed_seconds is not UNSET:
            field_dict["elapsed_seconds"] = elapsed_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        kind = d.pop("kind")

        status = TaskProgressOutStatus(d.pop("status"))

        progress = d.pop("progress")

        def _parse_phase(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        phase = _parse_phase(d.pop("phase", UNSET))

        def _parse_current(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        current = _parse_current(d.pop("current", UNSET))

        def _parse_total(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        total = _parse_total(d.pop("total", UNSET))

        def _parse_latest_event_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        latest_event_id = _parse_latest_event_id(d.pop("latest_event_id", UNSET))

        def _parse_latest_event_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        latest_event_kind = _parse_latest_event_kind(d.pop("latest_event_kind", UNSET))

        def _parse_started_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = isoparse(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        started_at = _parse_started_at(d.pop("started_at", UNSET))

        def _parse_finished_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = isoparse(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        finished_at = _parse_finished_at(d.pop("finished_at", UNSET))

        def _parse_elapsed_seconds(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        elapsed_seconds = _parse_elapsed_seconds(d.pop("elapsed_seconds", UNSET))

        task_progress_out = cls(
            task_id=task_id,
            kind=kind,
            status=status,
            progress=progress,
            phase=phase,
            current=current,
            total=total,
            latest_event_id=latest_event_id,
            latest_event_kind=latest_event_kind,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=elapsed_seconds,
        )

        task_progress_out.additional_properties = d
        return task_progress_out

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
