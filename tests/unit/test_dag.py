from __future__ import annotations

import pytest

from sceneapi.server.orchestrator.dag import TaskNode

pytestmark = pytest.mark.unit


def test_cache_key_stable() -> None:
    n1 = TaskNode(task_id="x", kind="extract", inputs_hash="abc", params_hash="def")
    n2 = TaskNode(task_id="y", kind="extract", inputs_hash="abc", params_hash="def")
    assert n1.cache_key("rv1") == n2.cache_key("rv1")
    assert n1.cache_key("rv1") != n1.cache_key("rv2")
