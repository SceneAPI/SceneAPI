from __future__ import annotations

import pytest

from sceneapi.server.core.ids import ID_LEN, is_id, new_id

pytestmark = pytest.mark.unit


def test_new_id_length() -> None:
    nid = new_id()
    assert len(nid) == ID_LEN
    assert is_id(nid)


def test_new_id_unique_under_loop() -> None:
    seen = {new_id() for _ in range(2000)}
    assert len(seen) == 2000


def test_new_id_sortable_in_time_order() -> None:
    a = new_id()
    b = new_id()
    # ULIDs are time-ordered to ms; b >= a even if generated in same ms.
    assert b >= a


def test_is_id_rejects_garbage() -> None:
    assert not is_id("")
    assert not is_id("not-a-ulid")
    assert not is_id("a" * 27)
