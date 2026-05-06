"""Client-side error hierarchy.

Mirrors the server's `app.core.errors` so callers can catch
domain-specific exceptions without re-parsing problem+json.
"""

from __future__ import annotations

from typing import Any

import httpx


class SfmApiError(Exception):
    """Base for all SDK exceptions."""

    status_code: int = 0

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int = 0,
        problem: dict[str, Any] | None = None,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message or (problem or {}).get("title") or "sfmapi error")
        self.status_code = status_code or (problem or {}).get("status", 0)
        self.problem = problem or {}
        self.response = response


class NotFoundError(SfmApiError):
    pass


class ConflictError(SfmApiError):
    pass


class ValidationError(SfmApiError):
    pass


class AuthError(SfmApiError):
    """403 — bad/missing API key, tenant violation."""


class QuotaExceededError(SfmApiError):
    pass


class StorageError(SfmApiError):
    pass


class PycolmapUnavailableError(SfmApiError):
    pass


class TransportError(SfmApiError):
    """httpx-level transport failure (connect, timeout, …)."""


def raise_for_response(resp: httpx.Response) -> None:
    """Map an HTTP error response to the right SDK exception. NOOP if 2xx."""
    if resp.is_success:
        return
    problem: dict[str, Any] = {}
    if "application/problem+json" in resp.headers.get(
        "content-type", ""
    ) or "application/json" in resp.headers.get("content-type", ""):
        try:
            problem = resp.json()
        except Exception:
            problem = {"title": resp.reason_phrase, "status": resp.status_code}
    msg = problem.get("detail") or problem.get("title") or resp.text or resp.reason_phrase
    cls = _STATUS_MAP.get(resp.status_code, SfmApiError)
    raise cls(msg, status_code=resp.status_code, problem=problem, response=resp)


_STATUS_MAP: dict[int, type[SfmApiError]] = {
    403: AuthError,
    404: NotFoundError,
    409: ConflictError,
    413: QuotaExceededError,
    422: ValidationError,
    429: QuotaExceededError,
    501: PycolmapUnavailableError,
    507: StorageError,
}
