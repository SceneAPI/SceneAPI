"""Shared dependency-readiness vocabulary (``sceneapi.server.orchestrator.readiness``).

Scheduler, dispatcher, and janitor used to carry three drifting copies
of this logic (the scheduler accepted only ``succeeded``). These tests
pin the single-source vocabulary and that the dispatcher re-exports it
rather than redefining it.
"""

from __future__ import annotations

import pytest

from sceneapi.server.orchestrator.readiness import (
    CANCELLED_DEPENDENCY_STATUSES,
    FAILED_DEPENDENCY_STATUSES,
    READY_DEPENDENCY_STATUSES,
    dependencies_ready,
    dependency_state,
)

pytestmark = pytest.mark.unit


def test_ready_vocabulary_is_succeeded_and_skipped() -> None:
    assert {"succeeded", "skipped"} == READY_DEPENDENCY_STATUSES
    assert {"failed"} == FAILED_DEPENDENCY_STATUSES
    assert {"cancelled", "cancelled_dirty"} == CANCELLED_DEPENDENCY_STATUSES


def test_dependencies_ready_accepts_succeeded_and_skipped() -> None:
    assert dependencies_ready(["a", "b"], {"a": "succeeded", "b": "skipped"})


def test_dependencies_ready_with_no_dependencies() -> None:
    assert dependencies_ready([], {})


@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled"])
def test_dependencies_ready_rejects_non_reusable_statuses(status: str) -> None:
    assert not dependencies_ready(["a"], {"a": status})


def test_dependencies_ready_rejects_missing_dependency() -> None:
    assert not dependencies_ready(["ghost"], {})


def test_dependencies_ready_coerces_non_string_dep_ids() -> None:
    # depends_on_json is raw JSON — ids may come back as non-str values.
    assert dependencies_ready([123], {"123": "succeeded"})


def test_dependency_state_ready_and_blocked() -> None:
    assert dependency_state([], {}) == "ready"
    assert dependency_state(["a"], {"a": "skipped"}) == "ready"
    assert dependency_state(["a"], {"a": "running"}) == "blocked"


def test_dependency_state_missing_dependency_is_failed() -> None:
    assert dependency_state(["ghost"], {}) == "failed"


def test_dependency_state_precedence() -> None:
    # failed beats cancellation beats blocked.
    statuses = {"f": "failed", "cd": "cancelled_dirty", "c": "cancelled", "p": "pending"}
    assert dependency_state(["f", "cd", "c", "p"], statuses) == "failed"
    assert dependency_state(["cd", "c", "p"], statuses) == "cancelled_dirty"
    assert dependency_state(["c", "p"], statuses) == "cancelled"
    assert dependency_state(["p"], statuses) == "blocked"


def test_dispatcher_reuses_shared_vocabulary() -> None:
    from sceneapi.server.orchestrator import readiness
    from sceneapi.server.workers import dispatcher

    assert dispatcher.READY_DEPENDENCY_STATUSES is readiness.READY_DEPENDENCY_STATUSES
    assert dispatcher.CANCELLED_DEPENDENCY_STATUSES is readiness.CANCELLED_DEPENDENCY_STATUSES
    assert dispatcher._dependency_state_from_statuses is readiness.dependency_state
