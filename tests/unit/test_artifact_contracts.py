from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters import backend_artifacts
from app.adapters.registry import register_backend
from app.adapters.stub_backend import StubBackend
from app.api.v1.artifacts import artifact_out
from app.core import artifacts as artifact_vocab
from app.core.capabilities import detect_capabilities, reset_capabilities_cache
from app.core.config import reset_settings_for_tests
from app.core.errors import ValidationError
from app.core.hashing import content_address
from app.core.ids import new_id
from app.db.models import Job, Project, StageArtifact, Task, utcnow
from app.services import artifact_service


class ArtifactContractBackend(StubBackend):
    name = "artifact_test"
    version = "1.0"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return {
            "features.extract.superpoint",
            "pairs.retrieval",
            "matchers.lightglue",
            "matches.verify",
            "map.incremental",
        }

    def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "contract_id": "artifact_test.features.superpoint",
                "stage": "features",
                "capability": "features.extract.superpoint",
                "provider": "artifact_test",
                "display_name": "SuperPoint artifacts",
                "accepts": [],
                "emits": ["features.local.v1"],
                "preferred": "features.local.v1",
            }
        ]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"sha256": "A" * 64}, r"metadata\.sha256.*lowercase hex SHA-256"),
        ({"byte_size": -1}, r"metadata\.byte_size.*non-negative int"),
        (
            {"files": [{"name": "x", "uri": "mem://x", "sha256": "A" * 64}]},
            r"metadata\.files\[0\]\.sha256.*lowercase hex SHA-256",
        ),
    ],
)
def test_task_artifact_reserved_metadata_is_validated(
    metadata: dict[str, Any],
    message: str,
) -> None:
    task = SimpleNamespace(kind="unit-test")
    with pytest.raises(ValidationError, match=message):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.local.v1",
                        "metadata": metadata,
                    }
                ]
            },
        )


def test_task_artifact_metadata_files_strip_local_paths() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    task = SimpleNamespace(kind="unit-test")
    normalized = artifact_service.normalize_task_outputs(
        task,  # type: ignore[arg-type]
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "files": [
                        {
                            "name": "public",
                            "uri": "mem://public",
                            "path": "C:/secret/a.bin",
                        },
                        {
                            "name": "private-uri",
                            "uri": "C:/secret/e.bin",
                        },
                        {
                            "name": "relative",
                            "uri": "images/a.jpg",
                        },
                        {"name": "private", "path": "C:/secret/b.bin"},
                    ],
                    "metadata": {
                        "debug_path": "C:/secret/debug.json",
                        "nested": {
                            "workspace": "\\\\server\\private\\run",
                            "note": "kept",
                        },
                        "files": [
                            {
                                "name": "nested",
                                "uri": "mem://nested",
                                "path": "C:/secret/c.bin",
                            },
                            {"name": "nested-private", "path": "C:/secret/d.bin"},
                        ]
                    },
                }
            ]
        },
    )

    metadata = normalized["artifacts"][0]["metadata"]
    assert metadata["files"] == normalized["artifacts"][0]["files"]
    assert metadata["files"][0]["path"] == "C:/secret/a.bin"
    assert metadata["nested"] == {"note": "kept"}

    public = sanitize_public_outputs(normalized)
    public_metadata = public["artifacts"][0]["metadata"]
    assert public_metadata["files"] == [
        {"name": "public", "uri": "mem://public"},
        {"name": "private-uri"},
        {"name": "relative"},
        {"name": "private"},
    ]
    assert "path" not in repr(public_metadata)
    assert "secret" not in repr(public_metadata)


def test_task_artifact_metadata_file_uris_strip_local_paths_without_descriptor_files() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    task = SimpleNamespace(kind="unit-test")
    normalized = artifact_service.normalize_task_outputs(
        task,  # type: ignore[arg-type]
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "metadata": {
                        "files": [
                            {"name": "public", "uri": "mem://nested"},
                            {"name": "local", "uri": "file:///C:/secret/nested.bin"},
                            {"name": "traversal", "uri": "../secret.bin"},
                        ]
                    },
                }
            ]
        },
    )

    metadata = normalized["artifacts"][0]["metadata"]
    assert metadata["files"] == [
        {"name": "public", "uri": "mem://nested"},
        {"name": "local", "uri": "file:///C:/secret/nested.bin"},
        {"name": "traversal", "uri": "../secret.bin"},
    ]

    public = sanitize_public_outputs(normalized)
    public_metadata = public["artifacts"][0]["metadata"]
    assert public_metadata["files"] == [
        {"name": "public", "uri": "mem://nested"},
        {"name": "local"},
        {"name": "traversal"},
    ]
    assert "secret" not in repr(public_metadata)


def test_task_artifact_metadata_redacts_service_urls_and_secret_keys() -> None:
    task = SimpleNamespace(kind="unit-test")
    normalized = artifact_service.normalize_task_outputs(
        task,  # type: ignore[arg-type]
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "metadata": {
                        "provider": "fixture",
                        "service_url": "http://127.0.0.1:5000/private",
                        "token": "SECRET_TOKEN",
                        "api_key": "abc123",
                        "env": "SFMAPI_INSTANTSFM_SERVICE_URL",
                        "nested": {
                            "authorization": "Bearer SECRET_TOKEN",
                            "note_with_credential": "Bearer abc123",
                            "note": "kept",
                        },
                    },
                }
            ]
        },
    )

    metadata = normalized["artifacts"][0]["metadata"]
    assert metadata["provider"] == "fixture"
    assert metadata["service_url"] == "<redacted>"
    assert metadata["nested"] == {"note": "kept"}
    assert "api_key" not in metadata
    assert "SECRET" not in repr(metadata)
    assert "Bearer" not in repr(metadata)
    assert "SFMAPI_" not in repr(metadata)
    assert "127.0.0.1" not in repr(metadata)


def test_task_artifact_summary_and_producer_are_public_sanitized() -> None:
    task = SimpleNamespace(kind="unit-test")
    normalized = artifact_service.normalize_task_outputs(
        task,  # type: ignore[arg-type]
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "producer": {
                        "backend": "fixture",
                        "host_path": "/app/private/provider.json",
                        "service_url": "http://127.0.0.1:5000/private",
                    },
                    "summary": {
                        "api_href": "/v10/jobs/01TEST",
                        "note": "wrote /app/private/out.bin then /scratch/job/out.bin",
                        "token": "SECRET_BODY",
                        "service_url": "http://127.0.0.1:5000/summary",
                    },
                }
            ]
        },
    )

    artifact = normalized["artifacts"][0]
    assert artifact["producer"] == {
        "backend": "fixture",
        "service_url": "<redacted>",
    }
    assert artifact["metadata"]["producer"] == artifact["producer"]
    assert artifact["summary"] == {
        "api_href": "/v10/jobs/01TEST",
        "note": "wrote <redacted> then <redacted>",
        "service_url": "<redacted>",
    }
    public_repr = repr(artifact)
    assert "SECRET" not in public_repr
    assert "127.0.0.1" not in public_repr
    assert "/app/" not in public_repr
    assert "/scratch/" not in public_repr


def test_task_artifact_explicit_datatype_must_match_known_format_and_kind() -> None:
    task = SimpleNamespace(kind="unit-test")
    with pytest.raises(ValidationError, match=r"datatype.*artifact_format"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.local.v1",
                        "artifact_format": "sfmapi.features.local.v1",
                        "datatype": "sparse_model",
                    }
                ]
            },
        )
    with pytest.raises(ValidationError, match=r"datatype.*kind"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.vendor.v1",
                        "artifact_format": "vendor.features.bundle.v1",
                        "datatype": "sparse_model",
                    }
                ]
            },
        )


def test_task_artifact_schema_version_must_match_known_core_format() -> None:
    task = SimpleNamespace(kind="unit-test")
    with pytest.raises(ValidationError, match=r"schema_version.*artifact_format"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.local.v1",
                        "artifact_format": "sfmapi.features.local.v1",
                        "schema_version": 2,
                    }
                ]
            },
        )
    with pytest.raises(ValidationError, match=r"schema_version.*artifact_format"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.local.v1",
                        "artifact_format": "sfmapi.features.local.v1",
                        "metadata": {"schema_version": 2},
                    }
                ]
            },
        )


def test_task_artifact_reserved_metadata_is_canonicalized() -> None:
    task = SimpleNamespace(kind="unit-test")
    normalized = artifact_service.normalize_task_outputs(
        task,  # type: ignore[arg-type]
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "artifact_format": "sfmapi.features.local.v1",
                    "metadata": {
                        "artifact_format": "sfmapi.reconstruction.sparse.v1",
                        "note": "kept",
                    },
                }
            ]
        },
    )

    artifact = normalized["artifacts"][0]
    assert artifact["artifact_format"] == "sfmapi.features.local.v1"
    assert artifact["datatype"] == "feature_set"
    assert artifact["metadata"]["artifact_format"] == "sfmapi.features.local.v1"
    assert artifact["metadata"]["datatype"] == "feature_set"
    assert artifact["metadata"]["note"] == "kept"


def test_task_artifact_metadata_fallback_must_match_format_and_kind() -> None:
    task = SimpleNamespace(kind="unit-test")
    with pytest.raises(ValidationError, match=r"artifact_format.*kind"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.vendor.v1",
                        "metadata": {
                            "artifact_format": "sfmapi.reconstruction.sparse.v1",
                        },
                    }
                ]
            },
        )
    with pytest.raises(ValidationError, match=r"datatype.*kind"):
        artifact_service.normalize_task_outputs(
            task,  # type: ignore[arg-type]
            {
                "artifacts": [
                    {
                        "kind": "features.vendor.v1",
                        "metadata": {
                            "artifact_format": "vendor.features.bundle.v1",
                            "datatype": "sparse_model",
                        },
                    }
                ]
            },
        )


def test_artifact_out_hides_local_paths_and_exposes_content_href(tmp_path: Path) -> None:
    managed = tmp_path / "managed.bin"
    managed.write_bytes(b"artifact")
    reset_settings_for_tests(workspace_root=tmp_path)
    try:
        base = {
            "artifact_id": new_id(),
            "job_id": new_id(),
            "task_id": new_id(),
            "recon_id": None,
            "dataset_id": None,
            "kind": "custom.output",
            "name": "C:/secret/managed.bin",
            "media_type": "application/octet-stream",
            "summary_json": None,
            "metadata_json": {
                "files": [
                    {
                        "name": "C:/secret/managed.bin",
                        "uri": str(managed),
                        "path": "C:/secret/managed.bin",
                    },
                    {
                        "name": "/workspace/private/other.bin",
                        "uri": "C:/secret/other.bin",
                    },
                ],
            },
            "created_at": utcnow(),
        }
        managed_out = artifact_out(SimpleNamespace(**base, uri=str(managed)))
        assert managed_out.uri == f"/v1/artifacts/{base['artifact_id']}/content"
        assert managed_out.name == "managed.bin"
        assert [item.model_dump(mode="json") for item in managed_out.files] == [
            {
                "name": "managed.bin",
                "uri": f"/v1/artifacts/{base['artifact_id']}/content",
                "media_type": None,
                "sha256": None,
                "byte_size": None,
            },
        ]

        public_uri = "/v1/artifacts/existing/content"
        public_out = artifact_out(
            SimpleNamespace(
                **{**base, "artifact_id": new_id(), "uri": public_uri}
            )
        )
        assert public_out.uri == public_uri

        local_out = artifact_out(
            SimpleNamespace(
                **{**base, "artifact_id": new_id(), "uri": "C:/secret/a.bin"}
            )
        )
        assert local_out.uri is None
    finally:
        reset_settings_for_tests()


def test_artifact_out_preserves_safe_remote_uri_and_drops_credentialed_uri() -> None:
    base = {
        "artifact_id": new_id(),
        "job_id": new_id(),
        "task_id": new_id(),
        "recon_id": None,
        "dataset_id": None,
        "kind": "custom.output",
        "name": None,
        "media_type": "application/octet-stream",
        "summary_json": None,
        "metadata_json": {},
        "created_at": utcnow(),
    }

    safe_out = artifact_out(
        SimpleNamespace(**base, uri="https://artifacts.example/out.bin")
    )
    assert safe_out.uri == "https://artifacts.example/out.bin"

    credentialed_out = artifact_out(
        SimpleNamespace(
            **{
                **base,
                "artifact_id": new_id(),
                "uri": "https://artifacts.example/out.bin?X-Amz-Signature=SECRET",
            }
        )
    )
    assert credentialed_out.uri is None


def test_artifact_out_sanitizes_dirty_stored_summary_and_producer() -> None:
    artifact = SimpleNamespace(
        artifact_id=new_id(),
        job_id=new_id(),
        task_id=new_id(),
        recon_id=None,
        dataset_id=None,
        kind="custom.output",
        name=None,
        uri="mem://artifact/public.bin",
        media_type="application/octet-stream",
        artifact_format=None,
        datatype=None,
        schema_version=None,
        files=[],
        sha256=None,
        byte_size=None,
        coordinate_frame=None,
        producer={"name": "worker", "host_path": "/root/private", "note": "/tmp/run"},
        summary_json={
            "count": 1,
            "local_path": "/root/private/summary.json",
            "note": "wrote /tmp/summary.json",
        },
        metadata_json={
            "local_path": "/root/private/metadata.json",
            "service_url": "http://127.0.0.1:5000/private",
            "producer": {
                "name": "metadata-worker",
                "token": "SECRET_BODY",
                "note": "read C:/secret/input.db",
            },
            "files": [
                {"name": "C:/secret/private.bin", "uri": "C:/secret/private.bin"},
                {"name": "public", "uri": "mem://public"},
            ],
        },
        created_at=utcnow(),
    )

    out = artifact_out(artifact)

    assert out.producer == {"name": "worker"}
    assert out.summary == {"count": 1, "note": "wrote <redacted>"}
    assert out.metadata == {
        "service_url": "<redacted>",
        "producer": {"name": "metadata-worker", "note": "<redacted>"},
        "files": [{"name": "public", "uri": "mem://public"}],
    }
    assert [item.model_dump(mode="json") for item in out.files] == [
        {
            "name": "public",
            "uri": "mem://public",
            "media_type": None,
            "sha256": None,
            "byte_size": None,
        }
    ]
    assert "secret" not in repr(out).lower()


def test_public_outputs_omit_private_file_ref_uris() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    sanitized = sanitize_public_outputs(
        {
            "artifacts": [
                {
                    "kind": "custom.output",
                    "files": [
                        {"uri": "C:/secret/result.bin", "sha256": "a" * 64},
                        {"name": "public.bin", "uri": "mem://artifact/public.bin"},
                        {
                            "name": "api.bin",
                            "uri": "/v1/artifacts/01TEST/content",
                        },
                    ],
                }
            ]
        }
    )

    private_ref = sanitized["artifacts"][0]["files"][0]
    assert private_ref == {"name": "result.bin", "sha256": "a" * 64}
    assert sanitized["artifacts"][0]["files"][1] == {
        "name": "public.bin",
        "uri": "mem://artifact/public.bin",
    }
    assert sanitized["artifacts"][0]["files"][2] == {
        "name": "api.bin",
        "uri": "/v1/artifacts/01TEST/content",
    }


def test_public_outputs_preserve_safe_remote_artifact_uris_only() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    sanitized = sanitize_public_outputs(
        {
            "artifacts": [
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/result.bin",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://user:pass@artifacts.example/result.bin",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/result.bin?token=SECRET",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/result.bin?api_key=abc123",
                },
                {
                    "kind": "custom.output",
                    "uri": "FILE:///C:/secret/result.bin",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/host_path/result.bin",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/sealed_path/result.bin",
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/_container_services/result.bin",
                    "files": [
                        {
                            "name": "bridge.bin",
                            "uri": "https://artifacts.example/_bridge_backend_actions/bridge.bin",
                            "sha256": "b" * 64,
                        }
                    ],
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/result.bin?safe=1;sig=abc123",
                },
                {
                    "kind": "custom.output",
                    "uri": (
                        "https://artifacts.example/result.bin"
                        "?safe=1;X-Amz-Signature=abc123"
                    ),
                },
                {
                    "kind": "custom.output",
                    "uri": "https://artifacts.example/result.bin?privatekey=abc123",
                },
                {
                    "kind": "custom.output",
                    "files": [
                        {
                            "name": "signed.bin",
                            "uri": "https://artifacts.example/signed.bin?safe=1;sigv4=abc123",
                            "sha256": "c" * 64,
                        }
                    ],
                },
            ]
        }
    )

    assert sanitized["artifacts"][0]["uri"] == "https://artifacts.example/result.bin"
    assert "uri" not in sanitized["artifacts"][1]
    assert "uri" not in sanitized["artifacts"][2]
    assert "uri" not in sanitized["artifacts"][3]
    assert sanitized["artifacts"][4]["uri"] is None
    assert "uri" not in sanitized["artifacts"][5]
    assert "uri" not in sanitized["artifacts"][6]
    assert "uri" not in sanitized["artifacts"][7]
    assert sanitized["artifacts"][7]["files"][0] == {
        "name": "bridge.bin",
        "sha256": "b" * 64,
    }
    assert "uri" not in sanitized["artifacts"][8]
    assert "uri" not in sanitized["artifacts"][9]
    assert "uri" not in sanitized["artifacts"][10]
    assert sanitized["artifacts"][11]["files"][0] == {
        "name": "signed.bin",
        "sha256": "c" * 64,
    }


def test_public_artifact_uri_drops_sensitive_fragments() -> None:
    from app.core.public_outputs import sanitize_public_artifact_uri, sanitize_public_outputs

    safe_uri = "https://artifacts.example/result.bin#sha256=abc123"
    assert sanitize_public_artifact_uri(safe_uri) == safe_uri
    for bad_uri in [
        "https://artifacts.example/result.bin#sig=abc123",
        "https://artifacts.example/result.bin#safe=1;X-Amz-Signature=abc123",
        "https://artifacts.example/result.bin#note=_bridge_backend_actions",
        "https://artifacts.example/%68%6f%73%74%5f%70%61%74%68/result.bin",
        "https://artifacts.example/%5fbridge%5fbackend%5factions/result.bin",
        "https://artifacts.example/result.bin;sig=abc123",
        "https://artifacts.example/result.bin;X-Amz-Signature=abc123",
        "https://artifacts.example/result.bin;GoogleAccessId=abc123",
        "https://artifacts.example/result.bin%3Bsig=abc123",
        "https://artifacts.example/result.bin%3FX-Amz-Signature%3Dabc123",
        "https://artifacts.example/result.bin%253FGoogleAccessId%253Dabc123",
        "http://artifacts.example/result.bin",
        "https://localhost/result.bin",
        "https://127.0.0.1/result.bin",
        "https://0177.0.0.1/result.bin",
        "https://012.0.0.1/result.bin",
        "https://192.168.001.001/result.bin",
        "https://0x7f.0.0.1/result.bin",
        "https://0x0a.0.0.1/result.bin",
        "https://0xc0.0xa8.1.1/result.bin",
        "https://%31%30.0.0.4/result.bin",
        "https://100.64.0.1/result.bin",
        "https://192.168.1.20/result.bin",
        "https://192.0.0.1/result.bin",
        "https://192.0.0.170/result.bin",
        "https://192.0.2.1/result.bin",
        "https://198.18.0.1/result.bin",
        "https://198.51.100.1/result.bin",
        "https://203.0.113.1/result.bin",
        "https://[::ffff:127.0.0.1]/result.bin",
        "https://[FC00::1]/result.bin",
        "https://[ff02::1]/result.bin",
        "https://plugin-hloc:8080/result.bin",
        "https://plugin-hloc.internal/result.bin",
        (
            "https://artifacts.example/result.bin"
            "?u=https%3A%2F%2Fs3.amazonaws.com%2Fb%3FX-Amz-Signature%3Dabcdef"
        ),
        (
            "https://artifacts.example/result.bin"
            "#u=https%3A%2F%2Fs3.amazonaws.com%2Fb%3FX-Amz-Signature%3Dabcdef"
        ),
    ]:
        assert sanitize_public_artifact_uri(bad_uri) is None

    sanitized = sanitize_public_outputs(
        {
            "artifacts": [{
                "kind": "custom.output",
                "uri": "https://artifacts.example/result.bin#safe=1;sig=abc123",
                "files": [{
                    "name": "fragment.bin",
                    "uri": "https://artifacts.example/result.bin#X-Amz-Signature=abc123",
                    "sha256": "d" * 64,
                }],
            }]
        }
    )
    assert "uri" not in sanitized["artifacts"][0]
    assert sanitized["artifacts"][0]["files"][0] == {
        "name": "fragment.bin",
        "sha256": "d" * 64,
    }


def test_generic_public_outputs_redact_signed_remote_uri_strings() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    sanitized = sanitize_public_outputs(
        {
            "message": (
                "download "
                "s3://bucket/object?X-Amz-Signature=abcdef"
            ),
            "nested": {
                "note": (
                    "wrapped "
                    "https://example.com/a?u=https%3A%2F%2Fs3.amazonaws.com%2Fb"
                    "%3FX-Amz-Signature%3Dabcdef"
                )
            },
            "encoded": "s3%3A%2F%2Fbucket%2Fobject%3FX-Amz-Signature%3Dabcdef",
        }
    )

    assert sanitized["message"] == "<redacted>"
    assert sanitized["nested"]["note"] == "<redacted>"
    assert sanitized["encoded"] == "<redacted>"


def test_generic_public_outputs_reject_double_encoded_signed_path_params() -> None:
    from app.core.public_outputs import sanitize_public_outputs

    sanitized = sanitize_public_outputs(
        {
            "artifacts": [
                {
                    "kind": "features.local.v1",
                    "uri": (
                        "https://artifacts.example/features.json"
                        "%253BX-Amz-Signature%253Dabcdef"
                    ),
                }
            ]
        }
    )

    assert "uri" not in sanitized["artifacts"][0]


def test_generic_public_outputs_redact_standalone_signed_parameters() -> None:
    from app.core.public_outputs import sanitize_public_error_message, sanitize_public_outputs

    sanitized = sanitize_public_outputs(
        {
            "message": "upload failed X-Amz-Signature=abcdef",
            "encoded": "upload%20failed%20X-Amz-Signature%3Dabcdef",
            "mixed": "upload failed at /tmp/out X-Amz-Signature=abcdef",
            "X-Amz-Signature": "abcdef",
        }
    )

    assert sanitized["message"] == "<redacted>"
    assert sanitized["encoded"] == "<redacted>"
    assert sanitized["mixed"] == "<redacted>"
    assert "X-Amz-Signature" not in sanitized
    assert (
        sanitize_public_error_message("upload failed X-Amz-Signature=abcdef")
        == "task execution failed"
    )
    assert (
        sanitize_public_error_message("upload failed at /tmp/out X-Amz-Signature=abcdef")
        == "task execution failed"
    )


def test_file_uri_parser_accepts_windows_drive_file_uri() -> None:
    from app.services.artifact_conversion_service import _file_uri_to_path

    assert str(_file_uri_to_path("file:///C:/sfmapi/file.bin")).replace(
        "\\",
        "/",
    ) == "C:/sfmapi/file.bin"


def test_public_error_message_redacts_urls_paths_and_secrets() -> None:
    from app.core.public_outputs import sanitize_public_error_message

    message = (
        "provider failed at http://127.0.0.1:8080/execute with "
        "SFMAPI_TOKEN=SECRET_BODY and C:/users/me/private.db"
    )

    assert sanitize_public_error_message(message) == "task execution failed"
    assert (
        sanitize_public_error_message("provider failed at C%3A%5CUsers%5Calice%5Cout.bin")
        == "task execution failed"
    )


def test_public_error_message_redacts_arbitrary_posix_paths_but_keeps_api_links() -> None:
    from app.core.public_outputs import sanitize_public_error_message

    message = (
        "decode failed at /app/run/image.jpg, /usr/local/bin/tool, "
        "\\tmp\\x.mp4 and /scratch/job/out; see /v1/jobs/01TEST "
        "and /v10/jobs/01TEST; bracket /tmp/x]tail, keep abc\\def "
        "and s3://bucket/object, hide file:///tmp/private.db"
    )

    assert sanitize_public_error_message(message) == (
        "decode failed at <redacted>, <redacted>, <redacted> and <redacted>; "
        "see /v1/jobs/01TEST and /v10/jobs/01TEST; bracket <redacted>, "
        "keep abc\\def and <redacted>, hide <redacted>"
    )


def test_job_out_sanitizes_public_error_message() -> None:
    from app.schemas.api.jobs import JobOut

    now = utcnow()
    job = JobOut.model_validate(
        {
            "job_id": "01JOBSANITIZE",
            "tenant_id": "default",
            "project_id": "01PROJECT",
            "recipe": "video.frames",
            "status": "failed",
            "cancel_requested": False,
            "cancel_force": False,
            "created_at": now,
            "started_at": now,
            "finished_at": now,
            "error_class": "FileNotFoundError",
            "error_message": "video file not found: \\tmp\\x.mp4",
        }
    )

    assert job.error_message == "video file not found: <redacted>"


class ArtifactConversionBackend(ArtifactContractBackend):
    name = "artifact_convert"

    def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "contract_id": "artifact_convert.matcher.hloc",
                "stage": "matcher",
                "provider": "artifact_convert",
                "display_name": "hloc match conversion",
                "accepts": ["matches.hloc_h5"],
                "emits": ["matches.indexed.v1"],
                "accepts_formats": ["hloc.matches.h5.v1"],
                "emits_formats": ["sfmapi.matches.indexed.v1"],
                "preferred": "matches.indexed.v1",
                "preferred_format": "sfmapi.matches.indexed.v1",
                "conversions": [
                    {
                        "from_format": "hloc.matches.h5.v1",
                        "to_format": "sfmapi.matches.indexed.v1",
                        "lossless": False,
                        "description": "drops backend-only match scores",
                    }
                ],
            }
        ]

    def convert_artifact(
        self,
        *,
        input_artifact: dict[str, Any],
        output_dir,
        to_format: str,
        to_kind: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "matches.json"
        target.write_text(
            (
                '{"format_id":"sfmapi.matches.indexed.v1",'
                '"schema_version":1,"datatype":"match_graph","pairs":[]}'
            ),
            encoding="utf-8",
        )
        return {
            "artifacts": [
                {
                    "kind": to_kind or "matches.indexed.v1",
                    "name": "converted-matches",
                    "uri": str(target),
                    "media_type": "application/json",
                    "artifact_format": to_format,
                    "schema_version": 1,
                    "producer": {"backend": self.name},
                    "metadata": {"source_artifact_id": input_artifact["artifact_id"]},
                }
            ]
        }


class MultiHopArtifactConversionBackend(ArtifactContractBackend):
    name = "artifact_multihop"

    def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "contract_id": "artifact_multihop.legacy",
                "stage": "matcher",
                "provider": "artifact_multihop",
                "accepts": ["matches.legacy_h5"],
                "emits": ["matches.intermediate.v1"],
                "accepts_formats": ["legacy.matches.h5.v1"],
                "emits_formats": ["intermediate.matches.json.v1"],
                "conversions": [
                    {
                        "from_format": "legacy.matches.h5.v1",
                        "to_format": "intermediate.matches.json.v1",
                        "lossless": True,
                    }
                ],
            },
            {
                "contract_id": "artifact_multihop.portable",
                "stage": "matcher",
                "provider": "artifact_multihop",
                "accepts": ["matches.intermediate.v1"],
                "emits": ["matches.indexed.v1"],
                "accepts_formats": ["intermediate.matches.json.v1"],
                "emits_formats": ["sfmapi.matches.indexed.v1"],
                "conversions": [
                    {
                        "from_format": "intermediate.matches.json.v1",
                        "to_format": "sfmapi.matches.indexed.v1",
                        "lossless": True,
                    }
                ],
            },
        ]

    def convert_artifact(
        self,
        *,
        input_artifact: dict[str, Any],
        output_dir: Path,
        to_format: str,
        to_kind: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{to_format.replace('.', '_')}.json"
        if to_format == "sfmapi.matches.indexed.v1":
            target.write_text(
                (
                    '{"format_id":"sfmapi.matches.indexed.v1",'
                    '"schema_version":1,"datatype":"match_graph","pairs":[]}'
                ),
                encoding="utf-8",
            )
        else:
            target.write_text(
                '{"format_id":"intermediate.matches.json.v1","schema_version":1}',
                encoding="utf-8",
            )
        return {
            "artifacts": [
                {
                    "kind": to_kind or "matches.indexed.v1",
                    "name": f"converted-{to_format}",
                    "uri": str(target),
                    "media_type": "application/json",
                    "artifact_format": to_format,
                    "schema_version": 1,
                    "producer": {"backend": self.name},
                    "metadata": {
                        "source_artifact_id": input_artifact.get("artifact_id"),
                        "input_format": input_artifact.get("artifact_format"),
                    },
                }
            ]
        }


def test_core_artifact_kinds_are_portable() -> None:
    assert "features.local.v1" in artifact_vocab.CORE_ARTIFACT_KINDS
    assert "sfmapi.features.local.v1" in artifact_vocab.CORE_ARTIFACT_FORMATS
    assert "matches.verified.v1" in artifact_vocab.CORE_ARTIFACT_KINDS
    assert "features.database" not in artifact_vocab.CORE_ARTIFACT_KINDS
    verified_format = artifact_vocab.default_format_for_kind("matches.verified.v1")
    assert verified_format is not None
    assert verified_format.format_id == "sfmapi.matches.verified.v1"
    assert artifact_vocab.datatype_for_format("sfmapi.matches.verified.v1") == "match_graph"
    assert artifact_vocab.is_artifact_allowed_for_role("features", "features.hloc_h5")
    assert artifact_vocab.is_artifact_allowed_for_role(
        "verified_matches",
        "matches.database.verified.colmap",
    )
    assert not artifact_vocab.is_artifact_allowed_for_role("features", "matches.indexed.v1")
    assert not artifact_vocab.is_artifact_allowed_for_role(
        "verified_matches",
        "matches.indexed.v1",
    )


def test_extension_artifact_kind_must_match_known_format_datatype() -> None:
    assert artifact_vocab.datatype_for_kind("features.vendor.v1") == "feature_set"
    assert artifact_vocab.is_format_compatible_with_kind(
        "features.vendor.v1",
        "sfmapi.features.local.v1",
    )
    assert not artifact_vocab.is_format_compatible_with_kind(
        "features.vendor.v1",
        "sfmapi.reconstruction.sparse.v1",
    )
    assert artifact_vocab.is_format_compatible_with_kind(
        "features.vendor.v1",
        "vendor.private.format.v1",
    )


def test_backend_artifact_contracts_accept_explicit_provider_rows() -> None:
    rows = backend_artifacts.list_backend_artifact_contracts(ArtifactContractBackend())

    assert rows[0]["contract_id"] == "artifact_test.features.superpoint"
    assert rows[0]["emits"] == ["features.local.v1"]
    assert rows[0]["emits_formats"] == ["sfmapi.features.local.v1"]


async def test_backend_artifact_contract_catalog_is_discoverable(
    db_setup: None,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFMAPI_BACKEND", "artifact_test")
    register_backend("artifact_test", ArtifactContractBackend, providers=["artifact_test"])
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        backend = await client.get("/v1/backend")
        assert backend.status_code == 200
        assert backend.json()["artifact_contract_count"] == 1
        assert backend.json()["_links"]["artifact_contracts"]["href"] == (
            "/v1/backend/artifact-contracts"
        )

        caps = detect_capabilities()
        assert caps.supports("backend.artifact_contracts")

        page = await client.get("/v1/backend/artifact-contracts")
        assert page.status_code == 200, page.text
        item = page.json()["items"][0]
        assert item["contract_id"] == "artifact_test.features.superpoint"
        assert item["preferred"] == "features.local.v1"
        assert item["preferred_format"] == "sfmapi.features.local.v1"

        formats = await client.get("/v1/artifacts/formats")
        assert formats.status_code == 200, formats.text
        format_ids = {row["format_id"] for row in formats.json()["items"]}
        assert "sfmapi.matches.verified.v1" in format_ids
        match_format = next(
            row
            for row in formats.json()["items"]
            if row["format_id"] == "sfmapi.matches.indexed.v1"
        )
        assert match_format["json_schema"]["required"]
        for item in formats.json()["items"]:
            schema = item.get("json_schema") or {}
            sha_schema = (
                schema.get("properties", {})
                .get("files", {})
                .get("items", {})
                .get("properties", {})
                .get("sha256", {})
            )
            if sha_schema:
                assert sha_schema["pattern"] == "^[0-9a-f]{64}$"


def test_backend_artifact_contracts_can_be_inferred_from_capabilities() -> None:
    class CapabilityOnlyBackend(StubBackend):
        name = "cap_only"
        version = "1.0"

        def capabilities(self) -> set[str]:
            return {"features.extract.disk", "matchers.loftr", "map.spherical"}

    rows = backend_artifacts.list_backend_artifact_contracts(CapabilityOnlyBackend())
    by_id = {row["contract_id"]: row for row in rows}

    assert by_id["cap_only.features.disk"]["emits"] == ["features.local.v1"]
    assert by_id["cap_only.features.disk"]["emits_formats"] == ["sfmapi.features.local.v1"]
    assert by_id["cap_only.matcher.loftr"]["emits"] == ["matches.coordinates.v1"]
    assert "reconstruction.sparse.v1" in by_id["cap_only.mapping.spherical"]["emits"]


def test_backend_artifact_contract_rejects_conversion_without_method() -> None:
    class MissingConverterBackend(ArtifactConversionBackend):
        convert_artifact = None  # type: ignore[assignment]

    violations = backend_artifacts.backend_artifact_contract_violations(MissingConverterBackend())

    assert any("convert_artifact() must be implemented" in violation for violation in violations)


def test_backend_artifact_contract_rejects_stub_converter() -> None:
    class StubConverterBackend(StubBackend):
        name = "stub_converter"

        def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
            return ArtifactConversionBackend().list_backend_artifact_contracts()

    violations = backend_artifacts.backend_artifact_contract_violations(StubConverterBackend())

    assert any("convert_artifact() must be implemented" in violation for violation in violations)


def test_backend_io_formats_derive_datatype_per_contract_side() -> None:
    class CrossDatatypeSideBackend(StubBackend):
        name = "cross_datatype_side"

        def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
            return [
                {
                    "contract_id": "cross_datatype_side.matcher",
                    "stage": "matcher",
                    "accepts": ["features.local.v1"],
                    "emits": ["matches.indexed.v1"],
                    "accepts_formats": ["plugin.features.bundle.v1"],
                    "emits_formats": ["plugin.matches.bundle.v1"],
                }
            ]

    formats = {
        item.format_id: item
        for item in backend_artifacts.backend_io_formats(CrossDatatypeSideBackend())
    }
    violations = backend_artifacts.backend_artifact_contract_violations(
        CrossDatatypeSideBackend()
    )

    assert formats["plugin.features.bundle.v1"].datatype == "feature_set"
    assert formats["plugin.features.bundle.v1"].serves_kinds == ("features.local.v1",)
    assert formats["plugin.matches.bundle.v1"].datatype == "match_graph"
    assert formats["plugin.matches.bundle.v1"].serves_kinds == ("matches.indexed.v1",)
    assert not violations


def test_backend_artifact_contract_rejects_cross_datatype_formats() -> None:
    class MismatchedFormatBackend(StubBackend):
        name = "mismatched_format"

        def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
            return [
                {
                    "contract_id": "mismatched.features",
                    "stage": "features",
                    "accepts": ["features.local.v1"],
                    "emits": ["features.local.v1"],
                    "accepts_formats": ["sfmapi.reconstruction.sparse.v1"],
                    "emits_formats": ["sfmapi.reconstruction.sparse.v1"],
                }
            ]

    violations = backend_artifacts.backend_artifact_contract_violations(
        MismatchedFormatBackend()
    )

    assert any("accepts_formats format" in violation for violation in violations)
    assert any("emits_formats format" in violation for violation in violations)


def test_backend_artifact_contract_rejects_plugin_format_datatype_mismatch() -> None:
    class PluginFormatMismatchBackend(StubBackend):
        name = "plugin_format_mismatch"

        def artifact_formats(self) -> list[dict[str, Any]]:
            return [
                {
                    "format_id": "plugin.reconstruction.bytes.v1",
                    "datatype": "sparse_model",
                    "title": "Wrong format",
                }
            ]

        def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
            return [
                {
                    "contract_id": "plugin.features",
                    "stage": "features",
                    "accepts": [],
                    "emits": ["features.local.v1"],
                    "accepts_formats": [],
                    "emits_formats": ["plugin.reconstruction.bytes.v1"],
                }
            ]

    violations = backend_artifacts.backend_artifact_contract_violations(
        PluginFormatMismatchBackend()
    )

    assert any("emits_formats format" in violation for violation in violations)


def test_backend_artifact_contract_rejects_plugin_format_without_datatype() -> None:
    class PluginFormatMissingDatatypeBackend(StubBackend):
        name = "plugin_format_missing_datatype"

        def artifact_formats(self) -> list[dict[str, Any]]:
            return [
                {
                    "format_id": "plugin.features.bundle.v1",
                    "title": "Missing datatype",
                }
            ]

        def list_backend_artifact_contracts(self) -> list[dict[str, Any]]:
            return [
                {
                    "contract_id": "plugin.features",
                    "stage": "features",
                    "accepts": [],
                    "emits": ["features.local.v1"],
                    "accepts_formats": [],
                    "emits_formats": ["plugin.features.bundle.v1"],
                }
            ]

    violations = backend_artifacts.backend_artifact_contract_violations(
        PluginFormatMissingDatatypeBackend()
    )

    assert any("artifact format datatype is required" in violation for violation in violations)
    assert any("has no declared Data Type" in violation for violation in violations)


async def test_artifact_conversion_plan_convert_and_validate_api(db_setup, monkeypatch) -> None:
    monkeypatch.setenv("SFMAPI_BACKEND", "stub")
    register_backend("stub", StubBackend)
    register_backend("artifact_convert", ArtifactConversionBackend, providers=["artifact_convert"])
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.db.session import get_session_factory
    from app.main import create_app

    factory = get_session_factory()
    async with factory() as session:
        project = Project(tenant_id="default", name="artifact-conversion")
        session.add(project)
        await session.flush()
        job = Job(
            tenant_id="default",
            project_id=project.project_id,
            recipe="seed",
            spec_json={},
            status="succeeded",
        )
        session.add(job)
        await session.flush()
        task = Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=job.job_id,
            kind="seed",
            inputs_hash="i" * 64,
            params_hash="p" * 64,
            runtime_version_id="rv",
            cache_key=content_address(b"seed"),
            status="succeeded",
        )
        session.add(task)
        await session.flush()
        source = StageArtifact(
            tenant_id="default",
            job_id=job.job_id,
            task_id=task.task_id,
            kind="matches.hloc_h5",
            name="hloc-matches",
            uri="memory://matches.h5",
            metadata_json={
                "artifact_format": "hloc.matches.h5.v1",
                "datatype": "match_graph",
                "schema_version": 1,
            },
        )
        session.add(source)
        await session.commit()
        source_id = source.artifact_id

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        plan = await client.post(
            f"/v1/artifacts/{source_id}:conversionPlan",
            json={
                "provider": "artifact_convert",
                "accepted_formats": ["sfmapi.matches.indexed.v1"],
            },
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["executable"] is True
        assert plan.json()["target_format"] == "sfmapi.matches.indexed.v1"
        assert plan.json()["steps"][0]["provider"] == "artifact_convert"

        submitted = await client.post(
            f"/v1/artifacts/{source_id}:convert",
            json={
                "provider": "artifact_convert",
                "accepted_formats": ["sfmapi.matches.indexed.v1"],
            },
        )
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["provider"] == "artifact_convert"
        job_id = submitted.json()["job_id"]
        job_body = (await client.get(f"/v1/jobs/{job_id}")).json()
        assert job_body["status"] == "succeeded"

        artifacts = await client.get(f"/v1/jobs/{job_id}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        converted = artifacts.json()["items"][0]
        assert converted["kind"] == "matches.indexed.v1"
        assert converted["artifact_format"] == "sfmapi.matches.indexed.v1"

        validation = await client.post(f"/v1/artifacts/{converted['artifact_id']}:validate")
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is True
        assert validation.json()["checked_content"] is True


async def test_artifact_conversion_supports_multihop_paths(db_setup, monkeypatch) -> None:
    monkeypatch.setenv("SFMAPI_BACKEND", "artifact_multihop")
    register_backend(
        "artifact_multihop",
        MultiHopArtifactConversionBackend,
        providers=["artifact_multihop"],
    )
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.db.session import get_session_factory
    from app.main import create_app

    factory = get_session_factory()
    async with factory() as session:
        project = Project(tenant_id="default", name="artifact-multihop")
        session.add(project)
        await session.flush()
        job = Job(
            tenant_id="default",
            project_id=project.project_id,
            recipe="seed",
            spec_json={},
            status="succeeded",
        )
        session.add(job)
        await session.flush()
        task = Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=job.job_id,
            kind="seed",
            inputs_hash="i" * 64,
            params_hash="p" * 64,
            runtime_version_id="rv",
            cache_key=content_address(b"seed-multihop"),
            status="succeeded",
        )
        session.add(task)
        await session.flush()
        source = StageArtifact(
            tenant_id="default",
            job_id=job.job_id,
            task_id=task.task_id,
            kind="matches.legacy_h5",
            name="legacy-matches",
            uri="memory://matches.h5",
            metadata_json={
                "artifact_format": "legacy.matches.h5.v1",
                "datatype": "match_graph",
                "schema_version": 1,
            },
        )
        session.add(source)
        await session.commit()
        source_id = source.artifact_id

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        plan = await client.post(
            f"/v1/artifacts/{source_id}:conversionPlan",
            json={"accepted_formats": ["sfmapi.matches.indexed.v1"], "require_lossless": True},
        )
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["executable"] is True
        assert [step["to_format"] for step in body["steps"]] == [
            "intermediate.matches.json.v1",
            "sfmapi.matches.indexed.v1",
        ]

        submitted = await client.post(
            f"/v1/artifacts/{source_id}:convert",
            json={"accepted_formats": ["sfmapi.matches.indexed.v1"], "require_lossless": True},
        )
        assert submitted.status_code == 202, submitted.text
        artifacts = await client.get(f"/v1/jobs/{submitted.json()['job_id']}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        converted = artifacts.json()["items"][0]
        assert converted["kind"] == "matches.indexed.v1"
        assert converted["artifact_format"] == "sfmapi.matches.indexed.v1"
        assert len(converted["metadata"]["conversion"]["steps"]) == 2


async def test_artifact_import_and_integrity_validation_api(
    db_setup,
    request: pytest.FixtureRequest,
) -> None:
    workspace = request.getfixturevalue("_isolate_workspace")
    assert isinstance(workspace, Path)
    reset_settings_for_tests()
    from app.db.session import get_session_factory
    from app.main import create_app

    artifact_path = workspace / "pairs.json"
    content = (
        '{"format_id":"sfmapi.pairs.image_names.v1",'
        '"schema_version":1,"datatype":"pair_set","pairs":[["a.jpg","b.jpg"]]}'
    )
    artifact_path.write_text(content, encoding="utf-8")

    factory = get_session_factory()
    async with factory() as session:
        project = Project(tenant_id="default", name="artifact-import")
        session.add(project)
        await session.commit()
        project_id = project.project_id

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        imported = await client.post(
            "/v1/artifacts:import",
            json={
                "project_id": project_id,
                "kind": "pairs.image_names.v1",
                "name": "manual-pairs",
                "uri": str(artifact_path),
                "media_type": "application/json",
                "artifact_format": "sfmapi.pairs.image_names.v1",
                "sha256": "0" * 64,
            },
        )
        assert imported.status_code == 201, imported.text
        body = imported.json()
        assert body["kind"] == "pairs.image_names.v1"
        assert body["artifact_format"] == "sfmapi.pairs.image_names.v1"

        listed = await client.get(f"/v1/jobs/{body['job_id']}/artifacts")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["artifact_id"] == body["artifact_id"]

        validation = await client.post(f"/v1/artifacts/{body['artifact_id']}:validate")
        assert validation.status_code == 200, validation.text
        report = validation.json()
        assert report["checked_content"] is True
        assert report["valid"] is False
        assert any("sha256 does not match" in issue["message"] for issue in report["issues"])


async def test_artifact_validation_rejects_uppercase_sha_metadata(
    db_setup,
    request: pytest.FixtureRequest,
) -> None:
    workspace = request.getfixturevalue("_isolate_workspace")
    assert isinstance(workspace, Path)
    reset_settings_for_tests()
    from app.db.session import get_session_factory
    from app.main import create_app

    file_path = workspace / "artifact.json"
    file_bytes = b'{"format_id":"sfmapi.pairs.image_names.v1","schema_version":1,"pairs":[]}'
    file_path.write_bytes(file_bytes)
    file_sha = hashlib.sha256(file_bytes).hexdigest()
    snapshot_dir = workspace / "snapshot"
    snapshot_dir.mkdir()
    points = snapshot_dir / "points.bin"
    points.write_bytes(b"points")
    points_sha = hashlib.sha256(b"points").hexdigest()

    factory = get_session_factory()
    async with factory() as session:
        project = Project(tenant_id="default", name="artifact-uppercase-sha")
        session.add(project)
        await session.flush()
        job = Job(
            tenant_id="default",
            project_id=project.project_id,
            recipe="seed",
            spec_json={},
            status="succeeded",
        )
        session.add(job)
        await session.flush()
        task = Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=job.job_id,
            kind="seed",
            inputs_hash="i" * 64,
            params_hash="p" * 64,
            runtime_version_id="rv",
            cache_key=content_address(b"uppercase-sha"),
            status="succeeded",
        )
        session.add(task)
        await session.flush()
        file_artifact = StageArtifact(
            tenant_id="default",
            job_id=job.job_id,
            task_id=task.task_id,
            kind="pairs.image_names.v1",
            name="artifact.json",
            uri=str(file_path),
            media_type="application/json",
            metadata_json={
                "artifact_format": "sfmapi.pairs.image_names.v1",
                "datatype": "pair_set",
                "schema_version": 1,
                "sha256": file_sha.upper(),
            },
        )
        dir_artifact = StageArtifact(
            tenant_id="default",
            job_id=job.job_id,
            task_id=task.task_id,
            kind="reconstruction.snapshot",
            name="snapshot",
            uri=str(snapshot_dir),
            metadata_json={
                "artifact_format": "sfmapi.reconstruction.snapshot.v1",
                "datatype": "sparse_model",
                "schema_version": 1,
                "files": [{"name": "points.bin", "uri": "points.bin", "sha256": points_sha.upper()}],
            },
        )
        session.add_all([file_artifact, dir_artifact])
        await session.commit()
        file_artifact_id = file_artifact.artifact_id
        dir_artifact_id = dir_artifact.artifact_id

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        file_validation = await client.post(f"/v1/artifacts/{file_artifact_id}:validate")
        assert file_validation.status_code == 200, file_validation.text
        assert any(
            issue["field"] == "metadata"
            and "lowercase hex SHA-256" in issue["message"]
            for issue in file_validation.json()["issues"]
        )

        dir_validation = await client.post(f"/v1/artifacts/{dir_artifact_id}:validate")
        assert dir_validation.status_code == 200, dir_validation.text
        assert any(
            issue["field"] == "files[0]"
            and "lowercase hex SHA-256" in issue["message"]
            for issue in dir_validation.json()["issues"]
        )


def test_public_resource_specs_sanitize_backend_options() -> None:
    from datetime import datetime, timezone

    from app.schemas.api.radiance import RadianceEvaluationOut, RadianceFieldOut
    from app.schemas.api.reconstructions import ReconstructionOut

    now = datetime.now(timezone.utc)
    recon = ReconstructionOut(
        recon_id="r1",
        project_id="p1",
        dataset_id="d1",
        dataset_snapshot_hash="h",
        spec_json={
            "kind": "incremental",
            "version": 1,
            "backend_options": {
                "safe": 3,
                "api_key": "abc123",
                "cache_path": "C:/tmp/private/cache.bin",
            },
        },
        rv_id="rv",
        status="succeeded",
        created_at=now,
    ).model_dump(mode="json", by_alias=True)
    recon_options = recon["spec"]["backend_options"]
    assert recon_options["safe"] == 3
    assert "api_key" not in recon_options
    assert recon_options["cache_path"] == "<redacted>"

    radiance = RadianceFieldOut(
        radiance_field_id="rf1",
        project_id="p1",
        dataset_id="d1",
        recon_id=None,
        name="field",
        provider="stub",
        method="stub",
        status="succeeded",
        spec_json={
            "method": "stub",
            "backend_options": {
                "signed_uri": "https://example.invalid/out.bin?api%5Fkey=abc123",
                "quality": "draft",
            },
        },
        summary_json={
            "encoded": "s3%3A%2F%2Fbucket%2Fmodel%3FX-Amz-Signature%3Dabc",
            "local": "C:/tmp/private/model.ply",
        },
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json", by_alias=True)
    radiance_options = radiance["spec"]["backend_options"]
    assert radiance_options["quality"] == "draft"
    assert radiance_options["signed_uri"] == "<redacted>"
    assert radiance["summary"]["encoded"] == "<redacted>"
    assert radiance["summary"]["local"] == "<redacted>"

    evaluation = RadianceEvaluationOut(
        evaluation_id="e1",
        radiance_field_id="rf1",
        snapshot_seq=1,
        dataset_id="d1",
        provider="stub",
        method="stub",
        split="test",
        status="succeeded",
        config_json={
            "backend_options": {
                "local_path": "/tmp/private/render.json",
                "max_images": 2,
            },
        },
        metrics_json={
            "psnr_db": 30.0,
            "debug": "upload failed X-Amz-Signature=abcdef",
        },
        artifacts_json=[
            {
                "kind": "radiance.evaluation.metrics",
                "uri": "https://example.invalid/metrics.json?X-Amz-Signature=abc",
                "metadata": {"cache_path": "/tmp/private/metrics.json"},
            }
        ],
        error_json=None,
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json", by_alias=True)
    eval_options = evaluation["config"]["backend_options"]
    assert eval_options["max_images"] == 2
    assert "local_path" not in eval_options
    assert evaluation["metrics"]["psnr_db"] == 30.0
    assert evaluation["metrics"]["debug"] == "<redacted>"
    assert "uri" not in evaluation["artifacts"][0]
    assert evaluation["artifacts"][0]["metadata"]["cache_path"] == "metrics.json"

    failed_evaluation = RadianceEvaluationOut(
        evaluation_id="e2",
        radiance_field_id="rf1",
        snapshot_seq=1,
        dataset_id="d1",
        provider="stub",
        method="stub",
        split="test",
        status="failed",
        config_json={},
        metrics_json=None,
        artifacts_json=None,
        error_json={
            "code": "provider_failed",
            "message": (
                "failed at C:/tmp/private/render.json with "
                "https://example.invalid/out.bin?api_key=abc123"
            ),
            "details": {
                "host_path": "/workspace/output/private.json",
                "safe": "kept",
            },
        },
        created_at=now,
        updated_at=now,
    ).model_dump(mode="json", by_alias=True)
    assert failed_evaluation["error"] == {
        "code": "provider_failed",
        "message": "task execution failed",
        "details": {"safe": "kept"},
    }


async def test_radiance_metrics_routes_sanitize_persisted_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import radiance as radiance_routes

    async def fake_get_radiance_evaluation(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            metrics_json={
                "psnr_db": 30.0,
                "debug": "upload failed X-Amz-Signature=abcdef",
            }
        )

    monkeypatch.setattr(
        radiance_routes.radiance_service,
        "get_radiance_evaluation",
        fake_get_radiance_evaluation,
    )

    metrics = await radiance_routes.get_radiance_evaluation_metrics(
        "eval1",
        tenant_id="default",
        session=None,  # type: ignore[arg-type]
    )
    artifact = await radiance_routes._read_radiance_evaluation_metrics_artifact(
        "eval1",
        tenant_id="default",
        session=None,  # type: ignore[arg-type]
    )

    assert metrics.model_dump()["debug"] == "<redacted>"
    assert b"X-Amz-Signature" not in artifact.body
    assert b"<redacted>" in artifact.body
