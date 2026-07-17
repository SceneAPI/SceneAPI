"""Task I/O helpers — read pre-execution state from ``Task.task_state_json``.

The orchestrator's :func:`sfmapi.server.services.job_service.materialize_dag`
writes ``{"inputs": {...}, "spec": {...}}`` into
``Task.task_state_json`` before a task runs. This module is the single
read point for worker handlers — keeping the dict-key vocabulary in one
place avoids the 19-file magic-string duplication this helper retired
(see ``L27`` in ``docs/guides/decisions.md``).

The dispatcher writes the worker's *result* into ``outputs_ref_json``
on success — that column is intentionally NOT touched here. Mixing the
two reads (state on the way in, result on the way out) was the original
design smell; the split keeps the wire-side ``TaskOut.outputs_ref``
typed as the result only.
"""

from __future__ import annotations

from pathlib import Path

from sfmapi.server.db.models import Task


def stage_output_dir(*, root: str | Path, task: Task, name: str) -> Path:
    """A fresh, created per-task output directory ``<root>/_<name>/<task_id>``.

    The path is derived worker-side from a *stable* root (a
    reconstruction or dataset root) plus the task id — it is NOT carried
    in ``inputs``. That keeps the task's ``inputs_hash`` stable across
    re-submits (so the cache short-circuits) while still giving each run
    its own scratch dir.
    """
    out = Path(root) / f"_{name}" / task.task_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def read_state(task: Task) -> tuple[dict, dict]:
    """Return ``(inputs, spec)`` from a task's pre-execution state.

    Both default to empty dicts when the column is ``NULL`` or the
    individual key is absent — matches the previous null-safe nested
    ``.get(...)`` idiom in 19 worker files that this helper retired.
    """
    state = task.task_state_json or {}
    return state.get("inputs") or {}, state.get("spec") or {}


def read_inputs(task: Task) -> dict:
    """Return just the ``inputs`` half of the task state."""
    return (task.task_state_json or {}).get("inputs") or {}


def read_extra(task: Task, key: str, default: object = None) -> object:
    """Return any other top-level key from task state.

    Used by tests (e.g. ``sleep_for`` in noop) and any future
    sidecar fields that don't fit the ``inputs`` / ``spec`` split.
    """
    return (task.task_state_json or {}).get(key, default)
