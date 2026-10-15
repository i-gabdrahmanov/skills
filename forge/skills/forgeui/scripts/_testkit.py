"""_testkit.py — вспомогательные средства тестов forge-ui (не тест; раннер подбирает только test_*.py).

PNG и JPEG для тестов собираются без внешних библиотек: движок картинки не декодирует (дифф идёт в
браузере), поэтому реальный кодек нужен лишь для файлов, которые читает Chromium (задача 5.1).
"""
from __future__ import annotations

import struct
import zlib
from typing import Callable, Optional, Tuple

Pixel = Tuple[int, int, int, int]


def png_bytes(width: int, height: int, pixel: Optional[Callable[[int, int], Pixel]] = None) -> bytes:
    """Валидный RGBA8 PNG; pixel(x, y) → (r, g, b, a), по умолчанию белый непрозрачный."""
    pixel = pixel or (lambda x, y: (255, 255, 255, 255))
    rows = bytearray()
    for y in range(height):
        rows.append(0)                                  # фильтр None
        for x in range(width):
            rows += bytes(pixel(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def jpeg_header_bytes(width: int, height: int) -> bytes:
    """Заголовок JPEG (SOI, APP0, SOF0, EOI): читается по размерам, но не декодируется как картинка."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + bytes([1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"
