"""Model-artifact filesystem layout + lazy download/verify."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sfmapi.server.core.config import Settings, get_settings
from sfmapi.server.core.errors import StorageError


def models_root(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return Path(s.workspace_root) / "models"


def artifact_path(family: str, name: str, version: str, filename: str = "weights.pth") -> Path:
    return models_root() / family / name / version / filename


def verify_sha(path: Path, expected_sha: str) -> bool:
    if not path.is_file():
        return False
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower() == expected_sha.lower()


def install_artifact(
    *, family: str, name: str, version: str, src_bytes: bytes, expected_sha: str
) -> Path:
    target = artifact_path(family, name, version)
    target.parent.mkdir(parents=True, exist_ok=True)
    actual = hashlib.sha256(src_bytes).hexdigest()
    if actual.lower() != expected_sha.lower():
        raise StorageError(f"Model artifact sha mismatch (got {actual}, expected {expected_sha})")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(src_bytes)
    os.replace(tmp, target)
    return target
