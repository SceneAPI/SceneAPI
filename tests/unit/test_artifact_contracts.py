from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters import backend_artifacts
from app.adapters.registry import register_backend
from app.adapters.stub_backend import StubBackend
from app.core import artifacts as artifact_vocab
from app.core.capabilities import detect_capabilities, reset_capabilities_cache
from app.core.config import reset_settings_for_tests
from app.core.hashing import content_address
from app.core.ids import new_id
from app.db.models import Job, Project, StageArtifact, Task


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
                '"schema_version":1,"artifact_type":"matches","pairs":[]}'
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
                    '"schema_version":1,"artifact_type":"matches","pairs":[]}'
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
    assert artifact_vocab.artifact_type_for_format("sfmapi.matches.verified.v1") == "matches"
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
    register_backend("artifact_test", ArtifactContractBackend)
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


async def test_artifact_conversion_plan_convert_and_validate_api(db_setup, monkeypatch) -> None:
    monkeypatch.setenv("SFMAPI_BACKEND", "artifact_convert")
    register_backend("artifact_convert", ArtifactConversionBackend)
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
                "artifact_type": "matches",
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
            json={"accepted_formats": ["sfmapi.matches.indexed.v1"]},
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["executable"] is True
        assert plan.json()["target_format"] == "sfmapi.matches.indexed.v1"

        submitted = await client.post(
            f"/v1/artifacts/{source_id}:convert",
            json={"accepted_formats": ["sfmapi.matches.indexed.v1"]},
        )
        assert submitted.status_code == 202, submitted.text
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
    register_backend("artifact_multihop", MultiHopArtifactConversionBackend)
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
                "artifact_type": "matches",
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
        '"schema_version":1,"artifact_type":"pairs","pairs":[["a.jpg","b.jpg"]]}'
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
