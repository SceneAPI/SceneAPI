"""Inline request models reject unknown fields.

The schema models in ``sfmapi/server/schemas/api/`` use ``extra="forbid"`` so a
typo'd field 422s loudly. The request models defined *inline* in the
route modules historically didn't — a typo'd ``provder`` was silently
dropped and the job ran on the wrong backend. These tests pin
``extra="forbid"`` on every inline request model. Body validation runs
before the route handler, so a fake parent id still surfaces the 422.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_FAKE_ID = "01H00000000000000000000000"
_SHA = "a" * 64


async def test_localization_request_rejects_unknown_field(client) -> None:
    resp = await client.post(
        f"/v1/reconstructions/{_FAKE_ID}/localize",
        json={"blob_sha": _SHA, "provder": "typo"},
    )
    assert resp.status_code == 422, resp.text


async def test_merge_request_rejects_unknown_field(client) -> None:
    resp = await client.post(
        "/v1/reconstructions:merge",
        json={
            "target_recon_id": _FAKE_ID,
            "source_recon_ids": [_FAKE_ID],
            "bogus": 1,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_video_frames_request_rejects_unknown_field(client) -> None:
    resp = await client.post(
        f"/v1/projects/{_FAKE_ID}/datasets:fromVideo",
        json={"video_path": "/tmp/v.mp4", "frames_per_sec": 2},
    )
    assert resp.status_code == 422, resp.text


async def test_kapture_import_request_rejects_unknown_field(client) -> None:
    resp = await client.post(
        f"/v1/projects/{_FAKE_ID}/datasets:importKapture",
        json={"archive_path": "/tmp/k", "extra": True},
    )
    assert resp.status_code == 422, resp.text


async def test_pipeline_request_rejects_unknown_field(client) -> None:
    resp = await client.post(
        f"/v1/projects/{_FAKE_ID}/pipelines/incremental",
        json={
            "dataset_id": _FAKE_ID,
            "spec": {"kind": "incremental"},
            "unknown_key": "x",
        },
    )
    assert resp.status_code == 422, resp.text
