"""Pydantic Settings — single source of truth for all sfmapi config."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SFMAPI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["dev", "test", "prod"] = "dev"

    db_url: str = "sqlite+aiosqlite:///./sfmapi.db"
    redis_url: str = "redis://localhost:6379/0"

    workspace_root: Path = Path("./workspaces")
    blob_root: Path = Path("./workspaces/_blobs")
    s3_cache_root: Path = Path("./workspaces/_cache/s3")

    # Pluggable blob backend. `fs` keeps bytes under `blob_root`; `s3`
    # stores them in the configured bucket and downloads to a local
    # cache on first read; `memory` keeps bytes in a process-local
    # dict (ephemeral mode).
    blob_backend: Literal["fs", "s3", "memory"] = "fs"
    blob_s3_bucket: str | None = None
    blob_s3_prefix: str = ""
    blob_s3_region: str | None = None
    blob_s3_endpoint_url: str | None = None

    # Pluggable queue backend. `arq` enqueues to Redis (production);
    # `inline` runs each task synchronously in-process (tests, dev).
    queue_backend: Literal["arq", "inline"] = "arq"

    # Ephemeral mode — single-process, zero persistence. Switches the
    # DB to in-memory SQLite (shared-cache StaticPool), the blob store
    # to in-memory, the queue to inline, and routes the workspace to a
    # tempdir wiped on shutdown. Intended for demos, embedded use, and
    # smoke tests; multi-worker / multi-instance deploys must not
    # enable this.
    ephemeral: bool = False

    default_tenant: str = "default"
    auth_mode: Literal["none", "api_key"] = "none"

    # Cache-key salt freeform string. Production deployments set this
    # from the registered backend's runtime_versions() so cache hits
    # invalidate when the engine changes (commit sha, CUDA arch,
    # auxiliary libraries, ...). sfmapi itself doesn't interpret it.
    runtime_version_id: str = "unknown"
    seed: int = 0

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    upload_chunk_max_bytes: int = 8 * 1024 * 1024
    upload_expiry_hours: int = 24

    lease_ttl_seconds: int = 30
    janitor_interval_seconds: int = 10

    snapshot_keep_last: int = 3

    inline_tasks: bool = False

    # CORS — comma-separated list of allowed origins.
    # `*` allows everything (dev only). Empty disables the middleware.
    cors_origins: str = "*"
    cors_allow_credentials: bool = False

    # Thumbnails are cached at <workspace_root>/_thumbs/<sha>_<size>.jpg.
    thumbnail_default_size: int = 256
    thumbnail_max_size: int = 2048

    # ``POST /v1/oneshot/...`` endpoints accept the entire image in
    # the request body. Cap to keep per-request memory bounded; 0
    # disables the cap.
    oneshot_max_request_bytes: int = 50 * 1024 * 1024

    # URL pointing at hosted spec documentation. Defaults to the
    # canonical GitHub Pages doc site; deployments may override via
    # ``SFMAPI_SPEC_URL`` (set explicitly to an empty string to omit
    # the field entirely from ``GET /spec`` responses).
    spec_url: str | None = "https://sfmapi.github.io/spec"

    def model_post_init(self, _ctx: object) -> None:
        # Ephemeral mode rewires four subsystems to in-memory equivalents.
        # We do it post-init so explicit overrides (e.g. from tests) still
        # win — only fields still at their defaults get replaced.
        if not self.ephemeral:
            return
        import tempfile

        defaults = type(self).model_fields
        if self.db_url == defaults["db_url"].default:
            self.db_url = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"
        if self.blob_backend == defaults["blob_backend"].default:
            self.blob_backend = "memory"
        if self.queue_backend == defaults["queue_backend"].default:
            self.queue_backend = "inline"
        if not self.inline_tasks:
            self.inline_tasks = True
        if self.workspace_root == defaults["workspace_root"].default:
            self.workspace_root = Path(tempfile.mkdtemp(prefix="sfmapi-ephemeral-ws."))
        if self.blob_root == defaults["blob_root"].default:
            self.blob_root = self.workspace_root / "_blobs"
        if self.s3_cache_root == defaults["s3_cache_root"].default:
            self.s3_cache_root = self.workspace_root / "_cache" / "s3"

    def ensure_dirs(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.s3_cache_root.mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "_thumbs").mkdir(parents=True, exist_ok=True)

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests(**overrides: object) -> Settings:
    """Test helper — reinstantiate settings with overrides applied."""
    global _settings
    _settings = Settings(**overrides) if overrides else Settings()
    return _settings


def runtime_version_tuple(s: Settings | None = None) -> tuple[str, ...]:
    s = s or get_settings()
    return (s.runtime_version_id, str(s.seed))
