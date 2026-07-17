from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog

from sceneapi.server.core.logging import (
    JobFileLogger,
    bind_job_context,
    configure_logging,
    get_logger,
)

pytestmark = pytest.mark.unit


def test_job_context_propagates_to_records(capfd: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    log = get_logger("t")
    with bind_job_context(
        tenant_id="t-A", project_id="p-1", job_id="j-99", task_id="k-7", phase="extract"
    ):
        log.info("hello", extra="x")
    captured = capfd.readouterr().out.strip().splitlines()
    assert captured, "no log output captured"
    record = json.loads(captured[-1])
    assert record["tenant_id"] == "t-A"
    assert record["project_id"] == "p-1"
    assert record["job_id"] == "j-99"
    assert record["task_id"] == "k-7"
    assert record["phase"] == "extract"
    assert record["event"] == "hello"

    structlog.contextvars.clear_contextvars()


def test_job_file_logger_appends_jsonl(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "j1"
    jl = JobFileLogger(job_dir)
    jl.emit("info", "phase_started", phase="extract")
    jl.emit("warning", "soft_failure", reason="x")
    lines = (job_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "phase_started"
    assert json.loads(lines[1])["level"] == "WARNING"
