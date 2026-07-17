"""Contract test fixtures.

Boots the app in ephemeral mode, records a small set of representative
server responses to ``tests/contract/fixtures/``, and exposes them to
tests as JSON. Tests then replay those fixtures through every SDK's
typed surface (Pydantic in Python, the openapi-types diff in TS) and
assert the structure decodes cleanly.

The fixtures are recorded fresh per session so they can never lag
behind the running server. ``TESTS_CONTRACT_REFRESH=true`` forces an
overwrite of any existing fixture file (useful when intentionally
broadening coverage).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from sceneapi.server.core.config import Settings, reset_settings_for_tests

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_ULID_RE = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_TIMESTAMP_KEYS = {
    "created_at",
    "updated_at",
    "expires_at",
    "started_at",
    "finished_at",
    "lease_expires_at",
    "ts",
}
_ID_KIND_BY_KEY = {
    "artifact_id": "artifact",
    "cache_key": "cache",
    "dataset_id": "dataset",
    "image_id": "image",
    "job_id": "job",
    "mask_id": "mask",
    "maskset_id": "maskset",
    "project_id": "project",
    "recon_id": "recon",
    "rv_id": "runtime",
    "source_id": "source",
    "submodel_id": "submodel",
    "task_id": "task",
    "upload_id": "upload",
}
_ID_KIND_BASE = {
    "artifact": 1_000,
    "cache": 2_000,
    "dataset": 3_000,
    "generic": 4_000,
    "image": 5_000,
    "job": 6_000,
    "mask": 7_000,
    "maskset": 8_000,
    "project": 9_000,
    "recon": 10_000,
    "runtime": 11_000,
    "source": 12_000,
    "submodel": 13_000,
    "task": 14_000,
    "upload": 15_000,
}


def _clear_inherited_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SCENEAPI_DB_URL",
        "SCENEAPI_WORKSPACE_ROOT",
        "SCENEAPI_BLOB_ROOT",
        "SCENEAPI_S3_CACHE_ROOT",
        "SCENEAPI_INLINE_TASKS",
        "SCENEAPI_QUEUE_BACKEND",
        "SCENEAPI_BLOB_BACKEND",
        "SCENEAPI_AUTO_LOAD_BACKEND_PLUGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    # Contract fixtures lock the recorded responses to the StubBackend
    # surface — never pull in whatever plugins the venv happens to ship.
    monkeypatch.setenv("SCENEAPI_AUTO_LOAD_BACKEND_PLUGINS", "false")


@pytest.fixture
def ephemeral_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    _clear_inherited_env(monkeypatch)
    monkeypatch.setenv("SCENEAPI_EPHEMERAL", "true")
    return reset_settings_for_tests()


@pytest.fixture
async def contract_client(ephemeral_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient bound to a fresh ephemeral app
    instance with lifespan driven so the schema bootstraps."""
    from sceneapi.server.db import session as session_mod

    if session_mod._engine is not None:
        await session_mod._engine.dispose()
    session_mod._engine = None
    session_mod._session_factory = None

    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://contract") as client,
    ):
        yield client


def fixture_path(name: str) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURE_DIR / f"{name}.json"


def load_fixture(name: str) -> Any:
    p = fixture_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"missing contract fixture: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _id_kind_for_key(key: str | None) -> str | None:
    if key is None:
        return None
    if key.endswith("_ids"):
        key = f"{key[:-4]}_id"
    return _ID_KIND_BY_KEY.get(key)


def _placeholder_id(kind: str, ordinal: int) -> str:
    base = _ID_KIND_BASE.get(kind, _ID_KIND_BASE["generic"])
    return f"01H{base + ordinal:023d}"


def _assign_id(
    value: str,
    *,
    kind: str,
    replacements: dict[str, str],
    counters: dict[str, int],
) -> None:
    if value in replacements:
        return
    counters[kind] = counters.get(kind, 0) + 1
    replacements[value] = _placeholder_id(kind, counters[kind])


def _collect_semantic_ids(
    value: Any,
    *,
    key: str | None,
    replacements: dict[str, str],
    counters: dict[str, int],
) -> None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _collect_semantic_ids(
                child_value,
                key=child_key,
                replacements=replacements,
                counters=counters,
            )
        return
    if isinstance(value, list):
        for item in value:
            _collect_semantic_ids(
                item,
                key=key,
                replacements=replacements,
                counters=counters,
            )
        return
    if not isinstance(value, str):
        return
    kind = _id_kind_for_key(key)
    if kind and _ULID_RE.fullmatch(value):
        _assign_id(value, kind=kind, replacements=replacements, counters=counters)


def _normalize_fixture_value(
    value: Any,
    *,
    key: str | None,
    replacements: dict[str, str],
    counters: dict[str, int],
) -> Any:
    if isinstance(value, dict):
        return {
            child_key: _normalize_fixture_value(
                child_value,
                key=child_key,
                replacements=replacements,
                counters=counters,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _normalize_fixture_value(
                item,
                key=key,
                replacements=replacements,
                counters=counters,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value
    if key in _TIMESTAMP_KEYS:
        return "2026-01-01T00:00:00Z"

    def replace_id(match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw not in replacements:
            _assign_id(raw, kind="generic", replacements=replacements, counters=counters)
        return replacements[raw]

    return _ULID_RE.sub(replace_id, value)


def normalize_fixture(body: Any) -> Any:
    """Replace volatile IDs and audit timestamps with stable fixture values."""
    replacements: dict[str, str] = {}
    counters: dict[str, int] = {}
    _collect_semantic_ids(body, key=None, replacements=replacements, counters=counters)
    return _normalize_fixture_value(body, key=None, replacements=replacements, counters=counters)


def save_fixture(name: str, body: Any) -> None:
    """Save a JSON body to ``fixtures/<name>.json``.

    Contract tests hit real routes, which allocate fresh IDs and timestamps.
    Normalize those volatile fields so checked-in fixtures represent wire
    shape, not the clock tick from the last local test run.
    """
    p = fixture_path(name)
    payload = json.dumps(normalize_fixture(body), indent=2, sort_keys=True) + "\n"
    if p.is_file() and not os.environ.get("TESTS_CONTRACT_REFRESH"):
        existing = p.read_text(encoding="utf-8")
        if existing == payload:
            return
    p.write_text(payload, encoding="utf-8")


# ---------------------------------------------------------------------
# Live-server fixture used by every ``test_e_*`` end-to-end test.
# Earlier each test inlined ~80 lines of bootstrap + cleanup; this
# fixture extracts the harness and makes the actual test bodies short
# enough to fit on screen.
# ---------------------------------------------------------------------


@pytest.fixture
def live_ephemeral_server(monkeypatch: pytest.MonkeyPatch):
    """Boot the ephemeral app on a random localhost port via uvicorn
    in a background thread, yield ``base_url``, tear down on exit.

    Cleanup includes engine disposal + memory-blob singleton reset
    + Settings reset so subsequent tests in the same process see a
    fresh global state.
    """
    pytest.importorskip("httpx")
    import asyncio
    import threading
    import time

    import uvicorn

    from sceneapi.server.db import session as session_mod
    from sceneapi.server.storage.blobs import reset_memory_blob_store_for_tests

    _clear_inherited_env(monkeypatch)
    monkeypatch.setenv("SCENEAPI_EPHEMERAL", "true")

    prior_engine = session_mod._engine

    async def _dispose(engine: object | None) -> None:
        if engine is not None:
            await engine.dispose()  # type: ignore[attr-defined]

    asyncio.run(_dispose(prior_engine))
    session_mod._engine = None
    session_mod._session_factory = None
    reset_memory_blob_store_for_tests()
    reset_settings_for_tests()
    monkeypatch.setattr(session_mod, "_engine", None, raising=False)
    monkeypatch.setattr(session_mod, "_session_factory", None, raising=False)

    from sceneapi.server.main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    def run() -> None:
        server.run()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started and server.servers:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn server didn't start in time"
    sock = next(iter(server.servers[0].sockets))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"

    try:
        yield base
    finally:
        server.should_exit = True
        t.join(timeout=5.0)
        cur = session_mod._engine
        asyncio.run(_dispose(cur))
        session_mod._engine = None
        session_mod._session_factory = None
        reset_memory_blob_store_for_tests()
        reset_settings_for_tests()
