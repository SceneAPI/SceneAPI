from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")


def _seed_bucket(name: str, files: dict[str, bytes]) -> None:
    import boto3

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=name)
    for key, data in files.items():
        s3.put_object(Bucket=name, Key=key, Body=data)


def test_s3_source_lists_and_materializes(s3_env, tmp_path: Path) -> None:
    moto = pytest.importorskip("moto")
    from sfmapi.server.sources.s3 import S3Source

    with moto.mock_aws():
        _seed_bucket(
            "sfm-test",
            {
                "scenes/a.jpg": b"\xff\xd8\xff\xe0aaa",
                "scenes/b.png": b"\x89PNG\r\nbbb",
                "scenes/readme.txt": b"ignore me",
            },
        )
        src = S3Source(bucket="sfm-test", prefix="scenes/")
        fp = src.fingerprint()
        assert len(fp["objects"]) == 2

        mats = src.materialize()
        names = sorted(m.name for m in mats)
        assert names == ["a.jpg", "b.png"]
        for m in mats:
            assert m.abs_path.is_file()


def test_s3_cache_lru_evicts_oldest(s3_env, monkeypatch) -> None:
    import os

    from sfmapi.server.storage.s3_cache import S3Cache

    cache = S3Cache()
    a = cache.insert(bucket="b", key="a", etag="1", src_bytes=b"x" * 1024)
    b = cache.insert(bucket="b", key="b", etag="1", src_bytes=b"y" * 4096)
    c = cache.insert(bucket="b", key="c", etag="1", src_bytes=b"z" * 1024)
    assert cache.total_bytes() == 1024 + 4096 + 1024

    # Force deterministic mtimes: oldest=b, then a, then c.
    for entry, ts in ((b, 100.0), (a, 200.0), (c, 300.0)):
        os.utime(entry.path.with_suffix(".meta.json"), (ts, ts))

    # Total 6144, target 4000 -> need to free 2144.
    # Iterating oldest-first, evicting b alone frees 4096 -> total 2048, done.
    freed = cache.evict_to(max_bytes=4000)
    assert freed >= 2144
    assert b.path.exists() is False
    assert a.path.exists()
    assert c.path.exists()


def test_s3_source_etag_change_invalidates_cache(s3_env) -> None:
    moto = pytest.importorskip("moto")
    from sfmapi.server.sources.s3 import S3Source
    from sfmapi.server.storage.s3_cache import S3Cache

    with moto.mock_aws():
        _seed_bucket("sfm-test2", {"x.jpg": b"\xff\xd8AAAA"})
        src = S3Source(bucket="sfm-test2", prefix="")
        m1 = src.materialize()
        cache = S3Cache()
        first_path = m1[0].abs_path
        assert first_path.is_file()

        # Replace the object → new ETag → new cache entry.
        import boto3

        boto3.client("s3", region_name="us-east-1").put_object(
            Bucket="sfm-test2", Key="x.jpg", Body=b"\xff\xd8BBBBBBBBBB"
        )
        src2 = S3Source(bucket="sfm-test2", prefix="")
        m2 = src2.materialize()
        assert m2[0].abs_path != first_path
        assert m2[0].abs_path.is_file()
        # Total cache size is now both entries.
        assert cache.total_bytes() >= len(b"\xff\xd8AAAA") + len(b"\xff\xd8BBBBBBBBBB")
