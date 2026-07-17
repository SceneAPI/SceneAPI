from __future__ import annotations


def test_public_backend_api_reexports_runtime_contracts() -> None:
    from sceneapi.backends import Backend, SfmBackend, assert_backend_contract, register_backend
    from sceneapi.errors import CapabilityUnavailableError, ValidationError
    from sceneapi.runtime import create_app

    assert Backend is not None
    assert SfmBackend is not None
    assert callable(assert_backend_contract)
    assert callable(register_backend)
    assert CapabilityUnavailableError.__name__ == "CapabilityUnavailableError"
    assert ValidationError.__name__ == "ValidationError"
    assert callable(create_app)


async def test_public_testing_reset_runtime_for_tests(tmp_path) -> None:
    from sceneapi.testing import reset_runtime_for_tests

    settings = await reset_runtime_for_tests(
        ephemeral=True,
        db_url="sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        blob_backend="memory",
        queue_backend="inline",
        inline_tasks=True,
        workspace_root=tmp_path / "workspace",
    )

    assert settings.ephemeral is True
