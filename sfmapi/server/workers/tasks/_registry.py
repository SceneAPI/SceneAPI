"""Auto-registration for worker task handlers + lifecycle hooks.

Each task module under ``sfmapi.server.workers.tasks`` decorates its ``run``
function with ``@task_handler("kind")``; the dispatcher walks the
package and reads :func:`get_registered` instead of maintaining a
hand-curated dispatch dict. Adding a new task is now: (1) create
``sfmapi/server/workers/tasks/<kind>.py`` with a decorated ``run`` function,
(2) update the (single) capability + spec entry. No dispatcher
edit, no chance of an "imported the module but forgot the dict
entry" drift mode.

Kind-specific resource roll-ups register the same way instead of
being hardwired into the dispatcher: pass ``on_status`` and/or
``on_success`` to the decorator.

  - ``await on_status(session, task, status)`` — fired by the
    dispatcher on the ``running`` transition and on every
    non-success terminal transition (with ``status="failed"``;
    cancelled tasks also fail their resource).
  - ``await on_success(session, task, outputs)`` — fired right
    before the dispatcher commits a successful task.

Both run inside the dispatcher's task session, before its commit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

#: ``await hook(session, task, status)``
StatusHook = Callable[..., Awaitable[None]]
#: ``await hook(session, task, outputs)``
SuccessHook = Callable[..., Awaitable[None]]

_HANDLERS: dict[str, Callable[..., Any]] = {}
_STATUS_HOOKS: dict[str, StatusHook] = {}
_SUCCESS_HOOKS: dict[str, SuccessHook] = {}


def task_handler(
    kind: str,
    *,
    on_status: StatusHook | None = None,
    on_success: SuccessHook | None = None,
) -> Callable[[F], F]:
    """Register the decorated function as the worker handler for
    ``kind``, optionally with lifecycle hooks. Re-registration is an
    error to catch typos."""

    def deco(fn: F) -> F:
        if kind in _HANDLERS:
            raise RuntimeError(
                f"task handler for {kind!r} already registered "
                f"(by {_HANDLERS[kind].__module__}); "
                f"now {fn.__module__}"
            )
        _HANDLERS[kind] = fn
        if on_status is not None:
            _STATUS_HOOKS[kind] = on_status
        if on_success is not None:
            _SUCCESS_HOOKS[kind] = on_success
        return fn

    return deco


def get_registered() -> dict[str, Callable[..., Any]]:
    """Return a copy of the registered handler map."""
    return dict(_HANDLERS)


def get_status_hook(kind: str) -> StatusHook | None:
    """Return the ``on_status`` hook registered for ``kind``, if any."""
    return _STATUS_HOOKS.get(kind)


def get_success_hook(kind: str) -> SuccessHook | None:
    """Return the ``on_success`` hook registered for ``kind``, if any."""
    return _SUCCESS_HOOKS.get(kind)


def clear_for_tests() -> None:
    """Reset the registry — used by the test suite when verifying
    auto-discovery semantics."""
    _HANDLERS.clear()
    _STATUS_HOOKS.clear()
    _SUCCESS_HOOKS.clear()


__all__ = [
    "StatusHook",
    "SuccessHook",
    "clear_for_tests",
    "get_registered",
    "get_status_hook",
    "get_success_hook",
    "task_handler",
]
