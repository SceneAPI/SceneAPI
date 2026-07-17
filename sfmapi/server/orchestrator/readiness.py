"""Single source of truth for task dependency readiness.

Three call sites used to carry private copies of this logic — the
scheduler (``submit_job_dag`` / ``resume_job``), the worker dispatcher,
and the janitor sweeps — and their vocabularies drifted: the scheduler
counted only ``succeeded`` dependencies as satisfied, so a task whose
upstream landed as ``skipped`` was never enqueued at submit time and
sat waiting for a janitor sweep. This module owns the vocabulary:

- a dependency edge is **satisfied** when the upstream status is in
  :data:`READY_DEPENDENCY_STATUSES` (``succeeded`` or ``skipped`` —
  the upstream's outputs are reusable either way);
- a task whose upstream ``failed`` (or vanished from the DB) can never
  run and must be failed;
- cancellation propagates, with ``cancelled_dirty`` taking precedence
  over plain ``cancelled``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Upstream statuses that satisfy a dependency edge.
READY_DEPENDENCY_STATUSES: frozenset[str] = frozenset({"succeeded", "skipped"})
#: Upstream statuses that doom the downstream task.
FAILED_DEPENDENCY_STATUSES: frozenset[str] = frozenset({"failed"})
#: Upstream statuses that cancel the downstream task.
CANCELLED_DEPENDENCY_STATUSES: frozenset[str] = frozenset({"cancelled", "cancelled_dirty"})


def dependencies_ready(deps: Iterable[object], status_by_id: Mapping[str, str]) -> bool:
    """True when every dependency has reached a reusable terminal state.

    A dependency missing from ``status_by_id`` counts as not ready —
    callers that need to distinguish "blocked" from "doomed" use
    :func:`dependency_state` instead.
    """
    return all(status_by_id.get(str(dep)) in READY_DEPENDENCY_STATUSES for dep in deps)


def dependency_state(deps: Iterable[object], status_by_id: Mapping[str, str]) -> str:
    """Classify a task's dependency edges into one scheduling state.

    Returns one of ``ready`` / ``blocked`` / ``failed`` / ``cancelled``
    / ``cancelled_dirty``. A dependency absent from ``status_by_id``
    counts as ``failed`` — the row is gone from the DB, so the task can
    never become ready. Precedence: missing/failed > cancelled_dirty >
    cancelled > blocked > ready.
    """
    dep_ids = [str(dep) for dep in deps]
    if any(dep not in status_by_id for dep in dep_ids):
        return "failed"
    if any(status_by_id.get(dep) in FAILED_DEPENDENCY_STATUSES for dep in dep_ids):
        return "failed"
    if any(status_by_id.get(dep) == "cancelled_dirty" for dep in dep_ids):
        return "cancelled_dirty"
    if any(status_by_id.get(dep) == "cancelled" for dep in dep_ids):
        return "cancelled"
    if any(status_by_id.get(dep) not in READY_DEPENDENCY_STATUSES for dep in dep_ids):
        return "blocked"
    return "ready"
