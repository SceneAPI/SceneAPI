from __future__ import annotations

import struct

import pytest

from app.core.image_metadata import read_image_metadata, sniff_image_extension

pytestmark = pytest.mark.unit


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x04ab"
        + b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )


def _tiff_le(width: int, height: int) -> bytes:
    return (
        b"II*\x00"
        + struct.pack("<I", 8)
        + struct.pack("<H", 2)
        + struct.pack("<HHII", 256, 4, 1, width)
        + struct.pack("<HHII", 257, 4, 1, height)
        + struct.pack("<I", 0)
    )


def _webp_vp8x(width: int, height: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    )
    return (
        b"RIFF"
        + (len(payload) + 12).to_bytes(4, "little")
        + b"WEBPVP8X"
        + (len(payload)).to_bytes(4, "little")
        + payload
    )


def _bmp(width: int, height: int) -> bytes:
    return (
        b"BM"
        + b"\x00" * 16
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + b"\x00" * 8
    )


@pytest.mark.parametrize(
    ("payload", "fmt", "media_type", "extension", "width", "height"),
    [
        (_jpeg(640, 480), "jpeg", "image/jpeg", ".jpg", 640, 480),
        (_png(320, 240), "png", "image/png", ".png", 320, 240),
        (_tiff_le(1024, 768), "tiff", "image/tiff", ".tif", 1024, 768),
        (_webp_vp8x(1920, 1080), "webp", "image/webp", ".webp", 1920, 1080),
        (_bmp(160, 90), "bmp", "image/bmp", ".bmp", 160, 90),
    ],
)
def test_read_image_metadata_from_headers(
    payload: bytes,
    fmt: str,
    media_type: str,
    extension: str,
    width: int,
    height: int,
) -> None:
    meta = read_image_metadata(payload)

    assert meta.format == fmt
    assert meta.media_type == media_type
    assert meta.width == width
    assert meta.height == height
    assert sniff_image_extension(payload) == extension


def test_read_image_metadata_uses_content_type_only_as_unknown_format_hint() -> None:
    meta = read_image_metadata(b"not-an-image", content_type="image/png")

    assert meta.format is None
    assert meta.media_type == "image/png"
    assert meta.width is None
    assert meta.height is None


def test_read_image_metadata_tolerates_truncated_headers() -> None:
    meta = read_image_metadata(b"\xff\xd8\xff\xc0\x00\x11")

    assert meta.format == "jpeg"
    assert meta.width is None
    assert meta.height is None
