"""Shared generated-SDK plumbing for the bench harness.

Builds ``scenesdk`` clients and translates the generated
:class:`UnexpectedStatus` into the typed ``SfmApiError`` hierarchy
from ``scenesdk._ergonomics``, so bench failure output keeps
the same typed error names the hand-rolled ``sfmapi_client`` package
used to raise (removed at 0.1.0 as scheduled).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from scenesdk import AuthenticatedClient, Client
from scenesdk._ergonomics import raise_for_status
from scenesdk.errors import UnexpectedStatus

ApiClient = AuthenticatedClient | Client


def make_client(base_url: str, *, api_key: str | None = None, timeout: float = 120.0) -> ApiClient:
    """Build a generated-SDK client, mirroring the deprecated
    ``SfmApiClient(base_url, api_key=..., timeout=...)`` constructor.

    ``raise_on_unexpected_status`` is enabled so every non-2xx surfaces
    as an exception instead of a silent ``None`` — :func:`call` then
    retypes it.
    """
    kwargs: dict[str, Any] = {
        "base_url": base_url.rstrip("/"),
        "timeout": httpx.Timeout(timeout),
        "raise_on_unexpected_status": True,
    }
    if api_key:
        return AuthenticatedClient(token=api_key, **kwargs)
    return Client(**kwargs)


def call[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Invoke a generated endpoint function (``<endpoint>.sync`` or
    ``.sync_detailed``) and re-raise any HTTP error as a typed
    :class:`scenesdk._ergonomics.SfmApiError` subclass."""
    try:
        return fn(*args, **kwargs)
    except UnexpectedStatus as exc:
        raise_for_status(exc)
        raise  # unreachable — raise_for_status always raises
