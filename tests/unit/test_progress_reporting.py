"""Backend progress reporter compatibility helpers."""

from __future__ import annotations

from sfmapi.server.adapters.progress import (
    NoopProgressReporter,
    accepts_progress,
    call_with_optional_progress,
)


def test_call_with_optional_progress_omits_unknown_keyword() -> None:
    def method(*, value: int) -> int:
        return value + 1

    assert accepts_progress(method) is False
    assert call_with_optional_progress(method, progress=NoopProgressReporter(), value=1) == 2


def test_call_with_optional_progress_passes_supported_keyword() -> None:
    def method(*, progress: object | None = None) -> bool:
        return progress is not None

    seen = call_with_optional_progress(method, progress=NoopProgressReporter())

    assert accepts_progress(method) is True
    assert seen is True


def test_call_with_optional_progress_supports_kwargs_backend() -> None:
    def method(**kwargs: object) -> bool:
        return "progress" in kwargs

    assert accepts_progress(method) is True
    assert call_with_optional_progress(method, progress=NoopProgressReporter()) is True
