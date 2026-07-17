from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sfmapi.server.core.errors import ValidationError
from sfmapi.server.db.models import Task
from sfmapi.server.workers.tasks import match as match_task

pytestmark = pytest.mark.unit


def test_match_worker_materializes_explicit_inline_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class Backend:
        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            captured["database_path"] = database_path
            captured["mode"] = mode
            captured["options"] = options
            pairs_path = Path(options["pairs"]["pairs_path"])
            captured["pairs_text"] = pairs_path.read_text(encoding="utf-8")
            return {"num_pairs": 1, "num_matches": 0}

        def iter_correspondences(self, *, database_path: Path):
            return iter(())

    monkeypatch.setattr(match_task, "backend_for_match_stage", lambda pairs, matcher: Backend())

    db_path = tmp_path / "database.db"
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {"database_path": str(db_path)},
            "spec": {
                "pairs": {
                    "strategy": "explicit",
                    "provider": "hloc",
                    "image_pairs": [{"image_name1": "a.jpg", "image_name2": "b.jpg"}],
                },
                "matcher": {"type": "superglue", "provider": "hloc"},
            },
        },
    )

    out = match_task.run(task)

    assert out["strategy"] == "explicit"
    assert captured["mode"] == "explicit"
    assert captured["pairs_text"] == "a.jpg b.jpg\n"
    assert captured["options"]["pairs_provider"] == "hloc"
    assert captured["options"]["matcher_provider"] == "hloc"
    assert captured["options"]["pairs"]["provider"] == "hloc"
    assert captured["options"]["matcher"]["provider"] == "hloc"
    assert "provider" not in captured["options"]


def test_match_worker_uses_precomputed_pairs_artifact_with_matcher_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    pairs_file = tmp_path / "hloc-pairs.txt"
    pairs_file.write_text("a.jpg b.jpg\n", encoding="utf-8")

    class Backend:
        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            captured["database_path"] = database_path
            captured["mode"] = mode
            captured["options"] = options
            captured["pairs_text"] = Path(options["pairs"]["pairs_path"]).read_text(
                encoding="utf-8"
            )
            return {"num_pairs": 1, "num_matches": 4}

        def iter_correspondences(self, *, database_path: Path):
            return iter(())

    def resolve_backend(pairs: dict[str, Any], matcher: dict[str, Any]) -> Backend:
        captured["resolved_pairs_provider"] = pairs.get("provider")
        captured["resolved_matcher_provider"] = matcher.get("provider")
        return Backend()

    monkeypatch.setattr(match_task, "backend_for_match_stage", resolve_backend)

    db_path = tmp_path / "database.db"
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {
                "database_path": str(db_path),
                "input_artifacts": {
                    "pairs": {
                        "kind": "pairs.image_names.v1",
                        "uri": str(pairs_file),
                        "media_type": "text/plain",
                        "artifact_format": "sfmapi.pairs.image_names.v1",
                        "metadata": {"producer": {"backend": "hloc"}},
                    }
                },
            },
            "spec": {
                "pairs": {
                    "strategy": "explicit",
                    "input_artifacts": {
                        "pairs": {
                            "artifact_id": "01H00000000000000000000002",
                            "kind": "pairs.image_names.v1",
                        }
                    },
                },
                "matcher": {
                    "type": "lightglue",
                    "provider": "vismatch",
                    "backend_options": {"model": "xfeat"},
                },
            },
        },
    )

    out = match_task.run(task)

    assert out["strategy"] == "explicit"
    assert captured["mode"] == "explicit"
    assert captured["pairs_text"] == "a.jpg b.jpg\n"
    assert captured["resolved_pairs_provider"] is None
    assert captured["resolved_matcher_provider"] == "vismatch"
    assert captured["options"]["matcher_provider"] == "vismatch"
    assert captured["options"]["pairs"]["pairs_path"] == str(pairs_file)
    assert (
        captured["options"]["input_artifacts"]["pairs"]["metadata"]["producer"]["backend"] == "hloc"
    )


def test_match_worker_converts_json_pairs_artifact_to_pair_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    pairs_json = tmp_path / "pairs.json"
    pairs_json.write_text(
        json.dumps(
            {
                "format_id": "sfmapi.pairs.image_names.v1",
                "schema_version": 1,
                "datatype": "pair_set",
                "pairs": [["a.jpg", "b.jpg"], {"image1": "b.jpg", "image2": "c.jpg"}],
            }
        ),
        encoding="utf-8",
    )

    class Backend:
        def match(self, *, database_path: Path, mode: str, options: dict) -> dict:
            pairs_path = Path(options["pairs"]["pairs_path"])
            captured["pairs_path"] = pairs_path
            captured["pairs_text"] = pairs_path.read_text(encoding="utf-8")
            return {"num_pairs": 2, "num_matches": 8}

        def iter_correspondences(self, *, database_path: Path):
            return iter(())

    monkeypatch.setattr(match_task, "backend_for_match_stage", lambda pairs, matcher: Backend())

    task = Task(
        task_id="01H00000000000000000000003",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {
                "database_path": str(tmp_path / "database.db"),
                "input_artifacts": {
                    "pairs": {
                        "kind": "pairs.image_names.v1",
                        "uri": str(pairs_json),
                        "media_type": "application/json",
                        "artifact_format": "sfmapi.pairs.image_names.v1",
                    }
                },
            },
            "spec": {
                "pairs": {"strategy": "explicit"},
                "matcher": {"type": "lightglue", "provider": "vismatch"},
            },
        },
    )

    out = match_task.run(task)

    assert out["num_pairs"] == 2
    assert captured["pairs_path"].name == "input_artifact_pairs.txt"
    assert captured["pairs_text"] == "a.jpg b.jpg\nb.jpg c.jpg\n"


def test_match_worker_rejects_mixed_pair_and_matcher_providers(tmp_path: Path) -> None:
    db_path = tmp_path / "database.db"
    task = Task(
        task_id="01H00000000000000000000000",
        tenant_id="default",
        job_id="01H00000000000000000000001",
        kind="match",
        inputs_hash="i" * 64,
        params_hash="p" * 64,
        runtime_version_id="rv",
        cache_key="c" * 64,
        task_state_json={
            "inputs": {"database_path": str(db_path)},
            "spec": {
                "pairs": {"strategy": "exhaustive", "provider": "hloc"},
                "matcher": {"type": "sift", "provider": "colmap_cli"},
            },
        },
    )

    with pytest.raises(ValidationError, match="different providers"):
        match_task.run(task)
