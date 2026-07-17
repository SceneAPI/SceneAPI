"""Test helpers for backend plugin packages."""

import asyncio

from sceneapi.server.core.capabilities import reset_capabilities_cache
from sceneapi.server.core.config import Settings, reset_settings_for_tests
from sceneapi.server.db.session import reset_engine_for_tests


async def reset_runtime_for_tests(**settings_overrides: object) -> Settings:
    """Reset settings, database engine, and capability cache for plugin tests."""

    settings = reset_settings_for_tests(**settings_overrides)
    await reset_engine_for_tests(settings)
    reset_capabilities_cache()
    return settings


def reset_runtime_for_tests_sync(**settings_overrides: object) -> Settings:
    """Synchronous wrapper for pytest suites that are not async."""

    return asyncio.run(reset_runtime_for_tests(**settings_overrides))


__all__ = [
    "Settings",
    "reset_capabilities_cache",
    "reset_runtime_for_tests",
    "reset_runtime_for_tests_sync",
    "reset_settings_for_tests",
]
