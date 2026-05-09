from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db.models import Task
from app.workers.tasks import match as match_task

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

    monkeypatch.setattr(match_task, "get_backend", lambda: Backend())

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
