"""Optional backend progress reporting contract.

Backends are not required to report progress. Long-running backend
methods MAY accept a keyword-only ``progress`` argument typed as
``ProgressReporter``. Worker code detects that argument before passing
it, preserving compatibility with older backend packages.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal, Protocol

from sfmapi.server.schemas.progress_event import Phase

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class ProgressReporter(Protocol):
    """Best-effort sink for backend progress telemetry.

    The reporter writes the existing ``ProgressEvent`` wire shape. It
    must never be required for backend correctness: implementations
    should tolerate reporter failures and keep the reconstruction work
    moving.
    """

    def phase_started(self, phase: Phase) -> None: ...

    def phase_progress(
        self,
        phase: Phase,
        *,
        current: int,
        total: int | None = None,
        rate: float | None = None,
    ) -> None: ...

    def phase_completed(self, phase: Phase) -> None: ...

    def metric(self, key: str, value: float) -> None: ...

    def snapshot_available(self, *, snapshot_seq: int, summary: dict[str, Any]) -> None: ...

    def log_line(self, level: LogLevel, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(
        self,
        *,
        error_class: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None: ...


class NoopProgressReporter:
    """Drop-in reporter for tests and backend code paths without a job."""

    def phase_started(self, phase: Phase) -> None:
        return None

    def phase_progress(
        self,
        phase: Phase,
        *,
        current: int,
        total: int | None = None,
        rate: float | None = None,
    ) -> None:
        return None

    def phase_completed(self, phase: Phase) -> None:
        return None

    def metric(self, key: str, value: float) -> None:
        return None

    def snapshot_available(self, *, snapshot_seq: int, summary: dict[str, Any]) -> None:
        return None

    def log_line(self, level: LogLevel, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(
        self,
        *,
        error_class: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        return None


def accepts_progress(call: Callable[..., Any]) -> bool:
    """Return true when ``call`` can receive ``progress=...``.

    Native-extension callables do not always expose inspectable
    signatures. In that case the safe compatibility choice is to omit
    the optional argument.
    """

    try:
        signature = inspect.signature(call)
    except (TypeError, ValueError):
        return False
    return any(
        name == "progress" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for name, parameter in signature.parameters.items()
    )


def call_with_optional_progress(
    call: Callable[..., Any],
    *,
    progress: ProgressReporter | None,
    **kwargs: Any,
) -> Any:
    """Call a backend method, adding ``progress`` only when supported."""

    if progress is not None and accepts_progress(call):
        kwargs["progress"] = progress
    return call(**kwargs)


__all__ = [
    "LogLevel",
    "NoopProgressReporter",
    "ProgressReporter",
    "accepts_progress",
    "call_with_optional_progress",
]
