"""Header-only image metadata parsing.

This module intentionally does not decode pixels and does not depend on
Pillow, OpenCV, or NumPy. It is for cheap API-layer metadata only; backend
packages own real image decoding and processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MAX_HEADER_SCAN_BYTES = 2 * 1024 * 1024

_JPEG_SOF_MARKERS = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)
_MEDIA_TYPE_BY_FORMAT = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "webp": "image/webp",
}
_EXTENSION_BY_FORMAT = {
    "jpeg": ".jpg",
    "png": ".png",
    "tiff": ".tif",
    "bmp": ".bmp",
    "webp": ".webp",
}


@dataclass(frozen=True)
class ImageMetadata:
    width: int | None
    height: int | None
    format: str | None
    media_type: str | None


def read_image_metadata(
    data: bytes | bytearray | memoryview,
    *,
    content_type: str | None = None,
) -> ImageMetadata:
    """Return header-derived image metadata.

    Invalid, truncated, or unsupported payloads return unknown dimensions
    instead of raising. ``content_type`` is only a caller hint; magic bytes
    decide the detected format whenever possible.
    """
    payload = bytes(data[:MAX_HEADER_SCAN_BYTES])
    detected = sniff_image_format(payload)
    if detected == "jpeg":
        size = _jpeg_size(payload)
    elif detected == "png":
        size = _png_size(payload)
    elif detected == "tiff":
        size = _tiff_size(payload)
    elif detected == "bmp":
        size = _bmp_size(payload)
    elif detected == "webp":
        size = _webp_size(payload)
    else:
        size = None
    media_type = (
        _MEDIA_TYPE_BY_FORMAT.get(detected) if detected else _known_media_type(content_type)
    )
    return ImageMetadata(
        width=size[0] if size else None,
        height=size[1] if size else None,
        format=detected,
        media_type=media_type,
    )


def sniff_image_format(data: bytes | bytearray | memoryview) -> str | None:
    b = bytes(data[:16])
    if len(b) < 2:
        return None
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if b[:2] == b"BM":
        return "bmp"
    if len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def sniff_image_extension(data: bytes | bytearray | memoryview) -> str | None:
    detected = sniff_image_format(data)
    return _EXTENSION_BY_FORMAT.get(detected) if detected else None


def _known_media_type(content_type: str | None) -> str | None:
    if content_type in _MEDIA_TYPE_BY_FORMAT.values():
        return content_type
    return None


def _valid_size(width: int, height: int) -> tuple[int, int] | None:
    if width <= 0 or height <= 0:
        return None
    return width, height


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return _valid_size(
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    limit = len(data)
    while i + 1 < limit:
        if data[i] != 0xFF:
            i += 1
            continue
        while i < limit and data[i] == 0xFF:
            i += 1
        if i >= limit:
            return None
        marker = data[i]
        i += 1
        if marker in {0x00, 0x01} or 0xD0 <= marker <= 0xD9:
            continue
        if marker == 0xDA:
            return None
        if i + 2 > limit:
            return None
        segment_len = int.from_bytes(data[i : i + 2], "big")
        if segment_len < 2 or i + segment_len > limit:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if segment_len < 7:
                return None
            height = int.from_bytes(data[i + 3 : i + 5], "big")
            width = int.from_bytes(data[i + 5 : i + 7], "big")
            return _valid_size(width, height)
        i += segment_len
    return None


def _tiff_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 8:
        return None
    if data[:4] == b"II*\x00":
        byteorder: Literal["little", "big"] = "little"
    elif data[:4] == b"MM\x00*":
        byteorder = "big"
    else:
        return None
    ifd_offset = int.from_bytes(data[4:8], byteorder)
    if ifd_offset < 8 or ifd_offset + 2 > len(data):
        return None
    tag_count = int.from_bytes(data[ifd_offset : ifd_offset + 2], byteorder)
    tag_count = min(tag_count, 256)
    width: int | None = None
    height: int | None = None
    pos = ifd_offset + 2
    for _ in range(tag_count):
        if pos + 12 > len(data):
            break
        tag = int.from_bytes(data[pos : pos + 2], byteorder)
        field_type = int.from_bytes(data[pos + 2 : pos + 4], byteorder)
        count = int.from_bytes(data[pos + 4 : pos + 8], byteorder)
        value = _tiff_value(data, pos + 8, field_type, count, byteorder)
        if tag == 256:
            width = value
        elif tag == 257:
            height = value
        if width is not None and height is not None:
            return _valid_size(width, height)
        pos += 12
    return None


def _tiff_value(
    data: bytes,
    value_pos: int,
    field_type: int,
    count: int,
    byteorder: Literal["little", "big"],
) -> int | None:
    if count != 1 or value_pos + 4 > len(data):
        return None
    if field_type == 3:
        return int.from_bytes(data[value_pos : value_pos + 2], byteorder)
    if field_type == 4:
        return int.from_bytes(data[value_pos : value_pos + 4], byteorder)
    return None


def _bmp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26 or data[:2] != b"BM":
        return None
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    return _valid_size(abs(width), abs(height))


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    pos = 12
    limit = len(data)
    while pos + 8 <= limit:
        chunk_type = data[pos : pos + 4]
        chunk_size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        chunk_start = pos + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > limit:
            return None
        if chunk_type == b"VP8X":
            return _webp_vp8x_size(data, chunk_start)
        if chunk_type == b"VP8L":
            return _webp_vp8l_size(data, chunk_start)
        if chunk_type == b"VP8 ":
            return _webp_vp8_size(data, chunk_start)
        pos = chunk_end + (chunk_size % 2)
    return None


def _webp_vp8x_size(data: bytes, pos: int) -> tuple[int, int] | None:
    if pos + 10 > len(data):
        return None
    width = int.from_bytes(data[pos + 4 : pos + 7], "little") + 1
    height = int.from_bytes(data[pos + 7 : pos + 10], "little") + 1
    return _valid_size(width, height)


def _webp_vp8l_size(data: bytes, pos: int) -> tuple[int, int] | None:
    if pos + 5 > len(data) or data[pos] != 0x2F:
        return None
    bits = int.from_bytes(data[pos + 1 : pos + 5], "little")
    width = (bits & 0x3FFF) + 1
    height = ((bits >> 14) & 0x3FFF) + 1
    return _valid_size(width, height)


def _webp_vp8_size(data: bytes, pos: int) -> tuple[int, int] | None:
    if pos + 10 > len(data) or data[pos + 3 : pos + 6] != b"\x9d\x01\x2a":
        return None
    width = int.from_bytes(data[pos + 6 : pos + 8], "little") & 0x3FFF
    height = int.from_bytes(data[pos + 8 : pos + 10], "little") & 0x3FFF
    return _valid_size(width, height)
