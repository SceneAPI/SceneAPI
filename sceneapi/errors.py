"""Stable exception classes for sfmapi extensions."""

from sceneapi.server.core.errors import (
    BackendUnavailableError,
    BadRequestError,
    CapabilityUnavailableError,
    ConflictError,
    NotFoundError,
    PycolmapUnavailableError,
    QuotaExceededError,
    SfmApiError,
    StorageError,
    TenantViolationError,
    ValidationError,
)

__all__ = [
    "BackendUnavailableError",
    "BadRequestError",
    "CapabilityUnavailableError",
    "ConflictError",
    "NotFoundError",
    "PycolmapUnavailableError",
    "QuotaExceededError",
    "SfmApiError",
    "StorageError",
    "TenantViolationError",
    "ValidationError",
]
