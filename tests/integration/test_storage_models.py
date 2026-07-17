from __future__ import annotations

import hashlib

import pytest

from sfmapi.server.core.errors import StorageError
from sfmapi.server.storage.models import artifact_path, install_artifact, verify_sha

pytestmark = pytest.mark.integration


def test_install_artifact_writes_and_verifies() -> None:
    payload = b"weights-bytes-12345"
    sha = hashlib.sha256(payload).hexdigest()
    p = install_artifact(
        family="sam", name="vit_b", version="1.0", src_bytes=payload, expected_sha=sha
    )
    assert p.is_file()
    assert verify_sha(p, sha) is True
    assert p == artifact_path("sam", "vit_b", "1.0")


def test_install_artifact_rejects_sha_mismatch() -> None:
    payload = b"abc"
    with pytest.raises(StorageError, match="sha mismatch"):
        install_artifact(
            family="sam",
            name="vit_b",
            version="bad",
            src_bytes=payload,
            expected_sha="0" * 64,
        )
