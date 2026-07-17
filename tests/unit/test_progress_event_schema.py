from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from sceneapi.server.schemas.progress_event import ProgressEvent

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime.now(UTC)


def test_phase_started_validates() -> None:
    adapter = TypeAdapter(ProgressEvent)
    obj = adapter.validate_python(
        {
            "kind": "phase_started",
            "ts": _now().isoformat(),
            "job_id": "01" * 13,
            "seq": 0,
            "phase": "feature_extraction",
        }
    )
    assert obj.kind == "phase_started"
    assert obj.schema_version == 1


def test_phase_progress_with_total() -> None:
    adapter = TypeAdapter(ProgressEvent)
    obj = adapter.validate_python(
        {
            "kind": "phase_progress",
            "ts": _now().isoformat(),
            "job_id": "j" * 26,
            "seq": 1,
            "phase": "incremental_register",
            "current": 3,
            "total": 10,
        }
    )
    assert obj.current == 3
    assert obj.total == 10


def test_unknown_kind_rejected() -> None:
    adapter = TypeAdapter(ProgressEvent)
    with pytest.raises(ValueError, match="kind"):
        adapter.validate_python(
            {
                "kind": "totally_made_up",
                "ts": _now().isoformat(),
                "job_id": "j" * 26,
                "seq": 1,
            }
        )


def test_snapshot_available_carries_summary() -> None:
    adapter = TypeAdapter(ProgressEvent)
    obj = adapter.validate_python(
        {
            "kind": "snapshot_available",
            "ts": _now().isoformat(),
            "job_id": "j" * 26,
            "seq": 4,
            "snapshot_seq": 2,
            "summary": {"images": 5},
        }
    )
    assert obj.snapshot_seq == 2
    assert obj.summary == {"images": 5}
